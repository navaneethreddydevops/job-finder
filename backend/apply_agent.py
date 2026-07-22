"""Autonomous apply agent (Task 10).

Applies to a stored job on the user's behalf: a Claude agent opens the posting URL in
a headless browser (Playwright MCP as an external stdio MCP server), fills the
employer's application form from the stored Task 9 profile, uploads the stored resume,
answers screening questions from profile data, and submits. It never fabricates and
never guesses legally significant answers — anything it can't answer from the profile
ends the run as ``needs_review`` with a reason and a screenshot.

Runs in its own lane: it does not touch the search agent's ``agent_status`` or the
search tools' run context, so a job pull and an apply can run concurrently. Up to
``APPLY_CONCURRENCY_MAX`` (default 3) applies per user at once, one per job; progress
is persisted on the ``applications`` row (``apply_*`` columns) and polled by the
frontend — the global SSE stream belongs to ``/api/pull``.

Browser transport is dual-mode: with ``PLAYWRIGHT_MCP_URL`` set the agent connects to
the remote Playwright MCP sidecar (``deploy/playwright-mcp/`` — streamable HTTP,
bearer-auth'd via ``PLAYWRIGHT_MCP_TOKEN``; resumes are POSTed to its ``/upload``
first, and screenshots are captured from MCP tool-result image blocks in the message
stream). Without it, the agent spawns ``npx @playwright/mcp`` locally as before.
``apply_agent_available()`` gates the feature (503 when off).
"""

import asyncio
import base64
import glob
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone

import httpx
import logfire
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

# Same OAuth-only rule as the rest of the backend: never authenticate via an API key.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from claude_agent_sdk import (  # noqa: E402
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import (  # noqa: E402
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    ResultMessage,
    UserMessage,
)

from auth import get_current_user  # noqa: E402
from db import (  # noqa: E402
    get_application,
    get_db_connection,
    get_job_for_user,
    get_profile_resume,
    get_user_profile,
    is_profile_apply_ready,
    toggle_applied,
    update_application_status,
    upsert_agent_application,
)
from rate_limit import RateLimitConfig, enforce_rate_limit  # noqa: E402

APPLY_LOG_MAX_CHARS = 50_000
ACTIVE_APPLY_STATUSES = ("queued", "running", "awaiting_input")

# Human-in-the-loop input: each blocking tool call waits this long before returning
# "pending" (the agent re-calls); the whole ask expires after INPUT_EXPIRY_SECONDS.
# MAX_INPUT_ASKS bounds how many distinct things the agent may ask the candidate for
# in one run — raised from 3 so it can collect any non-credential field values the
# profile lacks (screening answers, desired salary, a required URL, etc.) instead of
# bailing to needs_review. Never covers passwords/logins/CAPTCHAs (hard stops).
INPUT_WAIT_SECONDS = 55
INPUT_EXPIRY_SECONDS = 8 * 60
MAX_INPUT_ASKS = 12

router = APIRouter(prefix="/api", tags=["apply-agent"])

# Remote Playwright MCP sidecar (deploy/playwright-mcp/). When PLAYWRIGHT_MCP_URL is
# set the agent talks to it over streamable HTTP instead of spawning npx locally —
# this is what makes auto-apply work on FastAPI Cloud (no Node/Chromium there).
PLAYWRIGHT_MCP_URL = os.getenv("PLAYWRIGHT_MCP_URL", "").strip().rstrip("/")
PLAYWRIGHT_MCP_TOKEN = os.getenv("PLAYWRIGHT_MCP_TOKEN", "").strip()

# Concurrency: up to N applies per user at once (one per job). In-memory is correct
# here: the deployment is pinned to a single instance (see fastapi-cloud.yml) and
# BackgroundTasks share this loop.
APPLY_CONCURRENCY_MAX = max(1, int(os.getenv("APPLY_CONCURRENCY_MAX", "3") or 3))
_active_runs: dict[int, int] = {}  # user_id → number of live apply runs
_active_lock = asyncio.Lock()


def _use_remote_browser() -> bool:
    return bool(PLAYWRIGHT_MCP_URL)


async def _release_run_slot(user_id: int):
    async with _active_lock:
        count = _active_runs.get(user_id, 0) - 1
        if count > 0:
            _active_runs[user_id] = count
        else:
            _active_runs.pop(user_id, None)

# Live-run registry: application_id → {user_id, job_id, screenshot_dir, future,
# asked_at}. Powers the human-in-the-loop input hand-off and the live-screenshot
# endpoint; entries exist only while the run's browser session is alive.
_live_runs: dict[int, dict] = {}


def apply_agent_available() -> bool:
    """The agent needs a browser: either the remote Playwright MCP sidecar
    (PLAYWRIGHT_MCP_URL) or Node's npx to launch Playwright MCP locally.
    APPLY_AGENT_ENABLED overrides the autodetect in both directions (force-off,
    or force-on when npx lives outside PATH-at-import)."""
    override = os.getenv("APPLY_AGENT_ENABLED", "").strip()
    if override in ("0", "false", "no"):
        return False
    if override in ("1", "true", "yes"):
        return True
    return _use_remote_browser() or shutil.which("npx") is not None


