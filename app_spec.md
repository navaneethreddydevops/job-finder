# Application Specification

This document describes the design for three feature sets added on top of the
existing **C2C Job Finder** (FastAPI backend + Vite/React frontend + Claude Agent SDK).
It is the source of truth for the work and should be kept in sync with the code.

---

## Task 1 — Provide all the tools needed for the agent

Reference: https://code.claude.com/docs/en/agent-sdk/overview

The job-finder orchestrator (`backend/agent.py`) searches for jobs matching a user-provided query
across six job boards: **LinkedIn, Dice, Monster, Indeed, Glassdoor, and ZipRecruiter**. The agent is granted
the **full built-in toolset** (no MCP integration), with behavior intentional and documented.

**Built-in tools granted to orchestrator** (per the Agent SDK overview):

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

**Built-in tools granted to job_scout subagent**: the scout receives the same comprehensive
toolset as the orchestrator (`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `WebSearch`,
`WebFetch`, `TodoWrite`) to enable flexible searching, data processing, and result formatting
across all job sources.

**Web tooling**: the agent uses Claude's built-in `WebSearch` and `WebFetch` for job discovery —
there is **no MCP integration** (the former `job_finder_tools` and `puppeteer` MCP servers
were removed).

Implementation: module-level `AGENT_ALLOWED_TOOLS` and `SCOUT_ALLOWED_TOOLS` lists passed to
`ClaudeAgentOptions(allowed_tools=...)` and the `job_scout` AgentDefinition respectively.

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

   **Account menu.** Account access is consolidated into a single **rightmost user tab**
   (`UserMenu`, `frontend/src/components/UserMenu.jsx`) rendered on every authenticated header
   (Dashboard, Profile, Resume Optimizer). The tab shows an avatar initial + name and opens a
   dropdown containing **Account & Profile** (→ `/profile`) and **Log out**. The dropdown
   closes on outside click or `Escape`. The separate header "Profile" link and "Logout" button
   were removed. Stable `id`s: `user-menu`, `user-menu-trigger`, `user-menu-dropdown`,
   `user-menu-profile`, `user-menu-logout`.
8. **Verify.** Build the frontend (`npm run build`) and load via the preview workflow; confirm
   each route renders, the agent console + modal look correct, and no regression in behavior.

Scope guardrail: this task is **CSS + fonts only**. Class names, element structure, `id`s
(used by WebMCP/agent tooling), state, and endpoints are unchanged.

---

## Task 5 — Incremental batch persistence (stream jobs to the DB as scouts finish)

When **Trigger Agent Run** is clicked the agent searches for jobs posted within the
**last 24 hours** (unchanged). The new requirement: results must be persisted to the
database in **small batches as they are found**, rather than waiting for the entire
agent run to finish before a single bulk save at the end.

### How it works
- The orchestrator fans out to one `job_scout` subagent per source via the `Task`
  tool. Each scout returns a JSON array of jobs for **its** source — that array is a
  natural batch.
- `run_job_finder_agent()` accepts a `batch_callback(jobs: list[dict])`. While streaming
  the agent response it tracks every `Task` tool-use id, and when the matching
  `ToolResultBlock` (the scout's returned JSON array) arrives it parses the jobs and
  invokes `batch_callback` **immediately** — so each scout's batch is saved the moment
  that scout finishes, before the orchestrator has merged everything or the run is done.
- `main.py`'s `run_agent_task` passes a `batch_callback` that saves each batch with
  `save_job` (URL-keyed de-dup makes it idempotent) and emits a
  `Database now holds … total jobs` log line, which the UI already uses to refresh.
- The final structured-output list is still saved at the end as a **reconciliation /
  safety net** — `save_job` de-dups, so jobs already persisted from a batch are just
  updated, not duplicated.

### Notes
- Only jobs flagged/derivable as posted within 24h are kept (unchanged enforcement at
  agent + DB + frontend layers).
- Batches are best-effort: a malformed scout payload is skipped without failing the run;
  the end-of-run reconciliation pass still captures the merged list.

---

---

## Task 6 — UI/UX Enhancement Pass

Goal: raise the quality of the existing Notion-light design system to a polished,
production-grade experience without altering any behavior, routes, APIs, or WebMCP
tooling. Every change below is **frontend-only** unless a backend note is called out
explicitly. Changes are additive — no existing CSS class names, element `id`s, or
route paths are removed.

All enhancements are tracked in this spec as implementation acceptance criteria
(☐ todo / ☑ done). This task is broken into eight focused sub-tasks.

---

### 6.1 — Navigation & Header

**Problems:**
- "Resume Optimizer" (link) and "Sync Database" (button) look identical — no visual
  hierarchy between navigation and an action.
- No active-page indicator — user can't tell what route they're on.
- The user avatar chip ("AB") is small and easy to overlook.

**Acceptance criteria:**
- ☐ Navigation links (`Resume Optimizer`) use a **nav-link style** (hairline pill,
  no filled background) and show a subtle active state (slightly darker fill /
  border) when their route matches `location.pathname`.
- ☐ Action buttons (`Sync Database`) are styled as secondary actions: outlined,
  lighter weight, clearly distinct from nav links.
- ☐ `UserMenu` trigger is at least 36px tall, shows a colored avatar initial circle
  (using `--primary` background) and the user's first name next to it, making it
  unmistakably interactive.
- ☐ The backend status pill (`Backend: Online`) is moved to a more compact indicator
  (a small coloured dot + label, right-aligned) so it doesn't compete with the
  primary nav actions.
- ☐ Header adapts to ≤ 640px: logo + status pill left, user menu right — action
  buttons collapse into the user dropdown.

---

### 6.2 — Onboarding & Empty State

**Problems:**
- "No Jobs Found" with a giant icon conveys nothing actionable on first visit.
- "Use Default Query" does not explain what happens next.
- No visual explanation of the 3-step flow (Enter search → Run agent → View jobs).

**Acceptance criteria:**
- ☐ When the database is empty and the agent has never run, show an **onboarding
  banner** at the top of the job list area explaining the 3-step process:
  1. "Enter a search target" (arrow → search input)
  2. "Trigger Agent Run" (arrow → button)
  3. "View live results" (arrow → job list)
  The banner is dismissible (persisted in `localStorage` key `jf_onboarded`).
- ☐ The empty state illustration is removed; replace with a concise message and a
  single clear CTA: **"Start your first search →"** that scrolls to / focuses the
  search-target input.
- ☐ When jobs exist but all are filtered out, the empty state message changes to
  "No jobs match your filters." + a **"Clear filters"** button.

---

### 6.3 — Stats Cards

**Problems:**
- Four cards showing "0" offer no context; no "last updated" timestamp; no
  tooltips explaining what each metric counts.

**Acceptance criteria:**
- ☐ Each card has a **tooltip** (`title` attribute) explaining what it counts,
  e.g. "Total unique job postings fetched in the last agent run".
- ☐ A **"Last updated"** line is shown below the cards in muted text:
  `Updated just now` / `Updated 3 min ago` / `No data yet` — derived from the
  most recent job's `created_at` in the DB (returned by `GET /api/jobs`'s
  response or a lightweight `GET /api/status`).
- ☐ Stat numbers animate from 0 → final value on first paint (a simple
  `requestAnimationFrame` counter, ≤ 600ms, respects `prefers-reduced-motion`).
- ☐ The "Applied Jobs" card icon and counter turn `--success` green when > 0.

---

### 6.4 — Agent Controls & Filter Panel

**Problems:**
- The Agent Controls box has no visual separation between "run the agent" and
  "filter results" — they are distinct concerns jammed together.
- Button-group filters (All / Applied / Not Applied etc.) take a lot of vertical
  space; 3 groups × 3–4 buttons = 12 click targets before the job list begins.
- No way to see at a glance which filters are active.
- No "Clear all filters" shortcut.

**Acceptance criteria:**
- ☐ Split Agent Controls into two clearly-labelled sections:
  - **"Run Agent"** — search-target input + Trigger Agent Run button.
  - **"Filter Results"** — all filter groups. This section is **collapsible**
    (default open when jobs exist, default closed when DB is empty). A toggle
    label shows the count of active non-"All" filters, e.g. "Filters (2)".
- ☐ Each filter group uses a **segmented control / pill-button group** with
  compact sizing (font-size 0.8rem, padding 0.25rem 0.75rem) — less vertical
  height than current.
- ☐ A **"Clear filters"** text link appears inline with the "Filter Results"
  heading when any filter is non-default; clicking resets all filters to "All".
- ☐ Active filter values (non-"All") are reflected as removable **tags** shown
  above the job list header, one per active filter, each with an ×. Clicking ×
  resets that filter to "All".
- ☐ The filter source ("Source") group has option badges that show the job count
  for that source (e.g. "LinkedIn (12)") derived from the current unfiltered job
  list.

---

### 6.5 — Job List & Cards

**Problems:**
- Job cards (when populated) show a lot of text but no quick visual scan path.
- No hover elevation or click affordance on the cards.
- Pagination controls are bare.

**Acceptance criteria:**
- ☐ Job cards have a clear visual hierarchy:
  - Line 1: **Job title** (semibold, `--text-primary`)
  - Line 2: **Company** + location dot + **Location** (muted, truncated to 1 line)
  - Line 3: Chips row — C2C badge, Remote badge (if applicable), Source chip,
    Posted date
  - Right column: Applied toggle + "View" link
- ☐ Cards have a subtle `box-shadow` elevation on `:hover` (use `--shadow-md`)
  and a `0.1s` transition.
- ☐ The **C2C badge** uses Notion-style colored tags:
  - Confirmed C2C → green tint
  - Likely C2C → blue tint
  - Not Specified → grey tint
- ☐ Pagination shows **"Page X of Y · Z jobs"** in muted text; Prev/Next buttons
  are compact arrows; disabled states are visually distinct (not just pointer-events).
- ☐ The **search bar** above the job list has a keyboard shortcut hint (`⌘K` or `/`)
  that focuses it; pressing `/` anywhere on the dashboard focuses the search box.

---

### 6.6 — Agent Console / SSE Log

**Problems:**
- The agent console is a plain scrolling text area; it doesn't visually
  differentiate tool calls, thoughts, system messages, and errors.
- No collapse/expand control for the console.

**Acceptance criteria:**
- ☐ The console panel is **collapsible** (default collapsed when idle, expands
  automatically when an agent run starts). A header bar shows "Agent Log" and the
  latest single-line status message.
- ☐ Log entries are colour-coded by type using the existing CSS log-type classes
  (`log-thought`, `log-tool`, `log-system`, `log-error`) with subtle left-border
  accents matching the type color (thought = purple, tool = blue, system = muted,
  error = red).
- ☐ When the agent is running a **live typing indicator** (three bouncing dots) is
  shown in the console header alongside elapsed time (`Running — 00:42`).
- ☐ A **"Copy log"** button in the console header copies the full log text to the
  clipboard using `navigator.clipboard.writeText`.

---

### 6.7 — Resume Optimizer UX

**Problems:**
- The 3-area layout (JD textarea | left pane | right pane) has no visual labels
  explaining which area is which.
- The Download button blends with other buttons.
- The progress bar shows "Complete — 100%" but no stage label or time taken.
- Left pane "Existing Resume" is blank and silent when no resume is uploaded.

**Acceptance criteria:**
- ☐ Each pane has a visible step badge/label:
  - JD area: **"① Job Description"**
  - Left pane: **"② Your Resume"**
  - Right pane: **"③ Optimized Result"**
- ☐ The left pane, when empty, shows a **drag-and-drop zone** with a cloud-upload
  icon, "Drop your .docx here" text, and a smaller "or click to browse" sub-label.
  Zone highlights (`--border-focus` glow) on `dragover`.
- ☐ The **Download** button in the right pane header is styled as a **primary
  filled button** (blue, full-width on mobile) and shows a download icon; the
  edit/preview toggle buttons are secondary (outlined).
- ☐ The progress bar shows the current `stage` label below it and, once complete,
  shows the total time taken (e.g. `Completed in 47s`).
- ☐ The `+N new` badge next to "Optimized Result" is always visible when
  `is_new` items exist, and a legend line below the header reads
  "Green items are new — tailored to the job description."
- ☐ Clicking a `is_new` bullet in the editor highlights it briefly (`--success-glow`
  flash, 0.6s) to draw attention before editing.

---

### 6.8 — Loading States, Toast Notifications & Accessibility

**Problems:**
- No skeleton loading states — the page jumps from empty to populated.
- No feedback for successful actions (apply toggle, sync database, edits saved).
- Keyboard accessibility is incomplete: modal and dropdown don't trap focus or
  respond to `Escape`.

**Acceptance criteria:**
- ☐ While `GET /api/jobs` is in-flight, show **skeleton cards** (3 placeholder
  cards with animated shimmer) instead of "No Jobs Found".
- ☐ A **toast notification system** appears bottom-right:
  - Triggers: apply toggle toggled, database synced, edits saved, errors.
  - Style: compact card (white, `--shadow-md`), colored left border by type
    (success = green, error = red, info = blue), auto-dismisses after 3s,
    manually dismissible with ×.
  - No external library — implemented with a simple React context +
    `useState` queue.
- ☐ The job-detail `<dialog>` traps focus on open and returns focus to the trigger
  card on close; `Escape` closes it.
- ☐ The `UserMenu` dropdown responds to `Escape` (close), arrow keys (Up/Down
  navigate items), and `Enter`/`Space` (activate item).
- ☐ All interactive elements have a `:focus-visible` ring using `--border-focus`.
- ☐ The stats counter animation skips if `prefers-reduced-motion: reduce` is set.

---

### 6.9 — Mobile Responsiveness

**Problems:**
- Dashboard layout is desktop-first; on narrow viewports the sidebar and main
  content stack awkwardly.
- Resume Optimizer split-pane is unusable below ~768px.

**Acceptance criteria:**
- ☐ Below 768px the dashboard sidebar (Agent Controls) moves **above** the job list,
  and the filter panel is collapsed by default.
- ☐ Below 768px the Resume Optimizer switches from side-by-side panes to a
  **tabbed layout**: tabs "Your Resume" / "Optimized Result" toggle which pane is
  visible; the JD textarea stays pinned at the top.
- ☐ Stat cards change from 2×2 grid to a 2-column grid that scrolls horizontally
  on very narrow screens (< 400px) rather than stacking 4 rows.
- ☐ The header action buttons (Resume Optimizer link, Sync Database) collapse into
  the UserMenu dropdown below 640px.

---

### Implementation order (recommended)

Implement sub-tasks in this order so each leaves the app in a working state:

1. **6.8** (toasts + skeletons + focus trapping) — foundational, all later tasks depend on feedback patterns.
2. **6.3** (stats cards) — self-contained, high visual impact.
3. **6.1** (header/nav) — touches every page.
4. **6.4** (filter panel) — touches Dashboard only, isolated.
5. **6.2** (empty/onboarding) — depends on 6.4 (Clear filters CTA).
6. **6.5** (job cards) — depends on 6.4 (tag chips from filter state).
7. **6.6** (agent console) — depends on 6.8 (collapse pattern established).
8. **6.7** (Resume Optimizer) — isolated page.
9. **6.9** (mobile) — final pass across all pages.

### Files expected to change

| File | Sub-tasks |
|---|---|
| `frontend/src/index.css` | 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9 |
| `frontend/src/Dashboard.jsx` | 6.2, 6.3, 6.4, 6.5, 6.6 |
| `frontend/src/components/UserMenu.jsx` | 6.1, 6.8, 6.9 |
| `frontend/src/pages/ResumeOptimizer.jsx` | 6.7, 6.9 |
| `frontend/src/pages/Login.jsx` | 6.8 (focus/a11y) |
| `frontend/src/pages/Register.jsx` | 6.8 (focus/a11y) |
| `frontend/src/App.jsx` | 6.8 (toast context provider at root) |
| New: `frontend/src/components/Toast.jsx` | 6.8 |
| New: `frontend/src/hooks/useToast.js` | 6.8 |

### No backend changes required
All enhancements are purely frontend. The only new data surfaced is "last updated"
time, which is already derivable from the `jobs` array returned by `GET /api/jobs`
(sort by `created_at` desc, take first). No new endpoints are needed.

---

## Dependencies added
- **Python:** `python-docx` (docx parse/generate).
- **JS:** `react-router-dom` (routing), `docx-preview` (render .docx in browser).
- **Fonts (CDN):** Inter (UI), Lora (serif display), monospace for the console — loaded in
  `frontend/index.html`; no new npm packages.
