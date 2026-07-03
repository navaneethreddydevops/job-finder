# CLAUDE.md

Guidance for Claude Code (and other AI agents) working in this repository.

## What this is

A full-stack **Job Finder**. An autonomous agent built on the **Claude Agent SDK** researches
five sources — **LinkedIn (`linkedin.com/jobs`) and the ATS-hosted company careers portals
Workday (`*.myworkdayjobs.com`), Greenhouse (`boards.greenhouse.io` / `job-boards.greenhouse.io`),
Lever (`jobs.lever.co`), and Ashby (`jobs.ashbyhq.com`)** — for **remote, full-time** jobs posted
in the **last 7 days**, using parallel `job_scout` subagents. It searches the role the user types
as the Search Target (falling back to a default set of Principal-level platform/infra roles —
DevOps, Cloud, Kubernetes, SRE — only when the query is empty). Results are extracted as
structured JSON and stored in SQLite. A **FastAPI** backend exposes the agent + data over REST/SSE,
and a **Vite + React** dashboard renders the results with live agent-thought streaming.

```
job-finder/
├── backend/
│   ├── agent.py        # Claude Agent SDK orchestrator + job_scout subagent, schemas
│   ├── auth.py         # Auth router: users/sessions, login/register/profile/password
│   ├── resume.py       # Resume optimizer router: docx parse/generate + Claude call
│   ├── main.py         # FastAPI app: /api/pull, /api/jobs, /api/stream (SSE), etc.
│   ├── db.py           # Persistence (Neon Postgres via DATABASE_URL; SQLite test fallback) + de-duplication
│   └── diag.py         # Standalone smoke-test harness for the backend
├── frontend/
│   └── src/
│       ├── App.jsx         # Router root (BrowserRouter + protected routes)
│       ├── auth.jsx        # AuthContext + apiFetch bearer-token helper
│       ├── Dashboard.jsx   # Job dashboard (state, SSE, WebMCP tools)
│       └── pages/          # Login, Register, Profile, ResumeOptimizer
├── app_spec.md         # Spec for auth + resume optimizer + agent tools
├── .claude/launch.json # Preview server definitions (Frontend, Backend)
└── pyproject.toml      # Python deps (managed with uv)
```

## Running

Python is managed with **uv**. The backend and frontend are also defined as preview
servers in `.claude/launch.json`.

```bash
# Backend (FastAPI on :8000, serves built frontend if frontend/dist exists)
uv run uvicorn backend.main:app --reload --port 8000

# Frontend dev server (Vite on :5173, proxies /api to :8000)
cd frontend && npm run dev

# Production: build the frontend, then run only the backend
cd frontend && npm run build && cd .. && uv run python backend/main.py
```

## Cloud deployment — Vercel + FastAPI Cloud + Neon

Two services over HTTPS, Postgres on Neon. Frontend uses Git-based auto-deploy; backend deploys
via FastAPI Cloud CLI. See README "Cloud Deployment" for steps.

- **Frontend → Vercel.** [`frontend/vercel.json`](frontend/vercel.json) (Vite preset + SPA
  rewrites), Root Directory = `frontend`. The frontend reaches the backend **cross-origin** via
  `VITE_API_BASE_URL` (baked in at build time). All API calls go through `apiUrl()`/`apiFetch()`
  in `frontend/src/auth.jsx` — `apiUrl(path)` prepends the base; `apiFetch` also attaches the
  bearer token. **Never reintroduce raw `fetch('/api/...')` or `new EventSource('/api/...')`**;
  use the helpers so the app works both behind the Vite proxy (local) and cross-origin (prod).
  Auth-protected endpoints (`/api/jobs`, `/api/pull`, `/api/jobs/clear`, `/api/jobs/{id}/apply`)
  must use `apiFetch`; open ones (`/api/status`, `/api/health`, `/api/stream`) use `apiUrl`.