def recover_stale_apply_runs():
    """Apply runs live in this process; rows left queued/running/awaiting_input by a
    previous process can never finish — fail them so the UI doesn't poll forever."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ", ".join("?" * len(ACTIVE_APPLY_STATUSES))
    cursor.execute(
        "UPDATE applications SET apply_status = 'failed', "
        "apply_error = 'Server restarted while the application was in progress.', "
        "apply_input_prompt = '', apply_finished_at = ? "
        f"WHERE apply_status IN ({placeholders})",
        (now, *ACTIVE_APPLY_STATUSES),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Human-in-the-loop input (in-process SDK MCP server "userinput")
#
# When the employer's form needs a value the agent can't derive from the profile —
# an email verification code, a required screening answer, desired salary, a required
# URL, etc. — the agent calls await_user_input instead of aborting. The handler parks
# the run in `awaiting_input`, the dashboard shows an input box, and POST
# /api/jobs/{job_id}/apply-agent/input resolves the future — the browser session stays
# alive throughout. Never used for passwords/logins/account-creation/CAPTCHAs (hard stops).
# ---------------------------------------------------------------------------
def _set_awaiting_input(application_id: int, prompt: str, awaiting: bool):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "UPDATE applications SET apply_status = ?, apply_input_prompt = ?, "
        "updated_at = ? WHERE id = ?",
        ("awaiting_input" if awaiting else "running", prompt if awaiting else "", now, application_id),
    )
    conn.commit()
    conn.close()


@tool(
    "await_user_input",
    "Ask the candidate — live, in their dashboard — for a form value you cannot get "
    "from their profile or resume, so you can keep filling the form instead of stopping. "
    "Use it for ANY required or important field the profile doesn't answer: an email "
    "verification code the form sent to their inbox, a screening question, desired "
    "salary/compensation, notice period, a required portfolio/video/LinkedIn URL, a "
    "'how did you hear about us', etc. Put the exact question in `reason`, phrased so a "
    "human can answer in one line (name the field and any options). This call waits up "
    "to ~1 minute; if it returns status='pending', call it again with the SAME reason to "
    "keep waiting — the ask stays open ~8 minutes before returning status='expired' "
    "(then move on: skip the field if optional, else finish as needs_review). You may "
    "ask up to ~12 times per run; batch related fields into one clear question when you "
    "can. HARD LIMIT — NEVER use this for account passwords, logins, account creation, "
    "or CAPTCHAs; those remain immediate needs_review stops (rule 5).",
    {"application_id": int, "reason": str},
)
async def await_user_input(args: dict) -> dict:
    app_id = args.get("application_id")
    reason = (args.get("reason") or "").strip() or "The agent needs your input to continue."
    run = _live_runs.get(app_id)
    if run is None:
        return {"content": [{"type": "text", "text": '{"status": "error", "message": "No live run for this application_id."}'}]}

    loop = asyncio.get_running_loop()
    fut = run.get("future")
    if fut is not None and fut.done() and run.get("consumed"):
        fut = None  # previous ask fully finished — this is a new ask
    if fut is None:
        fut = loop.create_future()
        run["future"] = fut
        run["asked_at"] = loop.time()
        run["consumed"] = False
        run["asks"] = run.get("asks", 0) + 1
        _set_awaiting_input(app_id, reason, awaiting=True)
        _append_apply_log(app_id, f"[input needed] {reason}")
        _append_apply_step(app_id, "input", "Waiting for your input", detail=reason[:160])

    if run.get("asks", 0) > MAX_INPUT_ASKS:
        _set_awaiting_input(app_id, "", awaiting=False)
        return {"content": [{"type": "text", "text": '{"status": "expired", "message": "Ask limit reached — finish as needs_review."}'}]}

    try:
        value = await asyncio.wait_for(asyncio.shield(run["future"]), timeout=INPUT_WAIT_SECONDS)
        run["consumed"] = True
        run["future"] = None
        _set_awaiting_input(app_id, "", awaiting=False)
        _append_apply_log(app_id, "[input received] resuming the application.")
        _append_apply_step(app_id, "input", "Got your input — resuming")
        return {"content": [{"type": "text", "text": json.dumps({"status": "ok", "value": value})}]}
    except asyncio.TimeoutError:
        if loop.time() - run.get("asked_at", loop.time()) > INPUT_EXPIRY_SECONDS:
            run["future"] = None
            _set_awaiting_input(app_id, "", awaiting=False)
            _append_apply_log(app_id, "[input expired] no response from the candidate.")
            return {"content": [{"type": "text", "text": '{"status": "expired", "message": "No input arrived in time — finish as needs_review."}'}]}
        return {"content": [{"type": "text", "text": '{"status": "pending", "message": "No input yet — call await_user_input again with the same reason."}'}]}


user_input_server = create_sdk_mcp_server(
    name="userinput", version="1.0.0", tools=[await_user_input]
)


# ---------------------------------------------------------------------------
# Structured agent output
# ---------------------------------------------------------------------------
class QuestionAnswer(BaseModel):
    question: str
    answer: str


class ApplyResult(BaseModel):
    outcome: str  # submitted | needs_review | failed
    reason: str = ""
    confirmation_text: str = ""
    screenshot_file: str = ""
    questions_answered: list[QuestionAnswer] = []
    blocked_on: str = ""


PLAYWRIGHT_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_file_upload",
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_tabs",
]


def _append_apply_log(application_id: int, line: str):
    """Append one progress line to the row's apply_log, keeping it bounded."""
    line = (line or "").strip()
    if not line:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT apply_log FROM applications WHERE id = ?", (application_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return
    log = (row["apply_log"] or "") + line + "\n"
    if len(log) > APPLY_LOG_MAX_CHARS:
        log = log[-APPLY_LOG_MAX_CHARS:]
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "UPDATE applications SET apply_log = ?, updated_at = ? WHERE id = ?",
        (log, now, application_id),
    )
    conn.commit()
    conn.close()


