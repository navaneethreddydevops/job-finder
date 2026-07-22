# AI Job Finder Application

A full-stack, Claude-powered dashboard that researches LinkedIn, Indeed, Glassdoor, ZipRecruiter, the Workday/Greenhouse/Lever/Ashby careers portals, Dice, Wellfound, Built In, and employer career pages for **remote, full-time US** roles posted in the **last 7 days** (narrowing automatically to "since the last run" on repeat searches). It features an autonomous agent using the **Claude Agent SDK**, a **FastAPI backend**, and a premium **React UI** complete with a real-time console thought-logger.

---

## Features
* **Multi-agent Job Finder**: An orchestrator searches the **career portals first — Workday (`*.myworkdayjobs.com`), Greenhouse (`boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`), and open-web company career pages** — with the Exa/Tavily tools (highest priority: these link straight to the employer's apply page, and portal jobs sort to the top of the dashboard). It then bulk-scrapes **Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google Jobs** with one structured `jobspy_search` call per role (free, pre-verified results — no page reading), and finally covers **Dice, Wellfound, and Built In**. It hands batches of candidates to parallel `job_scout` subagents (claude-haiku-4-5, via the Task tool) for verification and formatting, then merges and de-duplicates the results. Only remote, full-time roles open to US-based candidates with a valid direct posting URL are kept.
* **Incremental, checkpointed pulls**: jobs are saved in batches as each scout finishes; a per-user/per-query checkpoint narrows the next run's search window to "since the last successful run", and the search tools automatically skip job URLs already in the database (`skipped_known`) — repeat runs never waste tokens re-verifying known jobs.
* **User-driven target role**: Every run searches the role typed as the Search Target; the default Principal DevOps / Cloud / Kubernetes / SRE roles are searched only when the query is empty.
* **Model picker**: A "Model" control in Agent Controls selects which Claude model runs the orchestrator — Fable 5, Opus 4.8, Sonnet 5 (default), or Haiku 4.5 — persisted per browser and validated server-side. The `job_scout` subagents always run on Haiku 4.5.
* **Remote + full-time only**: Collects only remote, full-time (FTE) roles; non-remote, contract, temporary, part-time, and internship postings are skipped.
* **Fresh-only results**: Collects and displays **only jobs posted within the last 7 days**.
* **No volume cap**: Pulls as many matching full-time roles as it can find.
* **Incremental saving**: Jobs are written to the database in small batches as each scout finishes, so the dashboard fills in progressively instead of waiting for the whole run to complete.
* **Exa + Tavily + SerpAPI search**: Scouts discover listings via the Exa and Tavily search APIs (in-process SDK MCP tools `exa_search` / `tavily_search`, keys from `EXA_API_KEY` / `TAVILY_API_KEY`), read them with `WebFetch`, and fall back to the built-in `WebSearch` if a key is missing. `serpapi_search` (key from `SERPAPI_API_KEY`) adds bulk pre-verified Google Jobs coverage via SerpAPI, supplementing JobSpy and serving as its first fallback.
* **Live thought console**: Streams agent reasoning and tool calls to the browser over Server-Sent Events.
* **Notion-inspired React Dashboard**: A clean, editorial light UI (serif display headings, hairline borders, soft shadows, Notion-style tag colors) with statistics, search, multi-selection filters (Remote vs Onsite, Job Sources, Applied), and a details drawer. See the redesign spec in [app_spec.md](app_spec.md) (Task 4).
* **Authentication**: Email/password login & registration (8+ char passwords), profile editing, and password change. Seeded test user `test@test.com` / `testtest`. Protected endpoints use an OAuth2 bearer scheme, so the interactive Swagger docs at `/docs` have an **Authorize** button — sign in with the test credentials to try them out.
* **Resume Optimizer** (`/resume/optimizer`): Split-pane tool — paste a job description, drop in your existing Word resume (previewed in-browser), and Claude generates a tailored, downloadable `.docx`. Includes a progress bar and refresh-safe persisted state.
* **Application-profile onboarding** (`/onboarding`): A skippable 8-step wizard (shown after registration, reopenable from the Profile page) collects everything careers pages ask for — contact + address, LinkedIn/GitHub/portfolio links, **work authorization & sponsorship answers**, salary/availability preferences, experience & education, optional EEO self-identification (defaults to "Decline to self-identify"), and a resume upload (`.docx`/`.pdf`, text auto-extracted). Everything stays editable under **Profile → Application Profile**.
* **Autonomous Auto-Apply**: An **Auto-Apply** button on each job launches a Claude agent that opens the posting in a **headless browser (Playwright MCP)**, fills the employer's application form from your saved profile, uploads your resume, answers screening questions, and submits — then marks the job applied and advances the application to `applied`. It never fabricates answers and never guesses legally significant questions (work authorization, EEO, clearance…): login walls and CAPTCHAs end the run as **needs review** with the reason and a screenshot in the job's details dialog. When a form asks for an **email verification code** (e.g. Greenhouse) or a required question your profile can't answer, the agent **pauses with the browser session alive** — the dashboard shows a "Needs your input" prompt, you paste the code from your inbox, and the run resumes and submits. While it works you get a **live view** in the job's details dialog that shows *what the agent is doing in the browser*: a **milestone stepper** (open form → fill details → upload resume → ready to submit → confirmation), a running **step-by-step timeline** of each browser action, and — in production — a **true live video stream** of the headless browser (a real-time canvas feed of the agent clicking and typing, via a CDP screencast from the Playwright MCP sidecar; local dev falls back to milestone screenshots). Up to **3 agents can run at once** (one per job — a "N agents applying" chip tracks them). Requires a complete profile (the button walks you to onboarding otherwise) and a headless browser: **Node/`npx`** locally, or in production the **Playwright MCP sidecar** (`deploy/playwright-mcp/`) reached via `PLAYWRIGHT_MCP_URL`/`PLAYWRIGHT_MCP_TOKEN`; with neither configured the button is disabled and the manual apply link remains.
* **Unified Server**: Serves the static production React build directly from the Python backend (with SPA fallback for client-side routes).

---

## Folder Structure
```
job-finder/
├── backend/               # Python Backend
│   ├── agent.py           # Claude Agent SDK orchestrator + job_scout subagent, schemas
│   ├── auth.py            # Auth: users/sessions, login/register/profile/password
│   ├── resume.py          # Resume optimizer: docx parse/generate + Claude call
│   ├── main.py            # FastAPI server (REST + SSE), wires auth + resume routers
│   └── db.py              # Persistence (Neon Postgres via DATABASE_URL) + de-duplication
├── frontend/              # Vite React Project
│   ├── src/
│   │   ├── App.jsx        # Router root (BrowserRouter + protected routes)
│   │   ├── auth.jsx       # AuthContext + apiFetch bearer-token helper
│   │   ├── Dashboard.jsx  # Job dashboard (formerly App.jsx)
│   │   ├── pages/         # Login, Register, Profile, ResumeOptimizer
│   │   └── index.css      # CSS Design System
│   └── vite.config.js     # Dev server proxy configuration
├── app_spec.md            # Spec for auth + resume optimizer + agent tools
├── CLAUDE.md              # Guidance for AI agents working in this repo
├── AGENTS.md              # Agent design, tools, and response schema
├── pyproject.toml         # Python Dependencies (managed with uv)
└── README.md              # Project Documentation
```

---

## Setup & Running Instructions

### 1. Authentication — Claude OAuth (no API key)
This project authenticates to Claude through the **Claude CLI's stored OAuth login**, not
an API key. Make sure you're logged in once:
```bash
claude login
```
The backend deliberately ignores `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` and always
uses the OAuth credentials in `~/.claude`. No `.env` API key is required.

### 2. Single-Process Production Start (Quickest)
This option runs the FastAPI backend and serves the compiled React production bundle on a single port.

1. **Build the React Frontend**:
   ```bash
   cd frontend
   npm run build
   cd ..
   ```
2. **Launch the Server**:
   ```bash
   uv run python backend/main.py
   ```
3. Open **`http://127.0.0.1:8000`** in your browser.

---

### 3. Concurrent Development Servers (Live Frontend Reloading)
If you wish to make live modifications to React code or styles:

1. **Start the Backend API**:
   ```bash
   uv run python backend/main.py
   ```
2. **Start the Frontend Dev Server** (in a separate terminal pane):
   ```bash
   cd frontend
   npm run dev
   ```
3. Open **`http://localhost:5173`** in your browser.

---

## Docker Setup (Recommended for Multi-Machine Deployment)

Run Job Finder in Docker containers with proper Claude OAuth authentication:

### Quick Start
```bash
# Run setup script (handles credentials, configuration)
./docker-setup.sh              # macOS/Linux
docker-setup.bat               # Windows

# Build and start services
docker compose build
docker compose up -d

# Access the application
# Frontend:  http://localhost:5173
# Backend:   http://localhost:8000
```

### Key Features
- ✅ **Claude OAuth Authentication** — Uses `~/.claude` credentials, never API keys
- ✅ **Cross-Machine Compatible** — Works on macOS, Linux, Windows
- ✅ **Persistent Database** — Jobs saved in Docker volumes
- ✅ **Service Health Checks** — Automatic restart on failure
- ✅ **Hot Reload Support** — Use override file for development

### Setup Details
1. Ensure `claude login` has been run (creates `~/.claude`)
2. Run `./docker-setup.sh` to validate and configure
3. Edit `.env` if needed (ports, API host, etc.)
4. Run `docker compose up -d`

### Documentation
- **Full Guide**: See [DOCKER.md](DOCKER.md) for detailed setup, troubleshooting, and production deployment
- **Quick Reference**: See [DOCKER-QUICKREF.md](DOCKER-QUICKREF.md) for common commands
- **Configuration**: See [.env.example](.env.example) for environment variables

### Useful Commands
```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild after code changes
docker compose up -d --build

# Access container shell
docker compose exec backend bash
```

---

## Cloud Deployment — Vercel (frontend) + Render (backend) + Neon (database)

The app deploys as two services that talk over HTTPS, with Postgres on Neon. Deploys are
driven by **native Git integration** — pushing to `master` auto-deploys both sides; there are
no deploy secrets in the repo.

```
Vercel (static React)  ──HTTPS──▶  Render (FastAPI + claude CLI)  ──▶  Neon (Postgres)
   VITE_API_BASE_URL              CLAUDE_CODE_OAUTH_TOKEN, DATABASE_URL
```

### 1. Database — Neon
Create a Neon Postgres database and copy its connection string (with `?sslmode=require`).
The backend auto-detects Postgres from `DATABASE_URL`; with no `DATABASE_URL` it falls back to
local SQLite. Schema/tables are created automatically on first boot (`init_db` / `init_auth_db` /
`init_resume_db`), including the seeded `test@test.com` user.

### 2. Backend — Render
Defined in [`render.yaml`](render.yaml) (Blueprint) using [`backend/Dockerfile.render`](backend/Dockerfile.render),
which installs the Python deps **plus Node.js + the Claude Code CLI** (the Agent SDK shells out to `claude`).

1. In Render, **New ▸ Blueprint** and point it at this repo.
2. Set the two secret env vars (Blueprint marks them `sync: false`):
   - `CLAUDE_CODE_OAUTH_TOKEN` — generate locally with **`claude setup-token`** and paste it in.
     OAuth only; never an API key.
   - `DATABASE_URL` — the Neon connection string from step 1.
3. Deploy. Health check: `GET /api/health`. Render injects `$PORT`; uvicorn binds it.

> Render's filesystem is ephemeral, which is why persistence lives in Neon. The resume
> optimizer's `docx` skill isn't shipped in the image (`.claude/` is excluded from the build
> context for credential safety), so it uses the deterministic python-docx fallback in production.

### 3. Frontend — Vercel
Defined in [`frontend/vercel.json`](frontend/vercel.json) (Vite preset, SPA rewrites).

1. In Vercel, import the repo with **Root Directory = `frontend`**.
2. Set env var `VITE_API_BASE_URL` to the FastAPI Cloud backend origin
   (e.g. `https://job-finder.fastapicloud.dev`, no trailing slash). It's baked in at build time.
3. Deploy. The frontend calls the backend cross-origin; CORS is already open on the backend and
   auth uses bearer tokens (no cookies).

### 4. Auto-Apply browser — Playwright MCP sidecar
The managed backend host has no Node/Chromium, so the auto-apply agent uses a small
**browser sidecar** defined in [`deploy/playwright-mcp/`](deploy/playwright-mcp/): a Docker
image (Playwright base) running `@playwright/mcp` in HTTP mode behind a bearer-token proxy
that also accepts resume uploads (`POST /upload`) and exposes `GET /healthz`. `--isolated`
gives every MCP session its own browser context, so concurrent applies share one sidecar.

1. Deploy the image to a container host. **Google Cloud Run is the recommended (free-tier)
   path** — its always-free quota covers normal apply volume at a browser-capable
   2 vCPU / 2 GiB size (needs a GCP project with open billing linked, but stays $0 under
   quota). One command after `gcloud auth login` + project setup:
   ```bash
   cd deploy/playwright-mcp
   ./deploy-cloudrun.sh     # prints the service URL + generated token
   ```
   A Fly.io config (`fly.toml`) is kept as a paid, always-on alternative. Full details and
   sizing rationale: [`deploy/playwright-mcp/README.md`](deploy/playwright-mcp/README.md).
2. On the backend host (FastAPI Cloud → Project Settings → Secrets) set:
   - `PLAYWRIGHT_MCP_URL` — the sidecar origin (e.g. `https://job-finder-playwright-mcp-….run.app`)
   - `PLAYWRIGHT_MCP_TOKEN` — the same token
3. Redeploy the backend; `GET /api/status` should now report `apply_agent_available: true`
   and the dashboard's Auto-Apply buttons become active. Without the sidecar the buttons
   render disabled with an explanatory tooltip.

Locally no sidecar is needed — with Node/`npx` on PATH the backend spawns Playwright MCP
itself. `APPLY_CONCURRENCY_MAX` (default 3) caps simultaneous apply runs per user.

See [`.env.example`](.env.example) for the full list of deployment variables.

## Further Documentation
* For Docker deployment and troubleshooting, see [DOCKER.md](DOCKER.md) and [DOCKER-QUICKREF.md](DOCKER-QUICKREF.md).
* For agent design, tools, and the response schema, see [AGENTS.md](AGENTS.md).
* For repo-wide architecture, conventions, and key invariants (OAuth-only auth, the 24-hour freshness rule), see [CLAUDE.md](CLAUDE.md).
