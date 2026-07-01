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


# Full toolset granted to the orchestrator (see the Claude Agent SDK overview:
# https://code.claude.com/docs/en/agent-sdk/overview). Job discovery is done primarily via
# the Exa + Tavily search tools (in-process SDK MCP server `jobsearch`), with the built-in
# WebSearch/WebFetch as a fallback, plus file I/O and system tools for processing/merging.
AGENT_ALLOWED_TOOLS = [
    # File and text operations
    "Read",
    "Write",
    "Edit",
    # System operations
    "Bash",
    "Glob",
    "Grep",
    # Job search APIs (Exa + Tavily, via the in-process `jobsearch` MCP server)
    EXA_TOOL,
    TAVILY_TOOL,
    # Web operations (fallback / reading individual listings)
    "WebSearch",
    "WebFetch",
    # Agent control
    "Task",  # spawns the job_scout subagent (fan-out)
    "TodoWrite",
]

# Tools granted to the job_scout subagent. NOTE: in-process SDK MCP tools (exa/tavily)
# CANNOT be granted to subagents — `AgentDefinition.mcpServers` is JSON-serialized for the
# CLI and a live in-process server isn't serializable. So scouts verify/extract candidate
# URLs (supplied by the orchestrator) using WebFetch; the orchestrator does the Exa/Tavily
# searching itself.
SCOUT_ALLOWED_TOOLS = [
    # File and text operations
    "Read",
    "Write",
    "Edit",
    # System operations
    "Bash",
    "Glob",
    "Grep",
    # Web operations — open candidate listings and verify them
    "WebFetch",
    "WebSearch",
    # Task tracking
    "TodoWrite",
]


# Target roles that are ALWAYS searched on every run, regardless of the typed query.
# These are remote, full-time, Principal-level platform/infra roles.
# Reduced to 2 roles to avoid Claude API rate limits (4 roles = 8+ agent calls, too aggressive).
DEFAULT_ROLES = [
    "Principal DevOps Engineer",
    "Principal Cloud Engineer",
]