# ── Structured live steps ───────────────────────────────────────────────────
# The message-stream loop already sees every browser action + the agent's
# reasoning; we turn that into a persisted, human-readable step list that powers
# the dashboard's live "what the agent is doing in the browser" view. Field
# VALUES are never surfaced (PII) — only field names/labels.
APPLY_STEPS_MAX = 60

# The 5 milestones mirror the progress-NN screenshot names the prompt instructs
# the agent to take; the screenshot filename is what advances the stepper.
_MILESTONE_BY_SCREENSHOT = {
    "progress-01": 1,
    "progress-02": 2,
    "progress-03": 3,
    "progress-04": 4,
    "progress-05": 5,
}
_MILESTONE_TITLES = {
    1: "Reached the application form",
    2: "Filled in your details",
    3: "Uploaded your resume",
    4: "Ready to submit the application",
    5: "Reached the confirmation page",
}


def _hostname(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or url
    except Exception:  # noqa: BLE001
        return url


def _reset_apply_steps(application_id: int):
    """Clear any steps left over from a previous run on the same application row."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE applications SET apply_steps = ? WHERE id = ?", ("", application_id)
    )
    conn.commit()
    conn.close()


def _append_apply_step(
    application_id: int,
    kind: str,
    title: str,
    *,
    detail: str = "",
    url: str = "",
    milestone: int | None = None,
):
    """Append one structured step to the row's apply_steps JSON array, bounded and
    with a monotonic milestone. Mirrors _append_apply_log (DB is the source of truth)."""
    title = (title or "").strip()
    if not title:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT apply_steps FROM applications WHERE id = ?", (application_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return
    try:
        steps = json.loads(row["apply_steps"]) if row["apply_steps"] else []
        if not isinstance(steps, list):
            steps = []
    except Exception:  # noqa: BLE001
        steps = []
    prev_ms = max((s.get("milestone", 0) for s in steps), default=0)
    cur_ms = max(prev_ms, milestone or 0)
    now = datetime.now(timezone.utc).isoformat()
    steps.append(
        {
            "kind": kind,
            "title": title[:200],
            "detail": (detail or "")[:200],
            "url": url or "",
            "milestone": cur_ms,
            "ts": now,
        }
    )
    steps = steps[-APPLY_STEPS_MAX:]
    # Keep the live registry's milestone in sync for any in-memory consumer.
    run = _live_runs.get(application_id)
    if run is not None:
        run["milestone"] = cur_ms
    cursor.execute(
        "UPDATE applications SET apply_steps = ?, updated_at = ? WHERE id = ?",
        (json.dumps(steps), now, application_id),
    )
    conn.commit()
    conn.close()


def _describe_tool_use(block: ToolUseBlock) -> dict | None:
    """Map a Playwright tool call (name + args) to a friendly step, or None to skip
    (page reads / noise). Returns kwargs for _append_apply_step."""
    name = block.name
    if not name.startswith("mcp__playwright__browser_"):
        return None
    inp = block.input if isinstance(getattr(block, "input", None), dict) else {}
    action = name.removeprefix("mcp__playwright__browser_")

    if action == "navigate":
        url = str(inp.get("url") or "")
        host = _hostname(url)
        return {"kind": "navigate", "title": f"Opening {host}" if host else "Opening a page", "url": url}
    if action == "navigate_back":
        return {"kind": "navigate", "title": "Going back to the previous page"}
    if action == "click":
        el = str(inp.get("element") or "").strip() or "a button"
        return {"kind": "click", "title": f"Clicking “{el}”"}
    if action == "type":
        el = str(inp.get("element") or "").strip() or "a field"
        return {"kind": "fill", "title": f"Typing into “{el}”"}
    if action == "fill_form":
        names = []
        fields = inp.get("fields")
        if isinstance(fields, list):
            for f in fields:
                if isinstance(f, dict):
                    nm = f.get("name") or f.get("element") or f.get("ref")
                    if nm:
                        names.append(str(nm))
        if names:
            shown = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
            return {"kind": "fill", "title": "Filling in the form", "detail": shown}
        return {"kind": "fill", "title": "Filling in the form"}
    if action == "select_option":
        el = str(inp.get("element") or "").strip() or "an option"
        return {"kind": "select", "title": f"Choosing an option for “{el}”"}
    if action == "file_upload":
        return {"kind": "upload", "title": "Uploading your resume"}
    if action == "wait_for":
        return {"kind": "wait", "title": "Waiting for the page to update"}
    if action == "press_key":
        key = str(inp.get("key") or "").strip()
        return {"kind": "click", "title": f"Pressing {key}" if key else "Pressing a key"}
    if action == "take_screenshot":
        fname = str(inp.get("filename") or "")
        for prefix, ms in _MILESTONE_BY_SCREENSHOT.items():
            if prefix in fname:
                return {"kind": "milestone", "title": _MILESTONE_TITLES[ms], "milestone": ms}
        return None  # non-milestone screenshot — skip the noise
    # snapshot / tabs / anything else: internal, not user-facing
    return None


def _build_prompts(application_id: int, profile: dict, job: dict, resume_path: str, screenshot_dir: str):
    """System + task prompts for the apply run. The profile is the ONLY source of
    truth for answers; legally significant questions must never be guessed."""
    profile_for_prompt = {k: v for k, v in profile.items() if k not in (
        "resume_text", "onboarding_completed", "onboarding_step", "has_resume",
    )}
    resume_text = (profile.get("resume_text") or "")[:4000]

    system_prompt = (
        "You are an autonomous job-application assistant operating a headless browser "
        "via Playwright tools. You fill out and submit ONE employer job-application "
        "form on behalf of the candidate, using ONLY the candidate profile provided.\n\n"
        "HARD RULES — these override everything else:\n"
        "1. NEVER fabricate information. Every answer must come from the candidate "
        "profile (or the resume text) verbatim or by direct derivation. Leave optional "
        "fields blank when the profile has no answer.\n"
        "2. NEVER guess legally significant answers: work authorization, visa/"
        "citizenship status, sponsorship needs, EEO self-identification (gender, race/"
        "ethnicity, veteran status, disability), criminal history, security clearance, "
        "age/date of birth. Use the profile's exact values. If a REQUIRED field of "
        "this kind has no profile answer, STOP and finish with outcome=needs_review.\n"
        "3. For EEO/self-identification sections, select the profile's stored values "
        "(they default to 'Decline to self-identify').\n"
        "4. Upload the resume file when the form has a resume/CV upload field, using "
        f"browser_file_upload with this exact path: {resume_path}\n"
        "5. STOP with outcome=needs_review (do NOT attempt workarounds) when you hit: "
        "a login or account-creation wall; a CAPTCHA or bot check; a payment request; "
        "or the posting is closed/removed/404. Never create accounts, never enter "
        "passwords, never try to defeat bot detection.\n"
        "6. ASK THE CANDIDATE instead of stopping whenever the form needs a value you "
        "cannot get from the profile or resume — do NOT fabricate it and do NOT bail to "
        "needs_review while a required field is merely unknown. This covers: (a) an email "
        "verification code sent to the candidate's inbox; (b) any REQUIRED screening "
        "question the profile can't answer; and (c) any other required-or-important field "
        "with no profile value — desired salary/compensation, notice period / start date, "
        "a required portfolio/video/LinkedIn/GitHub URL, 'how did you hear about us', "
        "years with a specific tool, etc. Call "
        f"mcp__userinput__await_user_input with application_id={application_id} and a "
        "specific one-line `reason` that names the field and lists any choices (e.g. "
        "'Desired annual salary in USD?' or 'Enter the 8-character code Greenhouse "
        "emailed to you@example.com' or 'Are you willing to relocate? yes/no'). The "
        "candidate answers it live in their dashboard. While it returns status=pending, "
        "keep calling with the SAME reason; on status=ok use the value; on "
        "status=expired, skip the field if it is optional, otherwise finish as "
        "needs_review. Ask for one field (or a small batch of related fields) per call, "
        "up to ~12 distinct asks per run. NEVER use it for passwords, logins, "
        "account creation, or CAPTCHAs — those remain hard stops under rule 5.\n"
        "7. LIVE PROGRESS: after each major milestone — application form reached, "
        "form fields filled, resume uploaded, just before submitting, and the "
        "confirmation page — take a screenshot named progress-01-loaded.png, "
        "progress-02-filled.png, progress-03-resume.png, progress-04-presubmit.png, "
        "progress-05-confirmation.png (browser_take_screenshot, type='png'). These "
        "stream live to the candidate's dashboard while you work.\n"
        "8. Before finishing — for EVERY outcome — take a final screenshot of the "
        "page state and remember its filename; screenshots are saved under "
        f"{screenshot_dir}.\n"
        "9. After filling all fields on the final step, SUBMIT the application, wait "
        "for the confirmation page/message, screenshot it, and finish with "
        "outcome=submitted and the confirmation text.\n\n"
        "Navigation notes: job boards often link out — if the posting page has an "
        "Apply button, follow it (one click-through) to reach the employer's form. "
        "ATS pages (Greenhouse, Lever, Ashby, Workday) usually embed the form "
        "directly. Use browser_snapshot to read the page and browser_fill_form to "
        "fill multiple fields at once. Work through multi-step forms step by step.\n\n"
        "Your final structured output must match the provided schema: outcome is one "
        "of submitted|needs_review|failed; reason explains any non-submitted outcome; "
        "blocked_on names the blocker (login|captcha|verification|question|closed|"
        "other) when needs_review; questions_answered lists every screening question "
        "you answered and the answer used; screenshot_file is the filename of your "
        "final screenshot.\n"
        "Your FINAL message MUST be ONLY that JSON object (no prose, no code fences):\n"
        f"{json.dumps(ApplyResult.model_json_schema())}"
    )
    prompt = (
        f"CANDIDATE PROFILE (single source of truth):\n"
        f"{json.dumps(profile_for_prompt, indent=2)}\n\n"
        f"RESUME FILE (upload this when asked): {resume_path}\n"
        f"RESUME TEXT (for answering experience/skills questions):\n{resume_text}\n\n"
        f"JOB TO APPLY TO:\n"
        f"- Title: {job.get('title')}\n"
        f"- Company: {job.get('company')}\n"
        f"- URL: {job.get('url')}\n\n"
        "Open the job URL, reach the application form, fill it from the profile, "
        "upload the resume, answer screening questions, submit, and report the result."
    )
    return system_prompt, prompt


async def _run_apply_agent_claude(
    application_id: int, profile: dict, job: dict, resume_path: str, screenshot_dir: str, workdir: str
) -> ApplyResult:
    """Logfire-traced wrapper: the Agent SDK spawns the `claude` CLI subprocess, so
    `instrument_anthropic()` can't see this call — trace it with a manual span."""
    with logfire.span(
        "apply_agent claude run",
        application_id=application_id,
        job_url=job.get("url"),
        remote_browser=_use_remote_browser(),
    ):
        return await _run_apply_agent_impl(
            application_id, profile, job, resume_path, screenshot_dir, workdir
        )


def _playwright_server_config(screenshot_dir: str) -> dict:
    """Remote HTTP sidecar when PLAYWRIGHT_MCP_URL is set, local npx stdio otherwise.
    Tool names (mcp__playwright__browser_*) are identical in both modes."""
    if _use_remote_browser():
        config = {"type": "http", "url": f"{PLAYWRIGHT_MCP_URL}/mcp"}
        if PLAYWRIGHT_MCP_TOKEN:
            config["headers"] = {"Authorization": f"Bearer {PLAYWRIGHT_MCP_TOKEN}"}
        return config
    return {
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@playwright/mcp@latest",
            "--headless",
            "--isolated",
            "--output-dir",
            screenshot_dir,
        ],
    }


