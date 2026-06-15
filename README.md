# AI C2C Job Finder Application

A full-stack, Claude-powered dashboard that scours web portals for Corp-to-Corp (C2C) Data Engineer roles. It features an autonomous agent using the **Claude Agent SDK**, a **FastAPI backend**, and a premium **React UI** complete with a real-time console thought-logger.

---

## Features
* **Multi-agent Job Finder**: An orchestrator fans the search out to parallel `job_scout` subagents (via the Task tool), one per source (LinkedIn, Dice, Monster, Indeed, ZipRecruiter), then merges and de-duplicates the results.
* **Fresh-only results**: Collects and displays **only jobs posted within the last 24 hours** (today / the run date).
* **No volume cap**: Pulls as many matching C2C roles as it can find.
* **Custom tools + headless browser**: DuckDuckGo `web_search` and a BeautifulSoup `fetch_webpage_content` tool (the `job_finder_tools` MCP server), plus a Puppeteer MCP browser for blocked sites.
* **Live thought console**: Streams agent reasoning and tool calls to the browser over Server-Sent Events.
* **Sleek React Dashboard**: A dark-mode, glassmorphism web UI with statistics, search, multi-selection filters (C2C viability, Remote vs Onsite, Job Sources, Applied), and a details drawer.
* **Unified Server**: Serves the static production React build directly from the Python backend.

---

## Folder Structure
```
job-finder/
├── backend/               # Python Backend
│   ├── agent.py           # Claude Agent SDK orchestrator + job_scout subagent, schemas
│   ├── main.py            # FastAPI server (REST + SSE)
│   ├── db.py              # SQLite persistence + de-duplication
│   ├── mcp_server.py      # FastMCP server: web_search + fetch_webpage_content
│   └── jobs.db            # SQLite job database (created at runtime)
├── frontend/              # Vite React Project
│   ├── src/
│   │   ├── App.jsx        # Dashboard Component
│   │   └── index.css      # CSS Design System
│   └── vite.config.js     # Dev server proxy configuration
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