# The only two sources the agent searches. "pull" fans subagents out across these.
SEARCH_SOURCES = ["LinkedIn", "Workday"]


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
        description="True ONLY if the job was posted within the last 7 days (on or after 7 days before the run date). Set False for anything older or if the posting date is unknown."
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
        description="Source website/portal: one of 'Workday' or 'LinkedIn'."
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

    The agent ALWAYS researches the DEFAULT_ROLES (remote, full-time, last 7 days) across
    LinkedIn and Workday careers. A non-empty `query` is added as an extra role to search
    on top of the defaults.

    Args:
        query: Optional extra search role/term, e.g. "Staff Platform Engineer". The four
            DEFAULT_ROLES are always searched regardless of this value.
        user_id: The ID of the user running the search (for multi-tenant data isolation)
        log_callback: Async function to stream thoughts/logs to (receives strings)
        session_id: A valid UUID string.
        is_resume: Whether the session_id is for an existing session to resume.
        batch_callback: Optional async function called with a list of job dicts each
            time a `job_scout` subagent finishes, so results can be persisted in small
            batches as they are found instead of waiting for the whole run to complete.
        job_types: List of job types to search for. Supported values: "fulltime", "remote", "contract".
            Defaults to ["fulltime", "remote"].
        time_period_days: Number of days to search back. Range: 7-90. Defaults to 7.
    """

    from datetime import datetime, timezone, timedelta

    if job_types is None:
        job_types = ["fulltime", "remote"]

    # Clamp time_period_days to 7-90 range
    time_period_days = max(7, min(90, time_period_days))

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    since_date = (now - timedelta(days=time_period_days)).strftime("%Y-%m-%d")

    # Always search the default Principal roles; add the typed query as an extra role.
    roles = list(DEFAULT_ROLES)
    q = (query or "").strip()
    if q and q.lower() not in [r.lower() for r in roles]:
        roles.append(q)
    roles_text = "; ".join(roles)
    sources_text = " and ".join(SEARCH_SOURCES)

    job_types_text = ", ".join(job_types)
    if log_callback:
        await log_callback(
            f"[Agent] Starting job research for roles: {roles_text} "
            f"(job types: {job_types_text}; sources: {sources_text}; posted in last {time_period_days} days)...\n"
        )

    # Subagent used to parallelize VERIFICATION/EXTRACTION. The orchestrator does the Exa +
    # Tavily searching itself (in-process tools only work on the main agent), then hands each
    # scout a BATCH of candidate listings to open, verify, and extract — in parallel.
    job_scout = AgentDefinition(
        description=(
            "Given a batch of ALREADY-VERIFIED candidate job postings (remote/full-time/last-7-days "
            "already determined by the search tools), formats each into a structured JobItem. Used "
            "for parallel formatting/extraction across a large candidate pool."
        ),
        prompt=(
            "Format job batch: keep if posted_within_7d=true/null (trust search tools, don't re-verify).\n"
            "Drop if: posted_within_7d=false OR remote=false OR full_time=false.\n"
            "For each kept job: title, company, location='Remote', url, date_posted, posted_within_7d, "
            "key_requirements, contact_email, contact_phone, source, description.\n"
            "Return ONLY JSON array — no commentary."
        ),
        model="claude-haiku-4-5-20251001",
        tools=SCOUT_ALLOWED_TOOLS,
    )

    # Agent config
    import uuid

    # Build dynamic system prompt based on selected job types
    job_type_constraints = []
    if "remote" in job_types:
        job_type_constraints.append("remote=true/null")
    if "fulltime" in job_types:
        job_type_constraints.append("full_time=true/null")
    if "contract" in job_types:
        job_type_constraints.append("contract=true/null")
    constraints_text = ", ".join(job_type_constraints) if job_type_constraints else "remote=true/null, full_time=true/null"

    effective_session_id = session_id or str(uuid.uuid4())
    options = ClaudeAgentOptions(
        session_id=effective_session_id if not is_resume else None,
        resume=effective_session_id if is_resume else None,
        model="claude-haiku-4-5-20251001",
        agents={"job_scout": job_scout},
        allowed_tools=AGENT_ALLOWED_TOOLS,
        mcp_servers={JOB_SEARCH_SERVER_NAME: job_search_server},
        max_turns=150,
        output_format=JobList.model_json_schema(),
        permission_mode="bypassPermissions",
        system_prompt=(
            f"Job Finder: Find jobs from last {time_period_days} days only. Keep it simple.\n"
            "1. Search exa_search + tavily_search for each role on LinkedIn and Workday.\n"
            f"2. Filter: {constraints_text}, posted_within_{time_period_days}d=true/null.\n"
            "3. Batch candidates (30-40) and spawn job_scout subagents SEQUENTIALLY to format.\n"
            "4. Return JSON: {\"jobs\": [...]} with only valid jobs.\n"
            "Tools: exa_search, tavily_search for LinkedIn/Workday only. Fall back to WebSearch if rate limited.\n"
            "Sources: LinkedIn + Workday only. No Glassdoor/Indeed/etc.\n"
            f"Constraints: {constraints_text}, posted_within_{time_period_days}d=true ONLY."
        ),
    )

    if log_callback:
        await log_callback(
            f"\n[Debug] ANTHROPIC_API_KEY in env: {'ANTHROPIC_API_KEY' in os.environ}\n"
        )
        await log_callback(f"[Debug] Options model: {options.model}\n")

    roles_list = ", ".join(roles)
    prompt = (
        f"Run date: {run_date}. Keep only jobs from {since_date} onward ({time_period_days} days).\n"
        f"Roles: {roles_list}\n"
        f"Sources: LinkedIn + Workday only.\n"
        f"Job types to find: {', '.join(job_types)}.\n\n"
        f"STEPS:\n"
        f"1. Search: for each role, call exa_search + tavily_search on LinkedIn, then Workday. ({len(roles)*4} calls)\n"
        f"2. Filter: keep posted_within_{time_period_days}d=true/null, and match selected job types.\n"
        f"3. Batch (~30-40 each) and spawn job_scout agents SEQUENTIALLY.\n"
        f"4. Merge results, de-dupe by URL.\n\n"
        f"Return ONLY: ```json\n{{\n\"jobs\": [...]\n}}\n```"
    )

    async with ClaudeSDKClient(options) as client:
        # We start the query, passing the session_id for conversation tracking
        await client.query(prompt, session_id=effective_session_id)

        data = None
        # Track the tool_use_id of each `Task` (job_scout) call so we can recognise its
        # result when it streams back and persist that scout's jobs immediately.
        scout_task_ids: set[str] = set()
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
                    # Fallback to parse json directly
                    try:
                        # First try to find markdown json block
                        match = re.search(
                            r"```json\s*(\{.*?\})\s*```",
                            msg.result,
                            re.DOTALL | re.IGNORECASE,
                        )
                        if not match:
                            # Fallback to finding just the outer brackets
                            match = re.search(r"(\{.*\})", msg.result, re.DOTALL)
                        if match:
                            data = json.loads(match.group(1))
                    except Exception as e:
                        print("Failed to parse JSON fallback:", e)
                break

        if log_callback:
            await log_callback("\n[Agent] Search complete. Parsing results...\n")

        return data