def _capture_tool_result_images(application_id: int, block: ToolResultBlock):
    """Screenshots come back as base64 image blocks inside MCP tool results — the
    only screenshot channel that works for the remote sidecar (its filesystem is
    not ours). Latest one powers the live view; the last one becomes the stored
    final screenshot."""
    if not isinstance(block.content, list):
        return
    for item in block.content:
        if not (isinstance(item, dict) and item.get("type") == "image"):
            continue
        data = (item.get("source") or {}).get("data")
        if not data:
            continue
        try:
            shot = base64.b64decode(data)
        except Exception:  # noqa: BLE001
            continue
        run = _live_runs.get(application_id)
        if run is not None and shot:
            run["live_shot"] = shot


async def _run_apply_agent_impl(
    application_id: int, profile: dict, job: dict, resume_path: str, screenshot_dir: str, workdir: str
) -> ApplyResult:
    system_prompt, prompt = _build_prompts(application_id, profile, job, resume_path, screenshot_dir)
    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        # Generous: waiting on human input burns a turn per ~55s pending poll.
        max_turns=100,
        # cwd = the run's local tempdir: in local mode Playwright MCP resolves bare
        # screenshot filenames against the process cwd, so this keeps stray files
        # out of the repo root. (Always local — never the sidecar's remote dir.)
        cwd=workdir,
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        output_format=ApplyResult.model_json_schema(),
        mcp_servers={
            "playwright": _playwright_server_config(screenshot_dir),
            "userinput": user_input_server,
        },
        allowed_tools=PLAYWRIGHT_TOOLS + ["mcp__userinput__await_user_input"],
    )

    result_text = ""
    structured = None
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
                        first_line = block.text.strip().splitlines()[0] if block.text.strip() else ""
                        if first_line:
                            _append_apply_log(application_id, first_line[:200])
                            _append_apply_step(application_id, "thought", first_line[:160])
                    elif isinstance(block, ToolUseBlock):
                        if block.name.startswith("mcp__playwright__browser_"):
                            label = f"[browser] {block.name.removeprefix('mcp__playwright__browser_')}"
                        else:
                            label = f"[tool] {block.name}"
                        _append_apply_log(application_id, label)
                        desc = _describe_tool_use(block)
                        if desc:
                            _append_apply_step(application_id, **desc)
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        _capture_tool_result_images(application_id, block)
            elif isinstance(msg, ResultMessage):
                logfire.info(
                    "apply agent result",
                    is_error=msg.is_error,
                    num_turns=getattr(msg, "num_turns", None),
                    duration_ms=getattr(msg, "duration_ms", None),
                    total_cost_usd=getattr(msg, "total_cost_usd", None),
                    usage=getattr(msg, "usage", None),
                )
                if msg.is_error:
                    raise RuntimeError(msg.errors or msg.result or "Claude returned an error")
                if msg.structured_output:
                    structured = msg.structured_output
                elif msg.result and not result_text:
                    result_text = msg.result
                break

    if structured:
        return ApplyResult(**structured)
    # Tolerant fallback: pull the first JSON object out of the final text.
    match = re.search(r"\{.*\}", result_text, re.DOTALL)
    if match:
        try:
            return ApplyResult(**json.loads(match.group(0)))
        except Exception:
            pass
    raise RuntimeError("Apply agent returned no parseable result.")


