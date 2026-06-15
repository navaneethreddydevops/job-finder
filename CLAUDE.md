# CLAUDE.md

Guidance for Claude Code (and other AI agents) working in this repository.

## What this is

A full-stack **C2C (Corp-to-Corp) Job Finder**. An autonomous agent built on the
**Claude Agent SDK** crawls job boards for recently-posted Data Engineer C2C roles,
extracts them as structured JSON, and stores them in SQLite. A **FastAPI** backend
exposes the agent + data over REST/SSE, and a **Vite + React** dashboard renders the
results with live agent-thought streaming.

```
job-finder/
├── backend/
│   ├── agent.py        # Claude Agent SDK orchestrator + job_scout subagent, schemas
│   ├── main.py         # FastAPI app: /api/pull, /api/jobs, /api/stream (SSE), etc.
│   ├── db.py           # SQLite persistence (jobs.db) + de-duplication
│   ├── mcp_server.py   # FastMCP server exposing web_search + fetch_webpage_content
│   ├── diag.py         # Standalone smoke-test harness for the backend
│   └── jobs.db         # SQLite database (created at runtime)
├── frontend/
│   └── src/App.jsx     # Single-component dashboard (state, SSE, WebMCP tools)
├── .claude/launch.json # Preview server definitions (Frontend, Backend, MCP)
└── pyproject.toml      # Python deps (managed with uv)
```

## Running

Python is managed with **uv**. The backend, frontend, and MCP server are also defined
as preview servers in `.claude/launch.json`.

```bash
# Backend (FastAPI on :8000, serves built frontend if frontend/dist exists)
uv run uvicorn backend.main:app --reload --port 8000

# Frontend dev server (Vite on :5173, proxies /api to :8000)
cd frontend && npm run dev

# Production: build the frontend, then run only the backend
cd frontend && npm run build && cd .. && uv run python backend/main.py
```

## Authentication — OAuth only, never an API key

**The backend must authenticate to Claude exclusively via the stored Claude OAuth
credentials (`~/.claude`) that the `claude` CLI logs in with. It must never use an
Anthropic API key.** `backend/agent.py` unconditionally drops `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` from the environment at import time so the SDK-spawned `claude`
CLI falls back to its OAuth login. Do not reintroduce any API-key path. Apply the same
env drop in any new backend entrypoint/script (see `backend/diag.py`).

## Agent architecture

`run_job_finder_agent()` in `backend/agent.py` configures a `ClaudeSDKClient` as an
**orchestrator** plus a `job_scout` **subagent**:

- The orchestrator fans the search out by spawning one `job_scout` per source via the
  built-in **Task tool** — at minimum LinkedIn, Dice, Monster, Indeed, ZipRecruiter —
  running them in parallel, then merges and de-duplicates the results.
- `model=None` (inherits whatever model the `claude` CLI is configured with);
  `max_turns=80`; `permission_mode="bypassPermissions"`.
- **MCP servers** (`mcp_servers`): `puppeteer`
  (`@modelcontextprotocol/server-puppeteer`, headless browser) and `job_finder_tools`
  (local `mcp_server.py`).
- **Structured output** is enforced via `output_format=JobList.model_json_schema()`;
  the stream is also parsed for a ```json fenced block as a fallback.

### Pull as many fresh jobs as possible
There is **no upper limit** on job count — more is better. Do not reintroduce a fixed
target (the old "aim for 20–30" cap was removed).

### 24-hour freshness is a hard requirement
Only jobs **posted within the last 24 hours / on the run date** should be collected and
shown. This is enforced at every layer, so keep them in sync if you touch one:
1. **Agent** (`agent.py`): the run date is injected into the prompts; scouts use each
   board's last-24h filter (e.g. LinkedIn `f_TPR=r86400`, Indeed `fromage=1`) and set
   the `posted_within_24h` boolean on every job.
2. **DB** (`db.py`): `posted_within_24h` column (with an `ALTER TABLE` migration for
   older databases), persisted by `save_job`, returned as a bool by `get_all_jobs`.
3. **Frontend** (`App.jsx`): `fetchJobs` filters to `isWithin24h(job)` — trusts the
   backend flag first, with a free-text `date_posted` fallback.

## Custom MCP tools (`backend/mcp_server.py`, FastMCP)

- `web_search(query)` — DuckDuckGo text search, returns the top 8 results
  (title/URL/snippet).
- `fetch_webpage_content(url)` — fetches a page, strips boilerplate with BeautifulSoup,
  returns cleaned text capped at 4000 chars.

## Persistence (`backend/db.py`, SQLite `jobs.db`)

- De-duplication keys on the posting **URL**; when a job has no URL, a stable key is
  synthesized from `title|company|location` so URL-less jobs don't collide on the
  `UNIQUE(url)` constraint and collapse into one row.
- `save_job` preserves the existing `applied` status on update and returns `True` only
  when a new row was inserted.

## Backend API (`backend/main.py`)

- `POST /api/pull` `{query}` — starts the agent as a background task (rejects if already
  running).
- `GET /api/jobs` — all stored jobs.
- `GET /api/stream` — SSE stream of agent thoughts/tool calls/backend logs. On a DB
  write the backend emits a `Database now holds …` line that the UI uses to refresh.
- `GET /api/status`, `GET /api/health`, `PATCH /api/jobs/{id}/apply`, `POST /api/jobs/clear`.

## Conventions

- Keep documentation current: this file, `AGENTS.md`, and `README.md` should reflect the
  actual code. Update them when behavior changes.
- Frontend is intentionally a single `App.jsx` component; it also registers **WebMCP**
  tools (`document.modelContext`) so an in-browser agent can drive the dashboard.
