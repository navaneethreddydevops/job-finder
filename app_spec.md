# Application Specification

This document describes the design for three feature sets added on top of the
existing **C2C Job Finder** (FastAPI backend + Vite/React frontend + Claude Agent SDK).
It is the source of truth for the work and should be kept in sync with the code.

---

## Task 1 — Provide all the tools needed for the agent

Reference: https://code.claude.com/docs/en/agent-sdk/overview

The job-finder orchestrator (`backend/agent.py`) previously relied on
`permission_mode="bypassPermissions"` alone and never declared which tools the agent
was allowed to use. We now explicitly grant the agent the **full built-in toolset**
(no MCP integration), so behaviour is intentional and documented.

**Built-in tools granted** (per the Agent SDK overview):

| Tool          | Purpose |
| ------------- | ------- |
| `Read`        | Read files in the working directory |
| `Write`       | Create new files |
| `Edit`        | Edit existing files |
| `Bash`        | Run shell commands / scripts |
| `Glob`        | Find files by glob pattern |
| `Grep`        | Search file contents |
| `WebSearch`   | Search the web for current postings |
| `WebFetch`    | Fetch & parse a web page |
| `Task`        | Spawn the `job_scout` subagent (fan-out) |
| `TodoWrite`   | Track multi-step plans |

**Web tooling**: the agent uses Claude's built-in `WebSearch` and `WebFetch` only —
there is **no MCP integration** (the former `job_finder_tools` and `puppeteer` MCP
servers were removed).

Implementation: a module-level `AGENT_ALLOWED_TOOLS` list passed to
`ClaudeAgentOptions(allowed_tools=...)`. The `job_scout` subagent keeps its narrower
toolset (`WebSearch`, `WebFetch`).

---

## Task 2 — Authentication (login / register / profile / password)

### Requirements
- Username **is** the user's email; password is **at least 8 characters**.
- Stored in the backend database (SQLite, same `jobs.db`).
- Endpoints for **login** and **register**, plus **change password** and **update profile**.
- Seed a **test user**: `test@test.com` / `testtest`.

### Data model (`backend/auth.py`, table `users`)
| column          | type    | notes |
| --------------- | ------- | ----- |
| id              | INTEGER | PK |
| email           | TEXT    | UNIQUE, lowercased — the username |
| password_hash   | TEXT    | PBKDF2-HMAC-SHA256, hex |
| salt            | TEXT    | per-user random hex |
| full_name       | TEXT    | profile |
| phone           | TEXT    | profile |
| created_at      | TEXT    | ISO timestamp |

Sessions table `auth_sessions`: `token` (PK, `secrets.token_urlsafe`), `user_id`,
`created_at`. Tokens are bearer tokens returned on login/register and sent back as
`Authorization: Bearer <token>`.

No external crypto deps — uses stdlib `hashlib.pbkdf2_hmac` + `secrets` + `hmac.compare_digest`.

### Endpoints (all JSON, `/api` prefix so the Vite proxy + static mount keep working)
| Method | Path                    | Auth | Body / Result |
| ------ | ----------------------- | ---- | ------------- |
| POST   | `/api/register`         | no   | `{email, password, full_name?, phone?}` → `{token, user}` |
| POST   | `/api/login`            | no   | `{email, password}` → `{token, user}` |
| POST   | `/api/logout`           | yes  | invalidates the token |
| GET    | `/api/me`               | yes  | `{user}` |
| PATCH  | `/api/profile`          | yes  | `{full_name?, phone?, email?}` → `{user}` |
| POST   | `/api/change-password`  | yes  | `{current_password, new_password}` → `{success}` |

Validation: email must look like an email; password `len >= 8`. Errors return 400/401.

### Frontend
- React Router routes: `/login`, `/register`, `/profile`.
- `AuthContext` stores `{token, user}` in `localStorage` (`jf_auth`), attaches the
  bearer header via a `apiFetch` helper, and guards the dashboard + resume routes.
- Test user is seeded on startup so the login form works out of the box.

---

## Task 3 — Resume Optimizer

New frontend route **`/resume/optimizer`** + backend endpoints under `/api/resume/*`.