def _store_screenshot_blob(application_id: int, blob: bytes):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE applications SET apply_screenshot = ? WHERE id = ?",
        (blob, application_id),
    )
    conn.commit()
    conn.close()


def _ingest_screenshot(
    application_id: int, screenshot_dir: str, preferred: str = "", fallback_blob: bytes | None = None
):
    """Store the agent's final screenshot: local file (preferred filename, else
    newest PNG), falling back to the last image captured from the MCP tool-result
    stream — the only source in remote-sidecar mode, where screenshots are written
    to the sidecar's filesystem, not ours."""
    # Playwright MCP may nest output under session subdirectories — search recursively.
    candidates = []
    for ext in ("png", "jpg", "jpeg"):
        candidates += glob.glob(os.path.join(screenshot_dir, "**", f"*.{ext}"), recursive=True)
    if not candidates:
        if fallback_blob:
            _store_screenshot_blob(application_id, fallback_blob)
            return
        try:
            listing = os.listdir(screenshot_dir)
        except OSError:
            listing = []
        print(f"[apply_agent] no screenshot found in {screenshot_dir}; contents: {listing}", flush=True)
        return
    path = None
    if preferred:
        base = os.path.basename(preferred)
        path = next((c for c in candidates if os.path.basename(c) == base), None)
    if path is None:
        path = max(candidates, key=os.path.getmtime)
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        blob = b""
    if not blob:
        blob = fallback_blob or b""
    if blob:
        _store_screenshot_blob(application_id, blob)


