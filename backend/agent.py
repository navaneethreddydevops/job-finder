import os
import json
import re
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    TextBlock,
    AssistantMessage,
    UserMessage,
    ResultMessage,
)

# Exa + Tavily search tools (in-process SDK MCP server). The scouts call these to fetch
# job listings with far better recall than the built-in WebSearch.
from search_tools import (
    job_search_server,
    JOB_SEARCH_SERVER_NAME,
    EXA_TOOL,
    TAVILY_TOOL,
)

# Load environment variables
load_dotenv()

# The claude-agent-sdk spawns the `claude` CLI, which must authenticate via the
# stored Claude OAuth credentials (~/.claude). An ANTHROPIC_API_KEY in the env
# forces API-key auth instead, which we never want here. Always drop it (and the
# legacy ANTHROPIC_AUTH_TOKEN) so the CLI uses its own OAuth login.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


# Essential tools for the orchestrator (see the Claude Agent SDK overview:
# https://code.claude.com/docs/en/agent-sdk/overview). Job discovery is done primarily via
# the Exa + Tavily search tools (in-process SDK MCP server `jobsearch`), with WebFetch
# as fallback for verifying individual listings.
AGENT_ALLOWED_TOOLS = [
    # Job search APIs (Exa + Tavily, via the in-process `jobsearch` MCP server)
    EXA_TOOL,
    TAVILY_TOOL,
    # Web operations (fallback verification only)
    "WebFetch",
    # Agent control
    "Task",  # spawns the job_scout subagent (fan-out)
]

# Tools granted to the job_scout subagent. NOTE: in-process SDK MCP tools (exa/tavily)
# CANNOT be granted to subagents — `AgentDefinition.mcpServers` is JSON-serialized for the
# CLI and a live in-process server isn't serializable. Scouts format JSON only; they don't
# need file I/O or system operations.
SCOUT_ALLOWED_TOOLS = [
    # Web operations — fallback verification if needed
    "WebFetch",
    "WebSearch",
]


# Fallback roles, used ONLY when the user submits an empty Search Target. When the user
# types a query in Agent Controls, that query is the ONLY role searched — these defaults
# are never added on top of it.
DEFAULT_ROLES = [
    "Principal DevOps Engineer",
    "Principal Cloud Engineer",
    "Principal Kubernetes Engineer",
    "Principal Site Reliability Engineer",
]

# The only sources the agent searches. LinkedIn + the ATS-hosted company careers portals
# (Workday, Greenhouse, Lever, Ashby) — all direct employer postings with reliable dates.
# Aggregator boards (Indeed, Glassdoor, Dice, Monster, ZipRecruiter) stay banned: stale
# reposts, scrape-hostile, unreliable dates.
SEARCH_SOURCES = ["LinkedIn", "Workday", "Greenhouse", "Lever", "Ashby"]


# Pydantic Schemas for Structured Output
class JobItem(BaseModel):
    title: str = Field(description="The job title")
    company: str = Field(description="The company name")
    location: str = Field(
        description="The location — should be 'Remote' (only remote roles are collected)"
    )
    url: str = Field(description="The direct job posting link or source URL")
    date_posted: str = Field(
        description="Date posted or found, e.g. '2 hours ago', 'today', '3 days ago'"
    )
    posted_within_7d: bool = Field(
        description="Whether the job is within the search window. You already searched each source with a last-N-days filter, so default to True. Set False ONLY when the posting date is clearly OLDER than the window. If the exact date is unknown but the source's freshness filter was applied, keep True — do not set False just because a date couldn't be parsed."
    )
    key_requirements: list[str] = Field(
        description="List of key requirements/skills mentioned"
    )
    contact_email: str | None = Field(
        None, description="Recruiter or contact email address if available"
    )
    contact_phone: str | None = Field(
        None, description="Recruiter or contact phone number if available"
    )
    source: str = Field(
        description="Source website/portal: one of 'LinkedIn', 'Workday', 'Greenhouse', 'Lever', or 'Ashby'."
    )
    description: str = Field(
        description="Short summary of the job description, responsibilities, and other details"
    )


class JobList(BaseModel):
    jobs: list[JobItem] = Field(description="List of jobs found")