### UX
A **split-pane** layout:
- **Top chat bar** (full width): textarea for the **job description / requirements** and an
  **Optimize** button that calls the Claude model to generate resume bullet points tailored
  to the requirement.
- **Left pane:** drop zone / file picker for an **existing Word resume** (`.docx`). The
  uploaded document is **previewed in the UI** (rendered via `docx-preview`). Uploading a
  new file overrides the preview.
- **Right pane:** the **result**, produced as a **Word document**, rendered in-browser and
  **downloadable** (`.docx`) with the updated points that match the requirement.
- A **progress bar** shows generation status (queued → parsing → calling Claude → building
  docx → done), driven by backend status polling.
- **State survives refresh:** the JD text and uploaded resume (base64) are persisted to
  `localStorage`; the in-flight / completed job + generated result live in the DB keyed by
  user, so a refresh mid-generation restores the progress and result.

### Backend (`backend/resume.py`, tables `resume_jobs`)
- `python-docx` parses the uploaded `.docx` to text and builds the output `.docx`.
- Claude is invoked via the Agent SDK (OAuth only, same env-drop rule) to produce an
  optimized, ATS-friendly set of bullet points / summary tailored to the JD.

| Method | Path                      | Auth | Notes |
| ------ | ------------------------- | ---- | ----- |
| POST   | `/api/resume/optimize`    | yes  | multipart: `resume` (.docx file, optional if text sent) + `job_description`. Starts a background job, returns `{job_id}`. |
| GET    | `/api/resume/status`      | yes  | `{status, progress, stage, error, has_result}` for the user's latest job. |
| GET    | `/api/resume/result`      | yes  | JSON: optimized text + structured points (for rendering). |
| GET    | `/api/resume/download`    | yes  | streams the generated `.docx` (`Content-Disposition: attachment`). |

`resume_jobs` columns: `id, user_id, status, stage, progress, job_description,
original_text, result_markdown, result_docx (BLOB), error, updated_at`. One active/last
job is kept per user (latest wins) so refresh restores state.

`status` ∈ `idle | running | done | error`; `progress` is 0–100; `stage` is a human label.

### Frontend
- `docx-preview` renders both the uploaded resume (left) and the generated docx (right).
- Polls `/api/resume/status` while running to advance the progress bar; on `done` fetches
  `/api/resume/result` + the download blob.
- JD text + uploaded file persisted in `localStorage` (`jf_resume_state`).

### Task 3.1 — Enhancements (fit, diff, editing)

Follow-up requirements:

1. **Fit the Word preview within the pane.** `docx-preview` renders fixed US-Letter "paper"
   (612pt) that overflowed the pane. Render with `ignoreWidth: true` (+ `ignoreHeight: true`)
   so the page reflows to the container width, and constrain `.docx-host` with `overflow`.
2. **Diff of newly-added points; originals stay intact.** The optimizer now **preserves every
   original bullet verbatim** and **adds** new, JD-tailored bullets, returning a **structured
   result** (JSON) where each item carries an `is_new` flag. The UI highlights `is_new` items
   (green + "NEW" badge) — that is the diff. The original points are never dropped.
3. **Manual editing of both sides in the UI.**
   - **Original pane:** Preview (fit docx) / **Edit** (textarea of the extracted text) toggle;
     edits persist to `localStorage` and feed re-optimization.
   - **Optimized pane:** a **structured editor** — editable section titles and per-bullet
     inputs, add/remove bullets, with `is_new` items badged. Edits are saved to the backend
     and the downloadable `.docx` is rebuilt from the edited content.

#### Data model additions (`resume_jobs`)
- `result_json TEXT` — structured content `{summary, sections:[{title, items:[{text,is_new}]}]}`
  (added via `ALTER TABLE` migration). `result_markdown` is kept as a derived/fallback form.

#### Backend changes
- `_optimize_with_claude` returns **structured JSON** (preserve-and-augment prompt). A robust
  JSON parse with a markdown fallback guards against malformed output.
- **The `docx` skill is bundled into the repo and wired into the Agent SDK.** The skill lives
  at `.claude/skills/docx/` (`SKILL.md` + `scripts/`). The resume optimizer's
  `ClaudeAgentOptions` set `cwd=REPO_ROOT`, `setting_sources=["project"]`, `skills=["docx"]`,
  and grant `Skill` + `Read`/`Write`/`Bash`/`Glob`/`Grep`, so the agent builds the polished
  `.docx` with the skill at runtime (writing to a temp path that the backend reads back).
