# CLAUDE.md

Guidance for Claude Code (and other AI agents) working in this repository.

## What this is

A full-stack **Job Finder**. An autonomous agent built on the **Claude Agent SDK** crawls
six job boards (LinkedIn, Dice, Monster, Indeed, Glassdoor, ZipRecruiter) for recently-posted jobs
matching a user-provided search query, extracts them as structured JSON, and stores them
in SQLite. A **FastAPI** backend exposes the agent + data over REST/SSE, and a **Vite + React**
dashboard renders the results with live agent-thought streaming.

```
job-finder/
├── backend/
│   ├── agent.py        # Claude Agent SDK orchestrator + job_scout subagent, schemas
│   ├── auth.py         # Auth router: users/sessions, login/register/profile/password
│   ├── resume.py       # Resume optimizer router: docx parse/generate + Claude call
│   ├── main.py         # FastAPI app: /api/pull, /api/jobs, /api/stream (SSE), etc.
│   ├── db.py           # SQLite persistence (jobs.db) + de-duplication
│   ├── diag.py         # Standalone smoke-test harness for the backend
│   └── jobs.db         # SQLite database (jobs, users, resume_jobs)
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

## Cloud deployment — Vercel + Render + Neon

Two services over HTTPS, Postgres on Neon, deployed via **native Git integration** (push to
`master` auto-deploys; no deploy secrets in the repo). See README "Cloud Deployment" for steps.

- **Frontend → Vercel.** [`frontend/vercel.json`](frontend/vercel.json) (Vite preset + SPA
  rewrites), Root Directory = `frontend`. The frontend reaches the backend **cross-origin** via
  `VITE_API_BASE_URL` (baked in at build time). All API calls go through `apiUrl()`/`apiFetch()`
  in `frontend/src/auth.jsx` — `apiUrl(path)` prepends the base; `apiFetch` also attaches the
  bearer token. **Never reintroduce raw `fetch('/api/...')` or `new EventSource('/api/...')`**;
  use the helpers so the app works both behind the Vite proxy (local) and cross-origin (prod).
  Auth-protected endpoints (`/api/jobs`, `/api/pull`, `/api/jobs/clear`, `/api/jobs/{id}/apply`)
  must use `apiFetch`; open ones (`/api/status`, `/api/health`, `/api/stream`) use `apiUrl`.
- **Backend → Render.** [`render.yaml`](render.yaml) Blueprint + [`backend/Dockerfile.render`](backend/Dockerfile.render),
  which installs Python deps **plus Node.js + the Claude Code CLI** (the SDK spawns `claude`).
  Auth stays **OAuth-only**: set `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) as a Render
  secret — the SDK/CLI honor it and `agent.py` does not drop it. Never an API key. Binds `$PORT`.
- **Database → Neon.** Set `DATABASE_URL` on Render (see Persistence above). `.claude/` is excluded
  from the Docker build context, so the resume optimizer uses its python-docx fallback in prod.

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

- The orchestrator accepts a dynamic search query from the user (e.g. "Data Engineer", "Senior Python Developer")
  and fans the search out by spawning one `job_scout` per source via the built-in **Task tool** —
  LinkedIn, Dice, Monster, Indeed, Glassdoor, and ZipRecruiter — running them in parallel, then merges and de-duplicates
  the results.
- **Tools granted to both agents** (`AGENT_ALLOWED_TOOLS` and `SCOUT_ALLOWED_TOOLS`):
  - **File operations**: `Read`, `Write`, `Edit` — for processing and storing job data
  - **System operations**: `Bash`, `Glob`, `Grep` — for data processing and filtering
  - **Web operations**: `WebSearch`, `WebFetch` — for discovering and reading job listings
  - **Agent control**: `Task` (orchestrator only), `TodoWrite` — for orchestration and planning
  - There is **no MCP integration**.
- `model=None` (inherits whatever model the `claude` CLI is configured with);
  `max_turns=80`; `permission_mode="bypassPermissions"`.
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

## Web tooling (built-in, no MCP)

The agent uses Claude's built-in web tools directly — no MCP servers are configured.

- `WebSearch` — targeted web queries using the user's search term (e.g. `<query> site:linkedin.com`) with each
  board's last-24h recency filter.
- `WebFetch` — opens and reads individual listings to verify dates and extract fields.

## Persistence (`backend/db.py` — dual backend: SQLite local, Postgres/Neon prod)

- **`db.py` supports two backends transparently.** Default is SQLite (`jobs.db`) for local
  dev and tests. When `DATABASE_URL` is a `postgres://`/`postgresql://` string (e.g. Neon on
  Render), it switches to Postgres via `psycopg`. The exported `IS_POSTGRES`, `AUTO_PK`,
  `BLOB_TYPE` constants and `insert_returning_id()` helper absorb the dialect differences, and a
  thin connection wrapper translates `?` placeholders to `%s` so the rest of the code stays
  backend-agnostic. **Keep new SQL `?`-style and route inserts-needing-an-id through
  `insert_returning_id`** — do not hardcode `lastrowid`, `AUTOINCREMENT`, `PRAGMA`, or `BLOB`.
  `auth.py` and `resume.py` follow this (DDL uses `AUTO_PK`/`BLOB_TYPE`; migrations branch on
  `IS_POSTGRES`). Tables auto-create on boot, so a fresh Neon DB needs no manual migration.
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
- `GET /api/status`, `GET /api/health`, `PATCH /api/jobs/{id}/apply`, `POST /api/jobs/clear`.

## Agent tools (Task 1)

`backend/agent.py` declares `AGENT_ALLOWED_TOOLS` and passes it to
`ClaudeAgentOptions(allowed_tools=...)`. It grants the full built-in toolset from the
[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) — `Read`,
`Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`, `WebFetch`, `Task`, `TodoWrite`.
There is **no MCP integration**; the agent relies solely on the built-in `WebSearch` and
`WebFetch` web tools. The `job_scout` subagent keeps its narrower toolset (`WebSearch`,
`WebFetch`).

## Authentication (`backend/auth.py`)

Email/password auth backed by the same SQLite DB. Username **is** the email; passwords are
**≥ 8 chars**, hashed with stdlib `pbkdf2_hmac` (no external crypto deps). Bearer tokens
live in `auth_sessions`. A test user `test@test.com` / `testtest` is seeded on startup.
Endpoints (all under `/api`): `register`, `login`, `logout`, `me`, `profile` (PATCH),
`change-password`. Protected routes depend on `get_current_user`. The frontend stores
`{token, user}` in `localStorage` (`jf_auth`) via `auth.jsx` and attaches the bearer header
through the `apiFetch` helper; React Router guards `/`, `/profile`, `/resume/optimizer`.

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
  can drive it.
- **Design system — Notion-inspired light theme.** All styling lives in
  `frontend/src/index.css` as the single source of truth, driven by `:root` CSS custom
  properties (`--primary`, `--text-*`, `--border`, `--*-glow`, …) and semantic class names
  shared across every page; component JSX references these tokens (incl. inline styles), so
  re-theme by editing the variables/classes rather than the components. Fonts (Inter UI + Lora
  serif display + JetBrains Mono) load via CDN in `frontend/index.html`. Keep it light, flat,
  and restrained (hairlines, soft shadows, small radii). When changing the look, update
  `app_spec.md` Task 4 and keep element `id`s intact (WebMCP/agent tooling depends on them).
