# Job Finder — Agent Documentation

This documents the design, configuration, and tools of the AI Job Finder Agent built on
the **Claude Agent SDK** (`claude-agent-sdk`). The implementation lives in
`backend/agent.py`.

## Overview

The Job Finder is a specialized autonomous agent that researches **LinkedIn and Workday
careers portals** for **remote, full-time** positions in a fixed set of Principal-level
platform/infra roles (DevOps, Cloud, Kubernetes, SRE) — plus any extra role the user types —
**posted within the last 7 days**. It runs asynchronously as a FastAPI background task,
delegates breadth to parallel subagents (one per role × source), evaluates findings against
the remote + full-time + 7-day-freshness criteria, and returns structured JSON that is
persisted to SQLite.

---

## Authentication — OAuth only

The backend authenticates to Claude **exclusively via the stored Claude OAuth
credentials** (`~/.claude`) used by the `claude` CLI. It **never** uses an Anthropic API
key. `agent.py` drops `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the environment
at import time so the SDK-spawned CLI uses its own OAuth login. No API key is required to
run this project.

**In the cloud (Render):** there is no interactive `claude login`, so OAuth is supplied via the
`CLAUDE_CODE_OAUTH_TOKEN` env var (minted locally with `claude setup-token`). The SDK/CLI honor
it and `agent.py` does **not** drop it — only the API-key vars are dropped. Still OAuth, still no
API key. See `render.yaml`, `backend/Dockerfile.render`, and the README "Cloud Deployment" section.

---

## Agent Configuration

The agent is configured via `ClaudeAgentOptions` in `run_job_finder_agent()`:

* **Model**: `model=None` — inherits whatever model the `claude` CLI is logged in with.
* **max_turns**: `80` (multi-agent fan-out needs headroom).
* **permission_mode**: `bypassPermissions`.
* **allowed_tools**: `AGENT_ALLOWED_TOOLS` grants the built-in toolset (`Read`, `Write`,
  `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `Task`, `TodoWrite`) **plus** the
  Exa/Tavily search tools (`mcp__jobsearch__exa_search`, `mcp__jobsearch__tavily_search`).
* **mcp_servers**: `{"jobsearch": job_search_server}` — an in-process SDK MCP server
  (`backend/search_tools.py`) wrapping the Exa and Tavily search APIs. This is the only MCP
  integration; there are no external MCP servers.
* **agents**: registers a `job_scout` subagent, which enables the built-in **Task** tool.
* **output_format**: `JobList.model_json_schema()` for guaranteed structured output.

### Orchestrator + subagent design

* The **orchestrator** always searches `DEFAULT_ROLES` (Principal DevOps / Cloud / Kubernetes /
  Site Reliability Engineer) plus any user-supplied extra role, and spawns one `job_scout`
  **per role × source** via the Task tool — **LinkedIn (`linkedin.com/jobs`) and Workday careers
  portals (`*.myworkdayjobs.com`) only** (no Glassdoor/Dice/Monster/Indeed/ZipRecruiter) — running
  them in parallel, then merges and de-duplicates the combined results.
* Each **`job_scout`** is given one role, one source, and the run date, and returns a
  JSON array of remote full-time jobs for that pair. Scouts discover listings with the
  `exa_search` + `tavily_search` tools, read them with `WebFetch`, and fall back to `WebSearch`
  if a search-API key is missing (`model="inherit"`).

### Goals & constraints

* **Sources**: ONLY LinkedIn and Workday careers portals — fixed in `agent.py`
  (`SEARCH_SOURCES`, scout prompt, system prompt, run prompt). Do not add other boards.
* **Roles**: always search `DEFAULT_ROLES`; a non-empty query is appended as an extra role.
* **Remote-only**: every kept job must be remote.
* **Volume**: pull **as many** matching jobs as possible — there is no upper limit (the
  previous "aim for 20–30" cap was removed); fan out one subagent per role × source.
* **Freshness**: include **only** jobs posted within the **last 7 days**. Scouts use each
  source's last-7-days recency filter (e.g. LinkedIn `f_TPR=r604800` + remote `f_WT=2`, Workday
  'posted in the last week') and set `posted_within_7d` on every job.
* **Full-time only**: keep **full-time (FTE)** roles; drop contract, temporary, part-time,
  and internship roles.

---

## Search tooling — Exa + Tavily (`backend/search_tools.py`)

Job discovery uses the **Exa** and **Tavily** search APIs, wrapped as in-process SDK MCP tools
(`create_sdk_mcp_server`, server name `jobsearch`). Keys come from env `EXA_API_KEY` /
`TAVILY_API_KEY` (never hardcoded). The domain map allows only `linkedin.com` and
`myworkdayjobs.com`.

### 1. `mcp__jobsearch__exa_search(query, source)` / `mcp__jobsearch__tavily_search(query, source)`
* **Purpose**: Primary job discovery. Each scopes results to the assigned source's domain and the
  last 7 days, returning a compact JSON list of candidate postings (`title, url, published_date,
  snippet`). Far higher recall than the built-in `WebSearch`. If a key is missing the tool returns a
  clear message and the agent falls back to `WebSearch`.

### 2. `WebSearch` (fallback)
* **Purpose**: Run targeted web queries scoped to the two allowed sources (e.g.
  `Principal SRE remote site:linkedin.com/jobs`, `Principal SRE remote site:myworkdayjobs.com`)
  when a search-API key is unavailable.

### 3. `WebFetch`
* **Purpose**: Open and read individual job listings to verify posting dates / remote + full-time
  status and extract structured fields.

---

## Live Thought Streaming

The agent's reasoning is surfaced to the React UI in real time. `agent.py` iterates the
SDK response stream and forwards blocks to a `log_callback`:

* **ThinkingBlock** → streamed as the agent's internal reasoning.
* **ToolUseBlock** → streamed as `[Tool Call] …` with the tool name and arguments.
* **TextBlock** → streamed as assistant text.

`main.py` publishes these over **Server-Sent Events** (`GET /api/stream`). When jobs are
written to the database it emits a `Database now holds …` line, which the frontend uses to
refresh the dashboard immediately. Each line is also appended to an in-memory `log_history`
buffer (bounded by `LOG_HISTORY_MAX`, cleared when a new `/api/pull` starts); on connect the
stream replays that buffer before going live, so a client that reconnects mid-run — e.g.
after a page refresh — sees the logs already produced instead of an empty console.

## Incremental Batch Persistence

Results are saved to the database **in small batches as scouts finish**, not in one bulk
write at the end. `run_job_finder_agent` accepts a `batch_callback`; while streaming it
tracks each `Task` (job_scout) tool-use id and, when that scout's `ToolResultBlock`
(its JSON array of jobs) streams back, parses the jobs and invokes `batch_callback`
immediately. `main.py`'s callback persists the batch with `save_job` (URL-keyed dedup →
idempotent) and emits a `Database now holds …` line, so the dashboard fills in
progressively. The final structured-output list is still saved at the end as a
de-duplicating reconciliation pass. See `app_spec.md` Task 5.

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
    posted_within_7d: bool   # True only if posted within the last 7 days
    key_requirements: list   # List of technical skills
    contact_email: str | None
    contact_phone: str | None
    source: str              # Portal source: 'Workday' or 'LinkedIn'
    description: str          # Short job description summary

class JobList(BaseModel):
    jobs: list[JobItem]
```

Results are persisted to SQLite (`backend/db.py`), de-duplicated by URL (or by
`title|company|location` when the URL is missing), preserving each job's `applied` status
across re-runs.
