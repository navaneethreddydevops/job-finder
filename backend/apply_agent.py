"""Autonomous apply agent (Task 10).

Applies to a stored job on the user's behalf: a Claude agent opens the posting URL in
a headless browser (Playwright MCP as an external stdio MCP server), fills the
employer's application form from the stored Task 9 profile, uploads the stored resume,
answers screening questions from profile data, and submits. It never fabricates and
never guesses legally significant answers — anything it can't answer from the profile
ends the run as ``needs_review`` with a reason and a screenshot.

Runs in its own lane: it does not touch the search agent's ``agent_status`` or the
search tools' run context, so a job pull and an apply can run concurrently. One apply
at a time per user; progress is persisted on the ``applications`` row (``apply_*``
columns) and polled by the frontend — the global SSE stream belongs to ``/api/pull``.

Requires Node (``npx``) + Chromium for Playwright MCP — available locally, not on
FastAPI Cloud. ``apply_agent_available()`` gates the feature (503 when off).
"""

import asyncio
import glob
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone

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
    ToolUseBlock,
    ResultMessage,
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
INPUT_WAIT_SECONDS = 55
INPUT_EXPIRY_SECONDS = 8 * 60
MAX_INPUT_ASKS = 3

router = APIRouter(prefix="/api", tags=["apply-agent"])

# One apply at a time per user. In-memory is correct here: the deployment is pinned
# to a single instance (see fastapi-cloud.yml) and BackgroundTasks share this loop.
_active_users: set[int] = set()
_active_lock = asyncio.Lock()

# Live-run registry: application_id → {user_id, job_id, screenshot_dir, future,
# asked_at}. Powers the human-in-the-loop input hand-off and the live-screenshot
# endpoint; entries exist only while the run's browser session is alive.
_live_runs: dict[int, dict] = {}


def apply_agent_available() -> bool:
    """The agent needs Node's npx to launch Playwright MCP. APPLY_AGENT_ENABLED
    overrides the autodetect in both directions (force-off in prod, force-on when
    npx lives outside PATH-at-import)."""
    override = os.getenv("APPLY_AGENT_ENABLED", "").strip()
    if override in ("0", "false", "no"):
        return False
    if override in ("1", "true", "yes"):
        return True
    return shutil.which("npx") is not None


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
# When the employer's form demands an email verification code (or has a required
# question the profile can't answer), the agent calls await_user_input instead of
# aborting. The handler parks the run in `awaiting_input`, the dashboard shows an
# input box, and POST /api/jobs/{job_id}/apply-agent/input resolves the future —
# the browser session stays alive throughout. Never used for passwords/CAPTCHAs.
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
    "Ask the candidate for a piece of information you cannot get any other way — an "
    "email verification code the form sent to their inbox, or (at most 3 times per "
    "run) a required screening answer missing from the profile. The candidate is "
    "prompted in their dashboard; this call waits up to ~1 minute. If it returns "
    "status='pending', call it again with the SAME reason to keep waiting — the ask "
    "stays open for ~8 minutes total before returning status='expired' (then finish "
    "as needs_review). NEVER use this for account passwords, logins, or CAPTCHAs.",
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

    if run.get("asks", 0) > MAX_INPUT_ASKS:
        _set_awaiting_input(app_id, "", awaiting=False)
        return {"content": [{"type": "text", "text": '{"status": "expired", "message": "Ask limit reached — finish as needs_review."}'}]}

    try:
        value = await asyncio.wait_for(asyncio.shield(run["future"]), timeout=INPUT_WAIT_SECONDS)
        run["consumed"] = True
        run["future"] = None
        _set_awaiting_input(app_id, "", awaiting=False)
        _append_apply_log(app_id, "[input received] resuming the application.")
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
        "6. ASK THE CANDIDATE instead of stopping when the form (a) sends an email "
        "verification code to the candidate's inbox and asks for it, or (b) has a "
        "REQUIRED screening question the profile cannot answer: call "
        f"mcp__userinput__await_user_input with application_id={application_id} and a "
        "specific reason (e.g. 'Enter the 8-character code Greenhouse emailed to "
        "you@example.com'). The candidate sees the reason in their dashboard and "
        "types the answer. While it returns status=pending, keep calling it with the "
        "same reason; on status=ok use the value; on status=expired finish as "
        "needs_review. At most 3 distinct asks per run. NEVER use it for passwords, "
        "logins, or CAPTCHAs — those remain hard stops under rule 5.\n"
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
    application_id: int, profile: dict, job: dict, resume_path: str, screenshot_dir: str
) -> ApplyResult:
    """Logfire-traced wrapper: the Agent SDK spawns the `claude` CLI subprocess, so
    `instrument_anthropic()` can't see this call — trace it with a manual span."""
    with logfire.span(
        "apply_agent claude run",
        application_id=application_id,
        job_url=job.get("url"),
    ):
        return await _run_apply_agent_impl(
            application_id, profile, job, resume_path, screenshot_dir
        )