- **Backend → FastAPI Cloud.** [`fastapi-cloud.yml`](fastapi-cloud.yml) defines the deployment
  config. Deploy with `fastapi deploy` CLI (part of `fastapi[standard]` in dependencies). Auth
  stays **OAuth-only**: set `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) as a secret in
  the FastAPI Cloud dashboard under Project Settings → Secrets — the SDK/CLI honor it and
  `agent.py` does not drop it. Never an API key. FastAPI Cloud auto-detects `backend/main.py`.
- **Database → Neon.** Set `DATABASE_URL` in the FastAPI Cloud dashboard Secrets section (include
  `?sslmode=require`). Secrets are never committed to git — set them in the dashboard only.

## Authentication — OAuth only, never an API key

**The backend must authenticate to Claude exclusively via the stored Claude OAuth
credentials (`~/.claude`) that the `claude` CLI logs in with. It must never use an
Anthropic API key.** `backend/agent.py` unconditionally drops `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` from the environment at import time so the SDK-spawned `claude`
CLI falls back to its OAuth login. Do not reintroduce any API-key path. Apply the same
env drop in any new backend entrypoint/script (see `backend/diag.py`).

## Agent architecture

`run_job_finder_agent(query)` in `backend/agent.py` configures a `ClaudeSDKClient` as an
**orchestrator** plus a `job_scout` **subagent**:

- The orchestrator searches ONLY the user's Search Target query as the role; `DEFAULT_ROLES`
  (Principal DevOps / Cloud / Kubernetes / Site Reliability Engineer) are used solely as a
  fallback when the query is empty. It
  searches every **role × source** pair itself with the Exa/Tavily tools (in-process SDK MCP
  tools can't be granted to subagents) — **LinkedIn plus the Workday/Greenhouse/Lever/Ashby
  careers portals only** (no Glassdoor/Dice/Monster/Indeed/ZipRecruiter) — then spawns
  `job_scout` subagents in parallel (via the built-in **Task** tool) to verify + format batches
  of 30-40 candidates, and finally merges and de-duplicates the results.
- **Tools granted to both agents** (`AGENT_ALLOWED_TOOLS` and `SCOUT_ALLOWED_TOOLS`):
  - **File operations**: `Read`, `Write`, `Edit` — for processing and storing job data
  - **System operations**: `Bash`, `Glob`, `Grep` — for data processing and filtering
  - **Job search APIs**: `mcp__jobsearch__exa_search`, `mcp__jobsearch__tavily_search` — the
    primary job-discovery tools (Exa + Tavily), via an in-process SDK MCP server (`backend/search_tools.py`)
  - **Web operations**: `WebSearch`, `WebFetch` — fallback search + reading individual listings
  - **Agent control**: `Task` (orchestrator only), `TodoWrite` — for orchestration and planning
  - The only MCP integration is the in-process `jobsearch` server (Exa + Tavily). No external MCP servers.
- `model="claude-sonnet-5"` for both the orchestrator and the `job_scout` subagent (the resume
  optimizer in `resume.py` uses the same model). Sonnet is chosen for stronger structured-output
  parsing and smarter tool use; `permission_mode="bypassPermissions"`.
- **Structured output** is enforced via `output_format=JobList.model_json_schema()`. If
  `msg.structured_output` is absent, the final `msg.result` is parsed with the tolerant
  `_extract_jobs_from_text` helper (handles ```json fences, **bare/unfenced arrays**,
  `{"jobs": [...]}` wrappers, and JSON embedded in prose) — do **not** narrow this back to a
  single fenced-object regex, which silently dropped whole runs that returned a bare array.
  As a final safety net, every job parsed from a scout result is accumulated (de-duped by URL);
  if the orchestrator's final message yields no parseable list, those collected jobs are returned
  and saved, so a run that visibly found jobs never persists nothing.
- **Remote, full-time only**: The agent keeps only remote full-time (FTE) roles and excludes
  non-remote, contract, temporary, internship, and part-time roles.

