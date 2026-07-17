# Job Finder — Agent Documentation

This documents the design, configuration, and tools of the AI Job Finder Agent built on
the **Claude Agent SDK** (`claude-agent-sdk`). The implementation lives in
`backend/agent.py`.

## Overview

The Job Finder is a specialized autonomous agent that researches **LinkedIn, Indeed, Glassdoor,
ZipRecruiter (via the structured JobSpy scraper), the Workday/Greenhouse/Lever/Ashby careers
portals, Dice, Wellfound, Built In, and employer career pages** for **remote, full-time positions
open to US-based candidates** in the role the user types as the Search Target (falling back to a
default set of Principal-level platform/infra roles — DevOps, Cloud, Kubernetes, SRE — only when
the query is empty), **posted within the last 7 days** (narrowed to "since the last successful
run" on repeat searches via the `pull_checkpoints` table). It runs asynchronously as a FastAPI
background task, delegates verification/formatting to parallel `job_scout` subagents, evaluates
findings against the remote + full-time + US-eligible + freshness criteria, and returns structured
JSON that is persisted to the database in incremental batches.

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

* **Model**: `claude-sonnet-5` for the orchestrator; `claude-haiku-4-5` for the `job_scout`
  subagents (mechanical verify+format work — ~3x cheaper per token).
* **max_turns**: `150` (per role: 8 non-jobspy sources × 2 search tools — portals first —
  plus 1 jobspy call and parallel scout batches).
* **permission_mode**: `bypassPermissions`.
* **allowed_tools**: `AGENT_ALLOWED_TOOLS` grants the built-in toolset (`Read`, `Write`,
  `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `Task`, `TodoWrite`) **plus** the
  search tools (`mcp__jobsearch__jobspy_search`, `mcp__jobsearch__exa_search`,
  `mcp__jobsearch__tavily_search`).
* **mcp_servers**: `{"jobsearch": job_search_server}` — an in-process SDK MCP server
  (`backend/search_tools.py`) wrapping the JobSpy scraper and the Exa/Tavily search APIs. This
  is the only MCP integration; there are no external MCP servers.
* **agents**: registers a `job_scout` subagent, which enables the built-in **Task** tool.
* **output_format**: `JobList.model_json_schema()` for guaranteed structured output.

### Orchestrator + subagent design

* The **orchestrator** searches ONLY the user's Search Target query as the role; `DEFAULT_ROLES`
  (Principal DevOps / Cloud / Kubernetes / Site Reliability Engineer) are a fallback used only
  when the query is empty. Per role it FIRST searches the **career portals** (`PORTAL_SOURCES`:
  Workday, Greenhouse, Lever, Ashby, and `Company` open-web employer career pages) with
  `exa_search` + `tavily_search` itself (in-process SDK MCP tools can't be granted to
  subagents) — these yield direct employer apply links and get the deepest effort; THEN calls
  `jobspy_search` once (structured, pre-verified bulk scrape of Indeed/LinkedIn/Glassdoor/
  ZipRecruiter/Google Jobs); THEN searches the secondary boards (Dice, Wellfound, Built In)
  with both search tools; and finally merges and de-duplicates the combined results.
* Each **`job_scout`** is handed a batch of 30-40 pre-annotated candidate postings and returns a
  JSON array of verified remote full-time US-eligible jobs; scouts run in parallel while the
  orchestrator keeps searching, and may use `WebFetch`/`WebSearch` to verify borderline
  candidates — but NEVER for `pre_verified=true` (jobspy) candidates, which are format-only.

### Goals & constraints

* **Sources**: the 12 entries of `SEARCH_SOURCES` in `agent.py`, in priority order — the
  career portals Workday, Greenhouse, Lever, Ashby, and `Company` (employer career pages)
  come FIRST (highest priority, searched most deeply, sorted to the top of the dashboard),
  then LinkedIn, Indeed, Glassdoor, ZipRecruiter (jobspy-covered), then Dice, Wellfound,
  Built In.
* **Data quality**: every job must have a valid http(s) URL pointing at the specific posting
  plus a non-empty title and company — `db.save_job` drops anything else (returns `None`),
  and `search_tools._is_valid_job_url` filters obvious search/category/index-page URLs out
  of tool results before the agent sees them.
* **Roles**: search the user's Search Target query only; `DEFAULT_ROLES` are the fallback for an empty query.
* **Remote US-only**: every kept job must be remote AND open to US-based candidates
  (`us_eligible` is never false; jobspy is US-scoped structurally).
* **Volume**: pull **as many** matching jobs as possible — there is no upper limit (the
  previous "aim for 20–30" cap was removed); fan out one subagent per role × source.
* **Freshness**: include **only** jobs posted within the **last 7 days**. Scouts use each
  source's last-7-days recency filter (e.g. LinkedIn `f_TPR=r604800` + remote `f_WT=2`, Workday
  'posted in the last week') and set `posted_within_7d` on every job.
* **Full-time only**: keep **full-time (FTE)** roles; drop contract, temporary, part-time,
  and internship roles.

---

## Search tooling — JobSpy + Exa + Tavily (`backend/search_tools.py`)

Job discovery uses the **JobSpy** structured scraper (open-source `python-jobspy`, free, no key)
plus the **Exa** and **Tavily** search APIs, wrapped as in-process SDK MCP tools
(`create_sdk_mcp_server`, server name `jobsearch`). Exa/Tavily keys come from env `EXA_API_KEY` /
`TAVILY_API_KEY` (never hardcoded). The domain map (`ALL_SOURCE_DOMAINS`) covers
`linkedin.com`, `indeed.com`, `glassdoor.com`, `ziprecruiter.com`, `myworkdayjobs.com`,
`boards.greenhouse.io`/`job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`,
`dice.com`, `wellfound.com`, and `builtin.com`; `source='Company'` searches the open web with
those domains excluded. All three tools drop URLs already in the user's database
(cross-run dedup via the run context — `skipped_known` in the payload).

### 0. `mcp__jobsearch__jobspy_search(search_term, time_period_days, results_wanted)`
* **Purpose**: Primary bulk discovery. One call scrapes Indeed, LinkedIn, Glassdoor, ZipRecruiter,
  and Google Jobs (US-scoped: `country_indeed="USA"`, `location="United States"`, `is_remote=True`,
  `hours_old` from the window) and returns structured, **pre-verified** records (title, company,
  url, location, date, salary, source, `pre_verified=true`, `us_eligible=true`). Scouts format
  these without WebFetch. On import/scrape failure it returns a clear message and the agent falls
  back to Exa/Tavily for those sources.

### 1. `mcp__jobsearch__exa_search(query, source)` / `mcp__jobsearch__tavily_search(query, source)`
* **Purpose**: Job discovery for the non-jobspy sources (ATS portals, Dice, Wellfound, Built In,
  Company) and jobspy fallback. Each scopes results to the assigned source's domain and the
  search window, returning a compact JSON list of candidate postings (`title, url, published_date,
  snippet`) annotated with `remote`/`full_time`/`posted_within_7d`/`us_eligible`. If a key is
  missing the tool returns a clear message and the agent falls back to `WebSearch`.

### 2. `WebSearch` (fallback)
* **Purpose**: Run targeted web queries scoped to the allowed sources (e.g.
  `Principal SRE remote site:linkedin.com/jobs`, `Principal SRE remote site:myworkdayjobs.com`,
  `Principal SRE remote site:boards.greenhouse.io`) when a search-API key is unavailable.

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
    source: str              # 'LinkedIn', 'Indeed', 'Glassdoor', 'ZipRecruiter', 'Workday', 'Greenhouse', 'Lever', 'Ashby', 'Dice', 'Wellfound', 'Built In', or 'Company'
    description: str          # Short job description summary

class JobList(BaseModel):
    jobs: list[JobItem]
```

Results are persisted to SQLite (`backend/db.py`), de-duplicated by URL (or by
`title|company|location` when the URL is missing), preserving each job's `applied` status
across re-runs.