async def _upload_resume_to_sidecar(blob: bytes, filename: str) -> tuple[str, str]:
    """POST the resume to the sidecar's /upload so browser_file_upload can reach it
    (Playwright MCP only allows uploads from under its --output-dir). Returns the
    remote (path, dir)."""
    headers = {"x-filename": filename, "content-type": "application/octet-stream"}
    if PLAYWRIGHT_MCP_TOKEN:
        headers["Authorization"] = f"Bearer {PLAYWRIGHT_MCP_TOKEN}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{PLAYWRIGHT_MCP_URL}/upload", content=blob, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["path"], data["dir"]


async def run_apply_agent(user_id: int, job_id: int, application_id: int):
    """Background task: drive the apply run end-to-end and persist every state
    transition so polling clients (and a restarted server) see the truth."""
    workdir = tempfile.mkdtemp(prefix="apply_agent_")
    screenshot_dir = os.path.join(workdir, "shots")
    os.makedirs(screenshot_dir, exist_ok=True)
    _live_runs[application_id] = {
        "user_id": user_id,
        "job_id": job_id,
        "workdir": workdir,
        "future": None,
        "consumed": False,
        "asks": 0,
    }
    try:
        now = datetime.now(timezone.utc).isoformat()
        upsert_agent_application(
            user_id, job_id, apply_status="running", apply_started_at=now
        )
        _reset_apply_steps(application_id)
        _append_apply_log(application_id, "Starting application run…")
        _append_apply_step(application_id, "start", "Starting the application…")

        job = get_job_for_user(job_id, user_id)
        profile = get_user_profile(user_id)
        stored = get_profile_resume(user_id)
        if job is None or stored is None:
            raise RuntimeError("Job or resume disappeared before the run started.")
        blob, filename, _mime, _text = stored
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "resume.pdf"
        if _use_remote_browser():
            # The browser runs on the sidecar host: put the resume on ITS disk and
            # reference the remote paths in the prompts.
            resume_path, prompt_shots_dir = await _upload_resume_to_sidecar(blob, safe_name)
        else:
            # Inside screenshot_dir on purpose: Playwright MCP only allows file_upload
            # from its --output-dir roots, and screenshot_dir is that root.
            resume_path = os.path.join(screenshot_dir, safe_name)
            with open(resume_path, "wb") as f:
                f.write(blob)
            prompt_shots_dir = screenshot_dir

        result = await _run_apply_agent_claude(
            application_id, profile, job, resume_path, prompt_shots_dir, workdir
        )

        # Search the whole workdir (recursive): screenshots may land in the
        # --output-dir or, for bare filenames, the agent's cwd (also the workdir).
        # In remote mode nothing lands locally — the tool-result capture is the source.
        captured = (_live_runs.get(application_id) or {}).get("live_shot")
        _ingest_screenshot(application_id, workdir, result.screenshot_file, fallback_blob=captured)
        finished = datetime.now(timezone.utc).isoformat()
        if result.outcome == "submitted":
            upsert_agent_application(
                user_id,
                job_id,
                apply_status="submitted",
                apply_error="",
                apply_finished_at=finished,
                applied_at=finished,
            )
            update_application_status(application_id, "applied", notes="Submitted by apply agent")
            if not job.get("applied"):
                toggle_applied(user_id, job_id, True)
            _append_apply_log(
                application_id,
                f"Submitted. {result.confirmation_text}".strip(),
            )
            _append_apply_step(
                application_id,
                "done",
                "Application submitted",
                detail=(result.confirmation_text or "")[:160],
                milestone=5,
            )
        else:
            status = "needs_review" if result.outcome == "needs_review" else "failed"
            reason = result.reason or result.blocked_on or "The agent could not finish."
            upsert_agent_application(
                user_id,
                job_id,
                apply_status=status,
                apply_error=reason,
                apply_finished_at=finished,
            )
            _append_apply_log(application_id, f"Stopped ({status}): {reason}")
            _append_apply_step(
                application_id,
                "stopped",
                "Needs your review" if status == "needs_review" else "Could not finish",
                detail=reason[:160],
            )
    except Exception as e:  # noqa: BLE001
        finished = datetime.now(timezone.utc).isoformat()
        upsert_agent_application(
            user_id,
            job_id,
            apply_status="failed",
            apply_error=str(e)[:1000],
            apply_finished_at=finished,
        )
        _append_apply_log(application_id, f"Error: {e}")
        _append_apply_step(application_id, "stopped", "The run hit an error", detail=str(e)[:160])
    finally:
        _live_runs.pop(application_id, None)
        await _release_run_slot(user_id)
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/apply-agent")
async def start_apply_agent(
    job_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    job = get_job_for_user(job_id, user["id"])
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not apply_agent_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "The apply agent's browser service is not configured on this "
                "deployment. Use the manual apply link."
            ),
        )

    profile = get_user_profile(user["id"])
    ready, missing = is_profile_apply_ready(profile)
    if not ready:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Complete your profile before auto-applying.",
                "missing_fields": missing,
            },
        )

    # One run per job: never start a second agent on an application that's live.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT apply_status FROM applications WHERE user_id = ? AND job_id = ?",
        (user["id"], job_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is not None and (row["apply_status"] or "") in ACTIVE_APPLY_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"message": "An application for this job is already in progress."},
        )

    async with _active_lock:
        if _active_runs.get(user["id"], 0) >= APPLY_CONCURRENCY_MAX:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        f"You already have {APPLY_CONCURRENCY_MAX} applications in "
                        "progress — wait for one to finish before starting another."
                    )
                },
            )
        enforce_rate_limit(
            user["id"],
            "/api/jobs/apply-agent",
            limit=RateLimitConfig.APPLY_AGENT,
            window=RateLimitConfig.APPLY_AGENT_WINDOW,
        )
        _active_runs[user["id"]] = _active_runs.get(user["id"], 0) + 1

    try:
        application_id = upsert_agent_application(
            user["id"],
            job_id,
            apply_method="agent",
            apply_status="queued",
            apply_error="",
            apply_log="",
            apply_started_at=None,
            apply_finished_at=None,
        )
        background_tasks.add_task(run_apply_agent, user["id"], job_id, application_id)
    except Exception:
        # Never leave the slot permanently occupied if the run can't start.
        await _release_run_slot(user["id"])
        raise
    return {"application_id": application_id, "apply_status": "queued"}