### User-driven role + fixed sources — LinkedIn plus ATS careers portals
The agent researches the user's Search Target query as the only role; `DEFAULT_ROLES` in
`agent.py` (Principal DevOps / Cloud / Kubernetes / Site Reliability Engineer) are a fallback
used only when the query is empty — never added on top of a typed query. Sources are fixed to
`SEARCH_SOURCES = ["LinkedIn", "Workday", "Greenhouse", "Lever", "Ashby"]` — **LinkedIn
(`linkedin.com/jobs`) and the ATS-hosted careers portals Workday (`*.myworkdayjobs.com`),
Greenhouse (`boards.greenhouse.io` / `job-boards.greenhouse.io`), Lever (`jobs.lever.co`), and
Ashby (`jobs.ashbyhq.com`)** — all direct employer postings with reliable dates. Do not
reintroduce aggregator boards (Glassdoor, Dice, Monster, Indeed, ZipRecruiter): stale reposts,
scrape-hostile, unreliable dates. The source list is intentionally fixed in
`agent.py`'s scout prompt and run prompt. The `source` field is one of `'LinkedIn'`, `'Workday'`,
`'Greenhouse'`, `'Lever'`, or `'Ashby'`.

### Pull as many fresh roles as possible
There is **no upper limit** on job count — more is better. Do not reintroduce a fixed
target (the old "aim for 20–30" cap was removed). The orchestrator fans out one subagent per
role × source for broad coverage.

### Remote + full-time + 7-day freshness is a hard requirement
Only **remote, full-time jobs posted within the last 7 days** should be collected and shown. This is
enforced at every layer, so keep them in sync if you touch one:
1. **Agent** (`agent.py`): the run date and a 7-day `since_date` are injected into the prompts;
   scouts verify the role is remote + full-time and use each source's last-7-days filter (e.g. LinkedIn
   `f_TPR=r604800` + remote `f_WT=2`, Workday 'posted in the last week'), then set the
   `posted_within_7d` boolean on every job.
2. **DB** (`db.py`): `posted_within_7d` column (with a `RENAME COLUMN` migration from the legacy
   `posted_within_24h`), persisted by `save_job`, returned as a bool by `get_user_jobs`.
3. **Frontend** (`Dashboard.jsx`): `fetchJobs` filters to `isWithinWindow(job)` — trusts the
   backend `posted_within_7d` flag first, then a free-text `date_posted` fallback measured against
   the selected `timePeriodDays` (not a hardcoded 7). **Keeps by default**: a job with a missing or
   unparseable `date_posted` is retained (the scouts already constrained the search window at the
   source), and is dropped only when its date positively proves it's older than the window. Do not
   revert this to a drop-by-default filter — that silently hid every job when the backend flag was
   absent (`posted_within_7d=0`, `date_posted=NULL`).

## Search tooling — Exa + Tavily (`backend/search_tools.py`)

Job discovery is done via the **Exa** and **Tavily** search APIs, exposed to the agent as
**in-process SDK MCP tools** (`create_sdk_mcp_server` → server name `jobsearch`):

- `mcp__jobsearch__exa_search(query, source)` and `mcp__jobsearch__tavily_search(query, source)` —
  each scopes results to the assigned source's domain(s) (`linkedin.com`, `myworkdayjobs.com`,
  `boards.greenhouse.io`/`job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`),
  enforces the last-7-days window, and returns a compact JSON list of candidate postings
  (`title, url, published_date, snippet`). These are the **primary** discovery tools and the reason
  recall is high (the built-in `WebSearch` alone returned too few results).
- **Keys**: read from env `EXA_API_KEY` / `TAVILY_API_KEY` (never hardcoded; in `.env` locally,
  FastAPI Cloud Secrets in prod). If a key is missing, the tool returns a clear message and the agent
  falls back to `WebSearch`.
- `WebSearch` — fallback web search when a search API key is unavailable.
- `WebFetch` — opens and reads individual listings to verify dates, that the role is remote + full-time, and extract fields.

Do not add aggregator boards to `search_tools.py`'s domain map — only the LinkedIn +
Workday/Greenhouse/Lever/Ashby domains in `ALL_SOURCE_DOMAINS` are allowed.

## Persistence (`backend/db.py` — dual backend: SQLite local, Postgres/Neon prod)