- `build_docx_from_content(content)` is the **deterministic python-docx fallback** (US-Letter,
  Arial, heading styles, real bullet list) used when the skill path produces no file, and for
  rebuilding after user edits (`PUT /content`). The final document is clean; the diff is a UI
  concern, not baked into the download.
- New endpoint `PUT /api/resume/content` — body `{content}`; saves edited structured content,
  rebuilds `result_docx` + `result_markdown`. `GET /api/resume/result` now returns
  `{content, markdown, original_text, status, stage}`.

---

## Task 4 — Notion-style UI redesign (award-winning UI/UX)

Goal: replace the dark "glassmorphism" theme with a clean, editorial, **Notion.com-inspired**
light interface across the entire app, raising visual quality to a polished, award-worthy
standard while changing **no behavior, routes, data flow, WebMCP tooling, or APIs**.

### Design language (what "looks like Notion" means here)
- **Light, paper-like canvas.** White background (`#ffffff`), subtle off-white surfaces
  (`#f7f6f3`) for hovers/sidebars, no gradients-on-the-page, no glow.
- **Notion ink palette.** Primary text `#37352f`, secondary/muted are alpha tints of it.
  Hairline borders `rgba(55,53,47,0.09)`. Accent blue `#2383e2` used sparingly.
- **Editorial type.** A serif display face (**Lora**) for headings/titles/numbers to evoke the
  Notion.com marketing site; **Inter** for body/UI; a monospace face for the agent console
  (rendered as a Notion-style code block, not a neon terminal).
- **Restraint over flash.** Small radii (6–10px), 1px hairlines, very soft shadows
  (`rgba(15,15,15,.05/.1)`), quiet hover states (background tint + ~1px lift), reduced
  animation. Colored "tag" chips use Notion's pastel tag colors (light fill + readable text).
- **Accessibility.** Respect `prefers-reduced-motion`; maintain AA contrast on the light theme.

### Implementation steps (single source of truth — follow in order)
1. **Tokens.** Rewrite the CSS custom properties in `frontend/src/index.css` (`:root`) to the
   Notion light palette **keeping the existing variable names** (`--primary`, `--success`,
   `--border`, `--text-*`, `--*-glow`, …) so the inline styles in the JSX adopt the new theme
   with **no component edits**.
2. **Fonts.** In `frontend/index.html`, load **Inter + Lora + a monospace** (replacing
   Outfit/Fira-only) and set `--font-sans`/`--font-heading`/`--font-mono` accordingly. Update
   the document `<title>`.
3. **Global canvas.** Remove the body radial-gradient glows; set the light canvas; keep the
   centered `max-width` container.
4. **Chrome.** Restyle header, stat cards, sidebar/controls, filter chips, buttons (primary =
   solid Notion blue; secondary = hairline) to the flat light system.
5. **Job list + modal.** Restyle job cards (hairline, quiet hover), badges (Notion tag colors),
   the native `<dialog>` details modal (white card, soft popover shadow, subtle backdrop).
6. **Agent console.** Re-skin the SSE log console as a **light code block** (off-white bg,
   monospace) with readable syntax colors for tool/thought/system/error log classes.
7. **Auth / Profile / Resume Optimizer.** These reuse the shared classes
   (`auth-card`, `sidebar-panel`, `input-text`, `resume-*`, `docx-host`), so they inherit the
   new look; only verify contrast and the docx "paper" preview still reads correctly.
8. **Verify.** Build the frontend (`npm run build`) and load via the preview workflow; confirm
   each route renders, the agent console + modal look correct, and no regression in behavior.

Scope guardrail: this task is **CSS + fonts only**. Class names, element structure, `id`s
(used by WebMCP/agent tooling), state, and endpoints are unchanged.

---

## Dependencies added
- **Python:** `python-docx` (docx parse/generate).
- **JS:** `react-router-dom` (routing), `docx-preview` (render .docx in browser).
- **Fonts (CDN):** Inter (UI), Lora (serif display), monospace for the console — loaded in
  `frontend/index.html`; no new npm packages.
