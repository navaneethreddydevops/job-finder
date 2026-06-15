# AI C2C Job Finder Application

A full-stack, Claude-powered dashboard that scours web portals for Corp-to-Corp (C2C) Data Engineer roles. It features an autonomous agent using the **Claude Agent SDK**, a **FastAPI backend**, and a premium **React UI** complete with a real-time console thought-logger.

---

## Features
* **Multi-agent Job Finder**: An orchestrator fans the search out to parallel `job_scout` subagents (via the Task tool), one per source (LinkedIn, Dice, Monster, Indeed, ZipRecruiter), then merges and de-duplicates the results.
* **Fresh-only results**: Collects and displays **only jobs posted within the last 24 hours** (today / the run date).
* **No volume cap**: Pulls as many matching C2C roles as it can find.
* **Incremental saving**: Jobs are written to the database in small batches as each scout finishes, so the dashboard fills in progressively instead of waiting for the whole run to complete.
* **Built-in web tooling**: Scouts search and read job boards using Claude's built-in `WebSearch` and `WebFetch` tools — no MCP servers required.
* **Live thought console**: Streams agent reasoning and tool calls to the browser over Server-Sent Events.
* **Notion-inspired React Dashboard**: A clean, editorial light UI (serif display headings, hairline borders, soft shadows, Notion-style tag colors) with statistics, search, multi-selection filters (C2C viability, Remote vs Onsite, Job Sources, Applied), and a details drawer. See the redesign spec in [app_spec.md](app_spec.md) (Task 4).
* **Authentication**: Email/password login & registration (8+ char passwords), profile editing, and password change. Seeded test user `test@test.com` / `testtest`.
* **Resume Optimizer** (`/resume/optimizer`): Split-pane tool — paste a job description, drop in your existing Word resume (previewed in-browser), and Claude generates a tailored, downloadable `.docx`. Includes a progress bar and refresh-safe persisted state.
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
│   ├── db.py              # SQLite persistence + de-duplication
│   └── jobs.db            # SQLite database (jobs, users, resume_jobs)
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

## Further Documentation
* For agent design, tools, and the response schema, see [AGENTS.md](AGENTS.md).
* For repo-wide architecture, conventions, and key invariants (OAuth-only auth, the 24-hour freshness rule), see [CLAUDE.md](CLAUDE.md).