- **`db.py` supports two backends transparently.** The primary database is **Neon Postgres**,
  selected when `DATABASE_URL` is a `postgres://`/`postgresql://` string (set in `.env` locally
  and as a Render secret in prod) via `psycopg`. Without `DATABASE_URL` it falls back to a local
  SQLite file (`backend/jobs.db`) — kept **only for tests/`diag.py`**; the file is git-ignored,
  not part of the repo, and gets auto-created empty if the app boots without `DATABASE_URL`
  (if the dashboard ever shows zero jobs unexpectedly, check that `.env` is being loaded). The exported `IS_POSTGRES`, `AUTO_PK`,
  `BLOB_TYPE` constants and `insert_returning_id()` helper absorb the dialect differences, and a
  thin connection wrapper translates `?` placeholders to `%s` (escaping literal `%`, so
  `LIKE '%…%'` is safe) and returns rows that support **both** dict-style (`row["col"]`) and
  positional (`row[0]`, tuple-unpacking) access, matching `sqlite3.Row`. **Keep new SQL
  `?`-style and route inserts-needing-an-id through `insert_returning_id`** — do not hardcode
  `lastrowid`, `AUTOINCREMENT`, `PRAGMA`, `BLOB`, SQLite date functions (`datetime('now', …)` —
  compute cutoffs in Python and pass as params), `BOOLEAN DEFAULT 1` (use `TRUE`), or
  `INSERT OR REPLACE` (use `ON CONFLICT(…) DO UPDATE`, portable to both). All router modules
  follow this (DDL uses `AUTO_PK`/`BLOB_TYPE`; migrations branch on `IS_POSTGRES`). Postgres
  also enforces FK targets at `CREATE TABLE` time (SQLite doesn't): the shared `users` DDL
  lives in `db.ensure_users_table()` (called by both `init_db` and `auth.init_auth_db`), and
  modules that self-init at import with FKs to `jobs`/`applications` call `init_db()` first.
  Tables auto-create on boot, so a fresh Neon DB needs no manual migration.
- De-duplication keys on the posting **URL**; when a job has no URL, a stable key is
  synthesized from `title|company|location` so URL-less jobs don't collide on the
  `UNIQUE(url)` constraint and collapse into one row.
- `save_job` preserves the existing `applied` status on update and returns `True` only
  when a new row was inserted.

## Backend API (`backend/main.py`)

- `POST /api/pull` `{query}` — starts the agent as a background task (rejects if already
  running). Jobs are persisted **incrementally in small batches as each `job_scout`
  finishes** (via `run_job_finder_agent`'s `batch_callback`), so the dashboard fills in
  progressively without waiting for the whole run; the agent's final merged list is saved
  at the end as a de-duplicating reconciliation pass. See `app_spec.md` Task 5.
- `GET /api/jobs` — all stored jobs.
- `GET /api/stream` — SSE stream of agent thoughts/tool calls/backend logs. On a DB
  write the backend emits a `Database now holds …` line that the UI uses to refresh.
  The current run's lines are also buffered in an in-memory `log_history` (bounded by
  `LOG_HISTORY_MAX`, cleared at the start of each `/api/pull`); a client that connects
  mid-run — e.g. after a browser refresh — is replayed that buffer before live streaming,
  so refreshing never loses the in-flight agent console. The frontend's mount effect polls
  `/api/status`, and if a run is active it reconnects and lets the replay repopulate `logs`.
  **Memory bounds — keep them:** each SSE client's queue is bounded (`LOG_QUEUE_MAX`,
  published with `put_nowait` + drop-oldest so a stalled client can't buffer a whole run in
  RAM or block the agent), and `Dashboard.jsx` caps its `logs` state at `LOG_LINES_MAX`
  (mirrors `LOG_HISTORY_MAX`). Don't revert either to unbounded.
- `GET /api/status`, `GET /api/health`, `PATCH /api/jobs/{id}/apply`, `POST /api/jobs/clear`.

## Agent tools (Task 1)

