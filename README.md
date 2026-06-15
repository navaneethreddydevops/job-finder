# AI C2C Job Finder Application

A full-stack, Claude-powered dashboard that scours web portals for Corp-to-Corp (C2C) Data Engineer roles. It features an autonomous agent using the **Claude Agent SDK**, a **FastAPI backend**, and a premium **React UI** complete with a real-time console thought-logger.

---

## Features
* **AI Job Finder Agent**: Uses DuckDuckGo search and custom webpage text parsers to find jobs and extract details.
* **Turn & Tool Hooks**: Intercepts agent thoughts and tool runs to stream logs live to the browser.
* **Sleek React Dashboard**: A dark-mode, glassmorphism-based web interface featuring statistics, search, multi-selection filters (C2C viability, Remote vs Onsite, Job Sources), and a details drawer.
* **Unified Server**: Serving static production React files directly from the Python backend.

---

## Folder Structure
```
job-finder/
├── backend/               # Python Backend
│   ├── agent.py           # Antigravity Agent, tools, and schemas
│   ├── main.py            # FastAPI Server
│   └── jobs.json          # Cached job listings database
├── frontend/              # Vite React Project
│   ├── src/
│   │   ├── App.jsx        # Dashboard Component
│   │   └── index.css      # CSS Design System
│   └── vite.config.js     # Dev server proxy configuration
├── pyproject.toml         # Python Dependencies
├── .env.example           # Environment template
└── README.md              # Project Documentation
```

---

## Setup & Running Instructions

### 1. Add API Key
Copy [.env.example](file:///.env.example) to `.env` in the root:
```bash
cp .env.example .env
```
Open `.env` and paste your Anthropic Claude API key:
```env
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```
*(Get a key from [Anthropic Console](https://console.anthropic.com/))*

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
* For details on agent design, tools, and response schemas, see [agents.md](file:///Users/navaneethreddy/Documents/Github/job-finder/agents.md).
* For a full walkthrough of implemented files and verification results, see [walkthrough.md](file:///Users/navaneethreddy/.gemini/antigravity-ide/brain/ab8c02d6-5572-4dbc-ab82-b43d5113f620/walkthrough.md).