async def _run_apply_agent_impl(
    application_id: int, profile: dict, job: dict, resume_path: str, screenshot_dir: str
) -> ApplyResult:
    system_prompt, prompt = _build_prompts(application_id, profile, job, resume_path, screenshot_dir)
    options = ClaudeAgentOptions(
        model="claude-sonnet-5",
        # Generous: waiting on human input burns a turn per ~55s pending poll.
        max_turns=100,
        # cwd = the run's tempdir: Playwright MCP resolves bare screenshot filenames
        # against the process cwd, so this keeps stray files out of the repo root.
        cwd=os.path.dirname(screenshot_dir),
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        output_format=ApplyResult.model_json_schema(),
        mcp_servers={
            "playwright": {
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
            },
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
                    elif isinstance(block, ToolUseBlock):
                        if block.name.startswith("mcp__playwright__browser_"):
                            label = f"[browser] {block.name.removeprefix('mcp__playwright__browser_')}"
                        else:
                            label = f"[tool] {block.name}"
                        _append_apply_log(application_id, label)
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


def _ingest_screenshot(application_id: int, screenshot_dir: str, preferred: str = ""):
    """Store the agent's final screenshot (preferred filename, else newest PNG)."""
    # Playwright MCP may nest output under session subdirectories — search recursively.
    candidates = []
    for ext in ("png", "jpg", "jpeg"):
        candidates += glob.glob(os.path.join(screenshot_dir, "**", f"*.{ext}"), recursive=True)
    if not candidates:
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
        return
    if blob:
        upsert_fields = {"apply_screenshot": blob}
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE applications SET apply_screenshot = ? WHERE id = ?",
            (upsert_fields["apply_screenshot"], application_id),
        )
        conn.commit()
        conn.close()


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
        _append_apply_log(application_id, "Starting application run…")

        job = get_job_for_user(job_id, user_id)
        profile = get_user_profile(user_id)
        stored = get_profile_resume(user_id)
        if job is None or stored is None:
            raise RuntimeError("Job or resume disappeared before the run started.")
        blob, filename, _mime, _text = stored
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "resume.pdf"
        # Inside screenshot_dir on purpose: Playwright MCP only allows file_upload
        # from its --output-dir roots, and screenshot_dir is that root.
        resume_path = os.path.join(screenshot_dir, safe_name)
        with open(resume_path, "wb") as f:
            f.write(blob)

        result = await _run_apply_agent_claude(
            application_id, profile, job, resume_path, screenshot_dir
        )

        # Search the whole workdir (recursive): screenshots may land in the
        # --output-dir or, for bare filenames, the agent's cwd (also the workdir).
        _ingest_screenshot(application_id, workdir, result.screenshot_file)
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
    finally:
        _live_runs.pop(application_id, None)
        _active_users.discard(user_id)
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
                "The apply agent is only available in local/self-hosted deployments "
                "(it needs Node + a headless browser). Use the manual apply link."
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

    async with _active_lock:
        if user["id"] in _active_users:
            raise HTTPException(
                status_code=409,
                detail={"message": "An application is already in progress. Please wait."},
            )
        enforce_rate_limit(
            user["id"],
            "/api/jobs/apply-agent",
            limit=RateLimitConfig.APPLY_AGENT,
            window=RateLimitConfig.APPLY_AGENT_WINDOW,
        )
        _active_users.add(user["id"])

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
        # Never leave the user permanently "active" if the run can't start.
        _active_users.discard(user["id"])
        raise
    return {"application_id": application_id, "apply_status": "queued"}


@router.get("/jobs/{job_id}/apply-agent/status")
async def apply_agent_status(job_id: int, user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, apply_status, apply_error, apply_log, apply_input_prompt, "
        "apply_started_at, apply_finished_at FROM applications "
        "WHERE user_id = ? AND job_id = ?",
        (user["id"], job_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return {"apply_status": "", "progress_lines": [], "application_id": None}
    lines = [ln for ln in (row["apply_log"] or "").splitlines() if ln.strip()]
    return {
        "application_id": row["id"],
        "apply_status": row["apply_status"] or "",
        "apply_error": row["apply_error"] or "",
        "apply_input_prompt": row["apply_input_prompt"] or "",
        "progress_lines": lines[-40:],
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
    candidates = []
    for ext in ("png", "jpg", "jpeg"):
        candidates += glob.glob(os.path.join(run["workdir"], "**", f"*.{ext}"), recursive=True)
    if not candidates:
        raise HTTPException(status_code=404, detail="No screenshot yet.")
    path = max(candidates, key=os.path.getmtime)
    with open(path, "rb") as f:
        data = f.read()
    import io

    return StreamingResponse(io.BytesIO(data), media_type="image/png")


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