`backend/agent.py` declares `AGENT_ALLOWED_TOOLS` and passes it to
`ClaudeAgentOptions(allowed_tools=...)` along with `mcp_servers={"jobsearch": job_search_server}`.
It grants the built-in toolset from the
[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) — `Read`,
`Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `Task`, `TodoWrite` — **plus**
the Exa/Tavily search tools (`mcp__jobsearch__exa_search`, `mcp__jobsearch__tavily_search`) from the
in-process `jobsearch` SDK MCP server. The `job_scout` subagent gets the same search tools plus
`WebSearch`/`WebFetch` (its `AgentDefinition.tools` = `SCOUT_ALLOWED_TOOLS`).

## Authentication (`backend/auth.py`)

Email/password auth backed by the same SQLite DB. Username **is** the email; passwords are
**≥ 8 chars**, hashed with stdlib `pbkdf2_hmac` (no external crypto deps). Bearer tokens
live in `auth_sessions`. A test user `test@test.com` / `testtest` is seeded on startup.
Endpoints (all under `/api`): `register`, `login`, `token`, `logout`, `me`, `profile` (PATCH),
`change-password`. Protected routes depend on `get_current_user`, which uses an
`OAuth2PasswordBearer` scheme (`tokenUrl=api/token`, `auto_error=False`) so **Swagger `/docs`
renders an Authorize button** — sign in there with the seeded `test@test.com` / `testtest`
account to exercise protected endpoints. `get_current_user` prefers the OAuth2-extracted token
but falls back to the raw `Authorization: Bearer` header, so the frontend's `apiFetch` is
unaffected. `/api/token` is an OAuth2 password-form endpoint (username = email) returning the
same bearer token as `/api/login`; the FastAPI app `description` surfaces the test credentials
at the top of `/docs`. The frontend stores `{token, user}` in `localStorage` (`jf_auth`) via
`auth.jsx` and attaches the bearer header through the `apiFetch` helper; React Router guards
`/`, `/profile`, `/resume/optimizer`.

## Resume Optimizer (`backend/resume.py`)

Route `/resume/optimizer` (frontend) + `/api/resume/*` (backend). Split-pane UI: top chat
takes a job description; left pane previews/edits the existing `.docx` resume; right pane is a
structured editor of the Claude-optimized result, downloadable as a clean `.docx`.

- **Preserve-and-augment + diff.** The optimizer returns **structured content**
  (`{summary, sections:[{title, items:[{text, is_new}]}]}`): every original point is kept
  (`is_new=false`) and JD-tailored points are added (`is_new=true`). The UI highlights `is_new`
  items (green + "NEW" badge) — that's the diff. Stored in `resume_jobs.result_json`.
- **Both sides editable.** Left pane Preview/Edit toggle (edit = textarea of extracted text,
  sent back via the `original_text` form field on re-optimize). Right pane structured editor
  (edit titles/bullets, add/remove). Edits saved via `PUT /api/resume/content`, which rebuilds
  the downloadable `.docx`.
- **Fit & clean output.** `docx-preview` renders with `ignoreWidth` so the page fits the pane.
  The downloaded `.docx` is produced by the **`docx` skill** (bundled at `.claude/skills/docx/`
  and enabled on the resume agent's `ClaudeAgentOptions` via `skills=["docx"]` +
  `setting_sources=["project"]`); `build_docx_from_content` (US-Letter, Arial, heading styles,
  real bullet lists) is the deterministic python-docx fallback. The diff lives in the UI, not
  the file.
- **State.** Persisted per user in `resume_jobs` (latest job wins) + `localStorage`
  (`jf_resume_state`, includes edited content) so a refresh restores progress and edits.

Endpoints: `POST /api/resume/optimize` (multipart: `job_description` + `original_text` or
`resume` file; background task), `GET /api/resume/status` (progress bar),
`GET /api/resume/result` (`{content, original_text, ...}`), `PUT /api/resume/content`
(save edits + rebuild), `GET /api/resume/download`. See `app_spec.md` for full details.

## Development workflow — follow this for ALL future work

This repo follows the same development methodology as Anthropic's
[autonomous-coding quickstart](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding).
Apply these steps to every feature or change, in order:

1. **Spec first — `app_spec.md` is the source of truth.** Before writing any code, add or
   update the relevant section in `app_spec.md` (data model, endpoints, UX, dependencies).
   Never implement first and document later. The spec is what you build against.
2. **Define a feature checklist.** Break the work into a numbered list of concrete,
   independently verifiable features/acceptance criteria (the equivalent of the quickstart's
   `feature_list.json`). Track each as ☐ todo / ☑ done. Use the `TaskCreate`/`TaskUpdate`
   tools for in-session tracking; record durable cross-session status in `app_spec.md`.
3. **Implement incrementally.** Build one feature at a time. Keep changes small and focused;
   don't bundle unrelated work into one pass.
4. **Verify before marking done — test-driven, never assume.** A feature is "done" only
   after it's been exercised and observed to work:
   - **Backend:** drive endpoints with FastAPI `TestClient` (mock the Claude call when it
     would hit the network) and confirm status codes + payloads.
   - **Frontend:** use the preview workflow (`preview_start` → `preview_snapshot` /
     `preview_screenshot` / `preview_console_logs`). Verify, don't ask the user to check.
   - Clean up any throwaway test data you created (see how the seeded `test@test.com` user
     is kept but ad-hoc rows are deleted).
5. **Keep docs in sync.** Update `app_spec.md`, this file (`CLAUDE.md`), `AGENTS.md`, and
   `README.md` whenever behavior changes — same change, same commit.
6. **Commit per milestone** (only when the user asks). Each commit should leave the app in a
   working, verified state, so progress is transparent and rollback is cheap.

### Architectural principles (mirror the quickstart)

- **Clean module separation.** One concern per module. New backend features get their own
  router module (`auth.py`, `resume.py`) wired into `main.py` via `include_router`, never
  piled into `main.py`. New frontend pages live in `frontend/src/pages/` and are added to the
  router in `App.jsx`; shared logic (e.g. auth) lives in its own module (`auth.jsx`).
- **Least-privilege / security by default.** Grant tools explicitly via `allowed_tools`
  (Task 1), validate all inputs, hash secrets, and never widen permissions without reason.
- **OAuth only** for any Claude call — drop `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` at
  import time in every backend entrypoint (see `agent.py`, `resume.py`, `diag.py`).
- **Persistent, resumable state.** Long-running / async work persists progress to SQLite and
  (where relevant) `localStorage` so a refresh or restart restores it — as the resume
  optimizer's `resume_jobs` + `jf_resume_state` do. Prefer this over in-memory-only state.

### Working with Word documents — use the `docx` skill

The **`docx` skill is bundled into this repo as a project skill** at `.claude/skills/docx/`
(`SKILL.md` + `scripts/`) and is **wired into the Agent SDK**: the resume optimizer's
`ClaudeAgentOptions` set `cwd=REPO_ROOT`, `setting_sources=["project"]`, `skills=["docx"]`,
and grant the `Skill` tool (plus `Read`/`Write`/`Bash`/`Glob`/`Grep`) so the running agent can
produce Word documents with the skill at runtime. The resume agent writes the polished `.docx`
to a temp path; if the skill path is unavailable, `build_docx_from_content` (python-docx) is the
deterministic fallback. Keep `.claude/skills/docx/` in the repo (committed) — removing it breaks
the skill path.

When you (the coding agent) implement or modify any Word feature, also invoke the
**`docx` skill** (`anthropic-skills:docx`) and follow its guidance — don't hand-roll Word
handling ad hoc.

## Conventions

- Keep documentation current: `app_spec.md`, this file, `AGENTS.md`, and `README.md` should
  reflect the actual code. Update them when behavior changes.
- Frontend is a React Router app: `App.jsx` is the router root, `Dashboard.jsx` is the main
  job dashboard, and `pages/` holds Login, Register, Profile, and ResumeOptimizer. The
  dashboard also registers **WebMCP** tools (`document.modelContext`) so an in-browser agent
  can drive it. The heavy pages — ResumeOptimizer (`docx-preview`), Analytics (`recharts`),
  Settings — are **`React.lazy` route chunks** (see `App.jsx`); keep new heavy-dependency
  pages lazy too so the dashboard bundle stays small.
- **Design system — Notion-inspired light theme.** All styling lives in
  `frontend/src/index.css` as the single source of truth, driven by `:root` CSS custom
  properties (`--primary`, `--text-*`, `--border`, `--*-glow`, …) and semantic class names
  shared across every page; component JSX references these tokens (incl. inline styles), so
  re-theme by editing the variables/classes rather than the components. Typography is all-sans
  for a clean Notion feel: both `--font-sans` and `--font-heading` resolve to Inter (JetBrains
  Mono for code) loaded via CDN in `frontend/index.html`. Keep it light, flat,
  and restrained (hairlines, soft shadows, small radii). When changing the look, update
  `app_spec.md` Task 4 and keep element `id`s intact (WebMCP/agent tooling depends on them).
