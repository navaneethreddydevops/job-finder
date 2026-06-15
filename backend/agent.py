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

# Load environment variables
load_dotenv()

# The claude-agent-sdk spawns the `claude` CLI, which must authenticate via the
# stored Claude OAuth credentials (~/.claude). An ANTHROPIC_API_KEY in the env
# forces API-key auth instead, which we never want here. Always drop it (and the
# legacy ANTHROPIC_AUTH_TOKEN) so the CLI uses its own OAuth login.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


# Full built-in toolset granted to the orchestrator (see the Claude Agent SDK overview:
# https://code.claude.com/docs/en/agent-sdk/overview). The agent relies solely on Claude's
# built-in web tooling (WebSearch + WebFetch) — there is no MCP integration. Granting tools
# explicitly makes the agent's capabilities clear rather than relying on bypassPermissions alone.
AGENT_ALLOWED_TOOLS = [
    # Built-in tools
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Task",  # spawns the job_scout subagent (fan-out)
    "TodoWrite",
]


# Pydantic Schemas for Structured Output
class JobItem(BaseModel):
    title: str = Field(description="The job title")
    company: str = Field(description="The company name")
    location: str = Field(
        description="The location, e.g. 'Remote', 'City, State', or 'Hybrid'"
    )
    url: str = Field(description="The direct job posting link or source URL")
    date_posted: str = Field(
        description="Date posted or found, e.g. '2 hours ago', 'today', 'June 12'"
    )
    posted_within_24h: bool = Field(
        description="True ONLY if the job was posted within the last 24 hours (i.e. today / on the run date). Set False for anything older or if the posting date is unknown."
    )
    c2c_viability: str = Field(
        description="Confirmation of C2C viability: 'Confirmed C2C', 'Likely C2C' (if mentions C2C or corp-to-corp but not explicitly confirmed), or 'Not Specified'"
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
        description="Source website/portal, e.g., LinkedIn, Indeed, Dice, etc."
    )
    description: str = Field(
        description="Short summary of the job description, C2C terms, and other details"
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
    query: str,
    log_callback=None,
    session_id=None,
    is_resume=False,
    batch_callback=None,
):
    """Initializes and executes the job finder agent using the claude-agent-sdk.

    Args:
        query: Search criteria, e.g. "C2C Data Engineer"
        log_callback: Async function to stream thoughts/logs to (receives strings)
        session_id: A valid UUID string.
        is_resume: Whether the session_id is for an existing session to resume.
        batch_callback: Optional async function called with a list of job dicts each
            time a `job_scout` subagent finishes, so results can be persisted in small
            batches as they are found instead of waiting for the whole run to complete.
    """

    from datetime import datetime, timezone

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if log_callback:
        await log_callback(f"[Agent] Starting Job Search for query: '{query}'...\n")

    # Subagent used to parallelize the search across job boards. The orchestrator
    # spawns one `job_scout` per source (LinkedIn, Dice, Monster, Indeed, ...) via the
    # built-in Task tool so coverage scales out instead of being done serially.
    job_scout = AgentDefinition(
        description=(
            "Scouts a single job board/source for recent C2C (Corp-to-Corp) Data Engineer "
            "postings and returns them as a JSON list. Use this for each source you want covered."
        ),
        prompt=(
            "You are a focused job scout. You will be given ONE source (e.g. LinkedIn, Dice, Monster, "
            "Indeed, ZipRecruiter), a search query, and the run date. "
            "Find AS MANY FRESH C2C / Corp-to-Corp Data Engineer postings on that source as you possibly can, but "
            "ONLY ones posted within the LAST 24 HOURS (i.e. today / on the run date). Discard anything older. "
            "Use each site's recency filter to enforce this — e.g. LinkedIn `f_TPR=r86400`, Indeed `fromage=1`, "
            "Dice/Monster 'posted today / last 24 hours'. "
            "Use the built-in `WebSearch` tool for quick lookups with targeted queries like "
            "'C2C Data Engineer site:<source>', and use `WebFetch` to open and read individual listings. "
            "Verify each posting's date before keeping it. "
            "For each job extract: title, company, location, url, date_posted (e.g. '3 hours ago', 'today'), "
            "posted_within_24h (true only when genuinely posted in the last 24 hours), c2c_viability "
            "('Confirmed C2C', 'Likely C2C', or 'Not Specified'), key_requirements (list), contact_email, "
            "contact_phone, source, and a short description. Skip strictly-W2 roles and anything older than 24 hours. "
            "Return ONLY a JSON array of job objects — no commentary."
        ),
        model="inherit",
        tools=["WebSearch", "WebFetch"],
    )

    # Agent config
    import uuid

    effective_session_id = session_id or str(uuid.uuid4())
    options = ClaudeAgentOptions(
        session_id=effective_session_id if not is_resume else None,
        resume=effective_session_id if is_resume else None,
        model=None,
        agents={"job_scout": job_scout},
        allowed_tools=AGENT_ALLOWED_TOOLS,
        max_turns=80,
        output_format=JobList.model_json_schema(),
        permission_mode="bypassPermissions",
        system_prompt=(
            "You are a professional Job Finder orchestrator specializing in finding C2C (Corp-to-Corp) "
            "Data Engineer roles. Your goal is to compile AS MANY matching, recently-posted jobs as possible — "
            "there is no upper limit; more is better. "
            "You have a `job_scout` subagent (invoke it with the Task tool) plus Claude's built-in web tools "
            "(`WebSearch` for queries and `WebFetch` for reading individual listings). "
            "STRATEGY: Delegate breadth to subagents. Spawn one `job_scout` per source — at minimum LinkedIn, Dice, "
            "Monster, Indeed, and ZipRecruiter — running them in parallel (issue multiple Task calls together) so the "
            "search fans out. Each scout returns a JSON array of jobs for its source. You may spawn additional scouts "
            "for more sources or extra query variations if it yields more jobs. "
            "Then merge every scout's results, de-duplicate by URL (or by title+company when the URL is missing), and "
            "keep ONLY roles posted within the last 24 hours (today / the run date) where C2C / Corp-to-Corp is "
            "explicitly mentioned or very likely. Drop anything older than 24 hours and set posted_within_24h "
            "accurately on every job. "
            "CRITICAL: Your final answer MUST be valid JSON matching the provided schema (a single 'jobs' key holding "
            "the full merged list). Do not return conversational markdown."
        ),
    )

    if log_callback:
        await log_callback(
            f"\n[Debug] ANTHROPIC_API_KEY in env: {'ANTHROPIC_API_KEY' in os.environ}\n"
        )
        await log_callback(f"[Debug] Options model: {options.model}\n")

    prompt = (
        f"The run date is {run_date}. Compile a list of AS MANY C2C Data Engineer job postings as you possibly can "
        f"matching the query '{query}', but ONLY jobs posted within the LAST 24 HOURS (today / the run date {run_date}). "
        f"There is no upper limit — find as many fresh ones as you can. "
        f"Fan the search out by spawning one `job_scout` subagent per source via the Task tool, running them in "
        f"parallel: at minimum LinkedIn, Dice, Monster, Indeed, and ZipRecruiter. Pass each scout the run date and "
        f"tell it to only return jobs posted in the last 24 hours. Spawn extra scouts for additional sources or query "
        f"variations if they surface more fresh jobs. "
        f"Each scout should use targeted queries like 'C2C Data Engineer site:linkedin.com', "
        f"'Contract Data Engineer C2C site:monster.com', or 'Data Engineer C2C site:dice.com', combined with each "
        f"site's last-24-hours recency filter. "
        f"DISCARD any job older than 24 hours, and set posted_within_24h=true on every job you return. "
        f"Filter out jobs that are strictly W2 or do not allow contract terms. "
        f"Merge all scout results and de-duplicate before responding. "
        f"\n\nCRITICAL: When you are done, you MUST return the final list of jobs as ONLY a valid JSON object wrapped in ```json ... ``` blocks. "
        f"The JSON object must have a single key 'jobs' containing a list of job objects. Each job object must match this schema:\n"
        f"{json.dumps(JobList.model_json_schema())}\n"
        f"Do not include any other markdown tables or conversational text in your final response."
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
                        if block.name == "Task":
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
