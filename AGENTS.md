# C2C Job Finder — Agent Documentation

This documents the design, configuration, and tools of the AI Job Finder Agent built on
the **Claude Agent SDK** (`claude-agent-sdk`). The implementation lives in
`backend/agent.py`.

## Overview

The Job Finder is a specialized autonomous agent that scours job boards for
**Corp-to-Corp (C2C) Data Engineer** positions **posted within the last 24 hours**. It
runs asynchronously as a FastAPI background task, delegates breadth to parallel
subagents, evaluates findings against C2C criteria and freshness, and returns structured
JSON that is persisted to SQLite.

---

## Authentication — OAuth only

The backend authenticates to Claude **exclusively via the stored Claude OAuth
credentials** (`~/.claude`) used by the `claude` CLI. It **never** uses an Anthropic API
key. `agent.py` drops `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the environment
at import time so the SDK-spawned CLI uses its own OAuth login. No API key is required to
run this project.

---

## Agent Configuration

The agent is configured via `ClaudeAgentOptions` in `run_job_finder_agent()`:

* **Model**: `model=None` — inherits whatever model the `claude` CLI is logged in with.
* **max_turns**: `80` (multi-agent fan-out needs headroom).
* **permission_mode**: `bypassPermissions`.
* **allowed_tools**: `AGENT_ALLOWED_TOOLS` grants the full built-in toolset (`Read`, `Write`,
  `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `Task`, `TodoWrite`). The agent
  relies solely on Claude's built-in web tooling — there is **no MCP integration**.
* **agents**: registers a `job_scout` subagent, which enables the built-in **Task** tool.
* **output_format**: `JobList.model_json_schema()` for guaranteed structured output.

### Orchestrator + subagent design

* The **orchestrator** spawns one `job_scout` per source via the Task tool — at minimum
  LinkedIn, Dice, Monster, Indeed, and ZipRecruiter — running them in parallel, then
  merges and de-duplicates the combined results.
* Each **`job_scout`** is given one source, the query, and the run date, and returns a
  JSON array of jobs for that source. Scouts use Claude's built-in `WebSearch` and
  `WebFetch` tools (`model="inherit"`).

### Goals & constraints

* **Volume**: pull **as many** matching jobs as possible — there is no upper limit (the
  previous "aim for 20–30" cap was removed).
* **Freshness**: include **only** jobs posted within the **last 24 hours** (today / the
  run date). Scouts use each board's last-24h recency filter (e.g. LinkedIn
  `f_TPR=r86400`, Indeed `fromage=1`) and set `posted_within_24h` on every job.
* **C2C**: keep roles where C2C / Corp-to-Corp is explicitly mentioned or very likely;
  drop strictly-W2 roles.

---

## Web tooling (built-in)

The agent uses Claude's built-in web tools directly — no MCP servers are configured.

### 1. `WebSearch`
* **Purpose**: Run targeted web queries (e.g. `C2C Data Engineer site:linkedin.com`) with
  each board's last-24h recency filter.

### 2. `WebFetch`
* **Purpose**: Open and read individual job listings to verify posting dates and extract
  structured fields.

---

## Live Thought Streaming

The agent's reasoning is surfaced to the React UI in real time. `agent.py` iterates the
SDK response stream and forwards blocks to a `log_callback`:

* **ThinkingBlock** → streamed as the agent's internal reasoning.
* **ToolUseBlock** → streamed as `[Tool Call] …` with the tool name and arguments.
* **TextBlock** → streamed as assistant text.

`main.py` publishes these over **Server-Sent Events** (`GET /api/stream`). When jobs are
written to the database it emits a `Database now holds …` line, which the frontend uses to
refresh the dashboard immediately.

---

## Response Schema (Structured Output)

The agent is configured with `output_format=JobList.model_json_schema()`, guaranteeing the
response matches this Pydantic model (`backend/agent.py`):

```python
class JobItem(BaseModel):
    title: str               # The job title
    company: str             # The company name
    location: str            # Remote, City/State, or Hybrid
    url: str                 # The direct posting URL
    date_posted: str         # e.g. "2 hours ago", "today", "June 12"
    posted_within_24h: bool  # True only if posted within the last 24 hours / run date
    c2c_viability: str       # Confirmed C2C, Likely C2C, or Not Specified
    key_requirements: list   # List of technical skills
    contact_email: str | None
    contact_phone: str | None
    source: str              # Portal source (e.g. LinkedIn, Dice)
    description: str          # C2C terms summary & description

class JobList(BaseModel):
    jobs: list[JobItem]
```

Results are persisted to SQLite (`backend/db.py`), de-duplicated by URL (or by
`title|company|location` when the URL is missing), preserving each job's `applied` status
across re-runs.