@router.get("/jobs/{job_id}/apply-agent/status")
async def apply_agent_status(job_id: int, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, apply_status, apply_error, apply_log, apply_input_prompt, "
        "apply_steps, apply_started_at, apply_finished_at FROM applications "
        "WHERE user_id = ? AND job_id = ?",
        (user["id"], job_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return {"apply_status": "", "progress_lines": [], "steps": [], "milestone": 0, "application_id": None}
    lines = [ln for ln in (row["apply_log"] or "").splitlines() if ln.strip()]
    try:
        steps = json.loads(row["apply_steps"]) if row["apply_steps"] else []
        if not isinstance(steps, list):
            steps = []
    except Exception:  # noqa: BLE001
        steps = []
    milestone = max((s.get("milestone", 0) for s in steps), default=0)
    return {
        "application_id": row["id"],
        "apply_status": row["apply_status"] or "",
        "apply_error": row["apply_error"] or "",
        "apply_input_prompt": row["apply_input_prompt"] or "",
        "progress_lines": lines[-40:],
        "steps": steps[-40:],
        "milestone": milestone,
        "started_at": row["apply_started_at"],
        "finished_at": row["apply_finished_at"],
        "has_live_screenshot": row["id"] in _live_runs,
    }


class ApplyInputRequest(BaseModel):
    value: str


@router.post("/jobs/{job_id}/apply-agent/input")
async def submit_apply_input(
    job_id: int, req: ApplyInputRequest, user: dict = Depends(get_current_user)
):
    """Hand the paused agent the verification code / answer it asked for."""
    value = req.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Input value is required.")
    run = next(
        (r for r in _live_runs.values() if r["user_id"] == user["id"] and r["job_id"] == job_id),
        None,
    )
    if run is None or run.get("future") is None or run["future"].done():
        raise HTTPException(
            status_code=409, detail="The agent is not waiting for input right now."
        )
    run["future"].set_result(value)
    return {"success": True}


@router.get("/jobs/{job_id}/apply-agent/live-screenshot")
async def live_screenshot(job_id: int, user: dict = Depends(get_current_user)):
    """Newest milestone screenshot of the in-flight run (404 when no run is live)."""
    run = next(
        (r for r in _live_runs.values() if r["user_id"] == user["id"] and r["job_id"] == job_id),
        None,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="No live apply run for this job.")
    import io

    # Stream-captured shot first (the only source in remote-sidecar mode) …
    if run.get("live_shot"):
        return StreamingResponse(io.BytesIO(run["live_shot"]), media_type="image/png")
    # … then the local tempdir (local npx mode writes milestone PNGs there).
    candidates = []
    for ext in ("png", "jpg", "jpeg"):
        candidates += glob.glob(os.path.join(run["workdir"], "**", f"*.{ext}"), recursive=True)
    if not candidates:
        raise HTTPException(status_code=404, detail="No screenshot yet.")
    path = max(candidates, key=os.path.getmtime)
    with open(path, "rb") as f:
        data = f.read()
    return StreamingResponse(io.BytesIO(data), media_type="image/png")


@router.get("/jobs/{job_id}/apply-agent/live-stream")
async def live_stream(job_id: int, user: dict = Depends(get_current_user)):
    """True live MJPEG stream of the headless browser (remote sidecar only). Relays
    the sidecar's CDP screencast so the sidecar bearer token stays server-side; the
    frontend fetches this with its own auth header. 404 in local mode / when no run is
    live, so the frontend falls back to the milestone-screenshot view."""
    run = next(
        (r for r in _live_runs.values() if r["user_id"] == user["id"] and r["job_id"] == job_id),
        None,
    )
    if run is None or not _use_remote_browser():
        raise HTTPException(status_code=404, detail="No live browser stream for this job.")

    # Match the run to the right browser target by the job's host.
    job = get_job_for_user(job_id, user["id"])
    match = ""
    if job and job.get("url"):
        host = _hostname(job["url"])
        match = host if host and "://" not in host else ""

    headers = {}
    if PLAYWRIGHT_MCP_TOKEN:
        headers["Authorization"] = f"Bearer {PLAYWRIGHT_MCP_TOKEN}"
    params = {"match": match} if match else {}

    # Open the upstream stream and inspect the status BEFORE returning, so a sidecar
    # that doesn't expose /screencast (e.g. an older deployment) surfaces as a clean
    # 404 → the frontend falls back to the milestone-screenshot view instead of
    # reconnect-looping on an empty 200. read=None: the screencast is long-lived; the
    # managed host caps the request duration and the frontend reconnects (like the SSE log).
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    stream_cm = client.stream(
        "GET", f"{PLAYWRIGHT_MCP_URL}/screencast", params=params, headers=headers
    )
    try:
        resp = await stream_cm.__aenter__()
    except Exception:  # noqa: BLE001 — sidecar unreachable
        await client.aclose()
        raise HTTPException(status_code=404, detail="Live browser stream unavailable.")
    if resp.status_code != 200 or "multipart" not in resp.headers.get("content-type", ""):
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=404, detail="Live browser stream unavailable.")

    async def relay():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        except Exception:  # noqa: BLE001 — upstream drop / client disconnect ends the stream
            pass
        finally:
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()

    return StreamingResponse(
        relay(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/applications/{application_id}/screenshot")
async def apply_screenshot(application_id: int, user: dict = Depends(get_current_user)):
    app = get_application(application_id)
    if not app or app["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Application not found")
    blob = app.get("apply_screenshot")
    if not blob:
        raise HTTPException(status_code=404, detail="No screenshot available")
    import io

    return StreamingResponse(io.BytesIO(bytes(blob)), media_type="image/png")


# ---------------------------------------------------------------------------
# Dev-only mock careers form (APPLY_AGENT_MOCK=1) for smoke-testing the real
# agent without flaky external ATS sites. Variants simulate the stop conditions.
# ---------------------------------------------------------------------------
if os.getenv("APPLY_AGENT_MOCK", "").strip() in ("1", "true", "yes"):

    @router.get("/dev/mock-application", response_class=HTMLResponse)
    async def mock_application(mode: str = ""):
        if mode == "login":
            return HTMLResponse(
                "<h1>Sign in to apply</h1><form><label>Email <input name='email'></label>"
                "<label>Password <input type='password' name='password'></label>"
                "<button>Sign in</button></form>"
            )
        if mode == "captcha":
            return HTMLResponse(
                "<h1>Apply — Security check</h1><p>Please verify you are human.</p>"
                "<div style='border:1px solid #888;padding:20px;width:300px'>"
                "<label><input type='checkbox'> I'm not a robot (CAPTCHA)</label></div>"
            )
        weird = (
            "<label>What is your favorite prime number? (required) "
            "<input required name='prime'></label>"
            if mode == "weird_question"
            else ""
        )
        # mode=verify: submit leads to an email-verification step (code: TEST1234)
        # exercising the human-in-the-loop await_user_input path end to end.
        verify_js = "true" if mode == "verify" else "false"
        script = (
            """
            <script>
            var NEEDS_VERIFY = """ + verify_js + """;
            function handleSubmit(e) {
              e.preventDefault();
              if (!NEEDS_VERIFY) { return done('#12345'); }
              document.body.innerHTML =
                '<h1>Verify your email</h1>' +
                '<p>We sent an 8-character verification code to your email address. ' +
                'Enter it below to submit your application.</p>' +
                '<input id="code" placeholder="8-character code"> ' +
                '<button onclick="checkCode()">Verify &amp; Submit</button>';
              return false;
            }
            function checkCode() {
              if (document.getElementById('code').value.trim() === 'TEST1234') done('#67890');
              else document.body.innerHTML = '<h1>Invalid code</h1><p>The code does not match.</p>';
            }
            function done(conf) {
              document.body.innerHTML = '<h1>Application received</h1>' +
                '<p>Confirmation ' + conf + '. Thank you for applying!</p>';
              return false;
            }
            </script>
            """
        )
        return HTMLResponse(
            script
            + f"""
            <h1>Apply for Principal DevOps Engineer — MockCorp</h1>
            <form onsubmit="return handleSubmit(event)">
              <label>Full name <input required name="name"></label><br>
              <label>Email <input required type="email" name="email"></label><br>
              <label>Phone <input required name="phone"></label><br>
              <label>City <input name="city"></label><br>
              <label>LinkedIn <input name="linkedin"></label><br>
              <label>Resume <input required type="file" name="resume"></label><br>
              <label>Are you authorized to work in the US?
                <select required name="authorized"><option value="">Select…</option>
                <option>Yes</option><option>No</option></select></label><br>
              <label>Will you require sponsorship?
                <select required name="sponsorship"><option value="">Select…</option>
                <option>Yes</option><option>No</option></select></label><br>
              <label>Years of experience <input name="years"></label><br>
              {weird}
              <fieldset><legend>Voluntary self-identification (optional)</legend>
                <label>Gender <select name="gender"><option>Decline to self-identify</option>
                  <option>Male</option><option>Female</option><option>Non-binary</option></select></label>
                <label>Veteran status <select name="veteran"><option>Decline to self-identify</option>
                  <option>I am a veteran</option><option>I am not a veteran</option></select></label>
              </fieldset>
              <button type="submit">Submit application</button>
            </form>
            """
        )