def _tool_result_text(content) -> str:
    """Flatten a ToolResultBlock's content (str | list of content dicts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _extract_jobs_from_text(text: str) -> list[dict]:
    """Best-effort parse of a scout's returned payload into a list of job dicts.

    A `job_scout` returns "ONLY a JSON array of job objects". We tolerate the array
    being wrapped in a ```json fence, surrounded by stray prose, or wrapped in a
    {"jobs": [...]} object. Returns [] on anything we can't parse — batching is
    best-effort and the end-of-run reconciliation pass is the safety net.
    """
    if not text:
        return []

    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)
    candidates.append(text)

    for chunk in candidates:
        chunk = chunk.strip()
        if not chunk:
            continue
        # Try the whole chunk, then the widest array/object substring within it.
        sub_candidates = [chunk]
        arr = re.search(r"\[.*\]", chunk, re.DOTALL)
        if arr:
            sub_candidates.append(arr.group(0))
        obj = re.search(r"\{.*\}", chunk, re.DOTALL)
        if obj:
            sub_candidates.append(obj.group(0))

        for sub in sub_candidates:
            try:
                parsed = json.loads(sub)
            except Exception:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("jobs"), list):
                parsed = parsed["jobs"]
            if isinstance(parsed, list):
                jobs = [
                    j
                    for j in parsed
                    if isinstance(j, dict) and (j.get("title") or j.get("company"))
                ]
                if jobs:
                    return jobs
    return []


# Running the Agent with real-time thought logs
async def run_job_finder_agent(
    query: str = "",
    user_id: int = None,
    log_callback=None,
    session_id=None,
    is_resume=False,
    batch_callback=None,
    job_types: list[str] = None,
    time_period_days: int = 7,
):
    """Initializes and executes the job finder agent using the claude-agent-sdk.

    The agent researches the user's Search Target `query` across the SEARCH_SOURCES
    (LinkedIn plus the Workday/Greenhouse/Lever/Ashby careers portals). Only when the
    query is empty does it fall back to the DEFAULT_ROLES.

    Args:
        query: The search role/term from Agent Controls, e.g. "Staff Platform Engineer".
            This is the only role searched; DEFAULT_ROLES are a fallback for an empty query.
        user_id: The ID of the user running the search (for multi-tenant data isolation)
        log_callback: Async function to stream thoughts/logs to (receives strings)
        session_id: A valid UUID string.
        is_resume: Whether the session_id is for an existing session to resume.
        batch_callback: Optional async function called with a list of job dicts each
            time a `job_scout` subagent finishes, so results can be persisted in small
            batches as they are found instead of waiting for the whole run to complete.
        job_types: List of job types to search for. Supported values: "fulltime", "remote", "contract".
            Defaults to ["fulltime", "remote"].
        time_period_days: Number of days to search back. Range: 1-90 (1 = last 24 hrs). Defaults to 7.
    """

    from datetime import datetime, timezone, timedelta

    if job_types is None:
        job_types = ["fulltime", "remote"]

    # Clamp time_period_days to 1-90 range (1 = last 24 hours)
    time_period_days = max(1, min(90, time_period_days))

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    since_date = (now - timedelta(days=time_period_days)).strftime("%Y-%m-%d")

    # Search ONLY the user's Search Target query; the hardcoded DEFAULT_ROLES are used
    # solely as a fallback when no query was typed.
    q = (query or "").strip()
    roles = [q] if q else list(DEFAULT_ROLES)
    roles_text = "; ".join(roles)
    sources_text = ", ".join(SEARCH_SOURCES)

    job_types_text = ", ".join(job_types)
    if log_callback:
        await log_callback(
            f"[Agent] Starting job research for roles: {roles_text} "
            f"(job types: {job_types_text}; sources: {sources_text}; posted in last {time_period_days} days)...\n"
        )

    # Format job types for search queries (needed by the scout definition below).
    job_type_str = " ".join(job_types) if job_types else "fulltime remote"

    # Subagent used to parallelize VERIFICATION/EXTRACTION. The orchestrator does the Exa +
    # Tavily searching itself (in-process tools only work on the main agent), then hands each
    # scout a BATCH of candidate listings to format — in parallel.
    job_scout = AgentDefinition(
        description=(
            "Verifies and formats a batch of candidate job postings into the final JSON "
            "schema. Pass it pre-annotated candidates; it returns ONLY a JSON array of job objects."
        ),
        prompt=(
            f"You verify and format candidate job postings ({job_type_str}) found between "
            f"{since_date} and {run_date}.\n"
            "KEEP a candidate when: posted_within_7d is true or null (null means the search "
            "was already time-filtered at the source), remote is not false, and full_time is "
            "not false. DROP contract, temporary, internship, part-time, and onsite/hybrid "
            "roles. For borderline candidates, KEEP them — dropping a real job is worse than "
            "including a borderline one.\n"
            "For each kept job output: title, company, location='Remote', url, date_posted, "
            "posted_within_7d (true unless the date is clearly older than the window), "
            "key_requirements (list of skills), contact_email, contact_phone, source (one of "
            "'LinkedIn', 'Workday', 'Greenhouse', 'Lever', 'Ashby'), description (2-3 sentences).\n"
            "Return ONLY a JSON array [{...}, {...}] — every kept job, no prose, no markdown fence."
        ),
        model="claude-sonnet-5",
        tools=SCOUT_ALLOWED_TOOLS,
    )

    # Agent config
    import uuid

    effective_session_id = session_id or str(uuid.uuid4())

    options = ClaudeAgentOptions(
        session_id=effective_session_id if not is_resume else None,
        resume=effective_session_id if is_resume else None,
        model="claude-sonnet-5",
        agents={"job_scout": job_scout},
        allowed_tools=AGENT_ALLOWED_TOOLS,
        mcp_servers={JOB_SEARCH_SERVER_NAME: job_search_server},
        # 4-5 roles x 5 sources x 2 search tools ≈ 40-50 search calls plus parallel scout
        # batches — 80 turns starved the wider fan-out and cut runs off mid-search.
        max_turns=150,
        output_format=JobList.model_json_schema(),
        permission_mode="bypassPermissions",
    )

    if log_callback:
        await log_callback(
            f"\n[Debug] ANTHROPIC_API_KEY in env: {'ANTHROPIC_API_KEY' in os.environ}\n"
        )
        await log_callback(f"[Debug] Options model: {options.model}\n")

    roles_list = ", ".join(roles)
    # Keyword string for search queries: job types + "remote", de-duplicated in order
    # (job_type_str usually already contains "remote").
    query_keywords = " ".join(dict.fromkeys(f"{job_type_str} remote".split()))
    prompt = (
        f"Find as many {job_type_str} jobs as possible posted between {since_date} and "
        f"{run_date} (last {time_period_days} days). There is NO upper limit on job count — "
        f"more is strictly better. Do not stop early or settle for a sample; exhaust every "
        f"role x source combination below before finishing.\n\n"
        f"Roles (search ALL of them): {roles_list}\n"
        f"Sources (search ALL of them): {sources_text}. Nothing else — never Indeed, "
        f"Glassdoor, Dice, Monster, or ZipRecruiter.\n\n"
        f"For EVERY role x source pair, run BOTH search tools (they return different results; "
        f"skipping one loses jobs):\n"
        f"1. exa_search(query='<role> {query_keywords}', source='<source>', "
        f"time_period_days={time_period_days})\n"
        f"2. tavily_search(query='<role> {query_keywords}', source='<source>', "
        f"time_period_days={time_period_days})\n"
        f"3. If a pair returned fewer than 5 candidates, retry ONCE with a broader query "
        f"variation (drop the seniority qualifier — e.g. 'Principal'/'Senior'/'Staff' — or "
        f"use a close synonym of the role title), then move on.\n"
        f"4. Keep candidates where posted_within_7d is true or null, remote is not false, "
        f"and full_time is not false. When a field is null, keep the candidate — the scout "
        f"verifies borderline cases.\n"
        f"5. As soon as you have 30-40 kept candidates, spawn a job_scout to verify + format "
        f"that batch, and run multiple scouts IN PARALLEL while you keep searching. Pass each "
        f"scout the full candidate data including the remote/full_time/posted_within_7d "
        f"annotations and the source name.\n"
        f"6. Merge all scout outputs, de-duplicate by URL only (same role at different "
        f"companies is NOT a duplicate), and return the COMPLETE merged list — never "
        f"truncate or summarize it.\n\n"
        f'Return ONLY a JSON object of the form {{"jobs": [ ... ]}} with every job found.'
    )

    async with ClaudeSDKClient(options) as client:
        # We start the query, passing the session_id for conversation tracking
        await client.query(prompt, session_id=effective_session_id)

        data = None
        # Track the tool_use_id of each `Task` (job_scout) call so we can recognise its
        # result when it streams back and persist that scout's jobs immediately.
        scout_task_ids: set[str] = set()
        # Safety net: accumulate every job parsed from a scout result, de-duped by URL
        # (then title|company). If the orchestrator's final message yields no parseable
        # job list, we still return everything the scouts found so it gets saved — the
        # symptom this guards against is "console showed jobs found but the DB stayed empty".
        collected_jobs: list[dict] = []
        seen_job_keys: set[str] = set()

        def _remember(jobs_batch):
            for j in jobs_batch:
                if not isinstance(j, dict):
                    continue
                key = (j.get("url") or "").strip() or (
                    f"{j.get('title', '')}|{j.get('company', '')}|{j.get('location', '')}"
                )
                if key and key not in seen_job_keys:
                    seen_job_keys.add(key)
                    collected_jobs.append(j)
        # Process the response stream manually to extract tool execution logs and thinking
        async for msg in client.receive_response():
            # If it's an assistant message, we can get thought process and tool execution
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ThinkingBlock):
                        if log_callback:
                            await log_callback(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        # The SDK's built-in subagent-spawning tool is named "Agent" (not
                        # "Task" as older docs suggest) — confirmed via live tool-call logs.
                        # Getting this name wrong silently breaks batch persistence: no id
                        # ever lands in scout_task_ids, so no ToolResultBlock ever matches,
                        # and batch_callback never fires (only the final reconciliation save
                        # runs — a real bug, previously masked because it "fails safe").
                        if block.name == "Agent":
                            scout_task_ids.add(block.id)
                        if log_callback:
                            tool_msg = f"\n[Tool Call] Running tool '{block.name}' with arguments: {block.input}\n"
                            await log_callback(tool_msg)
                    elif isinstance(block, TextBlock):
                        if log_callback:
                            await log_callback(block.text)

            # A `Task` (job_scout) result streams back as a UserMessage containing a
            # ToolResultBlock. Parse the scout's JSON array and persist it as a batch
            # right away, before the orchestrator merges everything or the run ends.
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if (
                        isinstance(block, ToolResultBlock)
                        and block.tool_use_id in scout_task_ids
                        and not block.is_error
                    ):
                        batch = _extract_jobs_from_text(
                            _tool_result_text(block.content)
                        )
                        if batch:
                            _remember(batch)
                        if batch and batch_callback:
                            try:
                                await batch_callback(batch)
                            except Exception as e:
                                if log_callback:
                                    await log_callback(
                                        f"\n[Agent] Batch save failed: {e}\n"
                                    )

            # When the generator completes the task, it outputs a ResultMessage
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    error_detail = msg.errors if msg.errors else msg.result
                    if log_callback:
                        await log_callback(f"\n[Agent Error] {error_detail}\n")
                elif msg.structured_output:
                    data = msg.structured_output
                elif msg.result:
                    # Parse the final merged list from the orchestrator's prose. Use the
                    # tolerant shared extractor (handles ```json fences, bare arrays,
                    # {"jobs": [...]} wrappers, and JSON embedded in prose) rather than a
                    # narrow regex — the old regex only matched a fenced object and returned
                    # None for a bare array or unfenced JSON, silently dropping the whole run.
                    jobs = _extract_jobs_from_text(msg.result)
                    if jobs:
                        data = {"jobs": jobs}
                    elif log_callback:
                        await log_callback(
                            "\n[Agent] Could not parse a job list from the final result; "
                            "falling back to jobs collected from scout batches.\n"
                        )
                break

        # Safety net: if the orchestrator's final message produced no parseable job list,
        # save everything the scouts returned during the run so a run that clearly found
        # jobs never ends up persisting nothing.
        if (not data or not (data.get("jobs") if isinstance(data, dict) else None)) and collected_jobs:
            data = {"jobs": collected_jobs}
            if log_callback:
                await log_callback(
                    f"\n[Agent] Using {len(collected_jobs)} jobs collected from scout results.\n"
                )

        if log_callback:
            await log_callback("\n[Agent] Search complete. Parsing results...\n")

        return data
