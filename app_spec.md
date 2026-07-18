# Application Specification

This document describes the design for three feature sets added on top of the
existing **Job Finder** (FastAPI backend + Vite/React frontend + Claude Agent SDK).
It is the source of truth for the work and should be kept in sync with the code.

---

## Task 1 — Provide all the tools needed for the agent

Reference: https://code.claude.com/docs/en/agent-sdk/overview

The job-finder orchestrator (`backend/agent.py`) researches **remote, full-time jobs open to
US-based candidates** across twelve sources, in **priority order**. HIGHEST priority — searched
first with the deepest effort, and sorted to the top of the dashboard — are the **career
portals** (`PORTAL_SOURCES`): **Workday (`*.myworkdayjobs.com`), Greenhouse
(`boards.greenhouse.io` / `job-boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby
(`jobs.ashbyhq.com`)**, and **`Company`** — employer career pages searched on the open web
(Exa/Tavily with the known board domains excluded). Next, **LinkedIn, Indeed, Glassdoor,
ZipRecruiter** (bulk-scraped in one structured `jobspy_search` call per role, plus Google Jobs
whose results map to `Company`), then the tech boards **Dice (`dice.com`), Wellfound
(`wellfound.com`), Built In (`builtin.com`)** (`SECONDARY_BOARD_SOURCES`).
It searches the user's typed
query as the only role (`DEFAULT_ROLES` — Principal DevOps, Cloud, Kubernetes, SRE — are a fallback
used only when the query is empty), keeping only postings from the **last 7 days** (narrowed further
by the incremental-search checkpoint, below); the orchestrator runs the searches itself and
hands batches of candidates to parallel `job_scout` subagents (running on **claude-haiku-4-5** for
cost) for verification and formatting. Candidates carrying `pre_verified=true` (from `jobspy_search`)
are formatted without any WebFetch. **US-only** is enforced at the tool layer (jobspy is called with
`country_indeed="USA"` / `location="United States"`; Exa/Tavily results carry a regex-derived
`us_eligible` bool|null annotation) and in both prompts (drop `us_eligible=false` / country-restricted
roles; keep unknowns for scout judgment). No US flag is persisted — every stored job is US-eligible
by construction.

**Built-in tools granted to orchestrator** (per the Agent SDK overview):

| Tool          | Purpose |
| ------------- | ------- |
| `Read`        | Read files in the working directory |
| `Write`       | Create new files |
| `Edit`        | Edit existing files |
| `Bash`        | Run shell commands / scripts |
| `Glob`        | Find files by glob pattern |
| `Grep`        | Search file contents |
| `mcp__jobsearch__jobspy_search` | **Primary bulk** discovery: structured scrape of Indeed/LinkedIn/Glassdoor/ZipRecruiter/Google Jobs (python-jobspy), pre-verified results |
| `mcp__jobsearch__serpapi_search` | **Supplementary bulk** discovery via SerpAPI's Google Jobs engine (pre-verified results); also the first fallback when jobspy fails |
| `mcp__jobsearch__exa_search`    | Job discovery via the Exa search API (non-jobspy sources + fallback) |
| `mcp__jobsearch__tavily_search` | Job discovery via the Tavily search API (non-jobspy sources + fallback) |
| `WebSearch`   | Fallback web search when a search-API key is missing |
| `WebFetch`    | Fetch & parse an individual listing |
| `Task`        | Spawn the `job_scout` subagent (fan-out) |
| `TodoWrite`   | Track multi-step plans |

**Tools granted to job_scout subagent**: the scout receives the same search + processing
toolset as the orchestrator (`exa_search`, `tavily_search`, `Read`, `Write`, `Edit`, `Bash`,
`Glob`, `Grep`, `WebSearch`, `WebFetch`, `TodoWrite`).

**Search tooling**: job discovery uses **JobSpy** (open-source `python-jobspy` structured
scraper — free, no API key), **SerpAPI** (`serpapi_search`, Google Jobs engine — bulk
pre-verified supplement to jobspy and its first fallback; `q="<role> remote"`,
`location="United States"`, `gl=us`, `ltype=1` remote filter, `chips=date_posted:*` window,
paginated via `next_page_token`, 10 results/page = 1 credit/page), plus the **Exa** and
**Tavily** search APIs, all wrapped as in-process SDK MCP tools (`backend/search_tools.py`,
`create_sdk_mcp_server` → server `jobsearch`) and passed via
`mcp_servers={"jobsearch": job_search_server}`. Keys come from env `EXA_API_KEY` /
`TAVILY_API_KEY` / `SERPAPI_API_KEY`. This is the only MCP integration; the former `job_finder_tools`
and `puppeteer` servers remain removed, and the built-in `WebSearch`/`WebFetch` are kept as a fallback.

**Checkpointing & incremental search**: jobs are saved **incrementally per scout batch**
(`batch_callback` in `main.py`) so the dashboard fills in during the run, and cross-run work is
never repeated:

- `pull_checkpoints` table (`db.py`): `(id AUTO_PK, user_id, query_normalized, last_run_at,
  jobs_found, UNIQUE(user_id, query_normalized))`. Written **only after a successful run**
  (`upsert_pull_checkpoint`); read at the start of `/api/pull` (`get_pull_checkpoint`) to narrow
  the effective search window to `hours since last run + 12h buffer`, floored at 1 day, capped at
  the requested `time_period_days` (`_effective_window_days` in `main.py`).
- **Cross-run URL dedup at zero token cost**: `run_job_finder_agent` loads the user's stored job
  URLs (`db.get_user_job_urls`) and installs them as the search tools' run context
  (`search_tools.set_run_context` / `clear_run_context`); all four tools drop already-known URLs
  before returning (reported as `skipped_known`), and `batch_callback` feeds freshly saved URLs
  back in via `add_known_urls` — the agent never spends tokens re-verifying known jobs. Safe as
  module state because `/api/pull` allows a single run at a time.

Implementation: module-level `AGENT_ALLOWED_TOOLS` and `SCOUT_ALLOWED_TOOLS` lists passed to
`ClaudeAgentOptions(allowed_tools=..., mcp_servers=...)` and the `job_scout` AgentDefinition respectively.

---

## Task 2 — Authentication (login / register / profile / password)

### Requirements
- Username **is** the user's email; password is **at least 8 characters**.
- Stored in the backend database (Neon Postgres via `DATABASE_URL`; SQLite test fallback).
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
| POST   | `/api/token`            | no   | OAuth2 password form (`username`=email, `password`) → `{access_token, token_type}` — powers Swagger's **Authorize** button |
| POST   | `/api/logout`           | yes  | invalidates the token |
| GET    | `/api/me`               | yes  | `{user}` |
| PATCH  | `/api/profile`          | yes  | `{full_name?, phone?, email?}` → `{user}` |
| POST   | `/api/change-password`  | yes  | `{current_password, new_password}` → `{success}` |

Validation: email must look like an email; password `len >= 8`. Errors return 400/401.

### Swagger / OpenAPI docs auth
Protected routes declare an `OAuth2PasswordBearer` security scheme (`tokenUrl=api/token`),
so `/docs` renders an **Authorize** button and a username/password form. Sign in there with
the seeded test account (`test@test.com` / `testtest`) to call protected endpoints from the
docs page. The scheme is wired with `auto_error=False`; `get_current_user` prefers the token
it extracts but still falls back to parsing a raw `Authorization: Bearer <token>` header, so
the frontend's existing `apiFetch` calls are unaffected. The FastAPI app `description` also
spells out the test credentials so they're visible at the top of `/docs`.

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

When **Trigger Agent Run** is clicked the agent researches remote, full-time jobs posted
within the **last 7 days**. The new requirement: results must be persisted to the
database in **small batches as they are found**, rather than waiting for the entire
agent run to finish before a single bulk save at the end.

### How it works
- The orchestrator fans out to one `job_scout` subagent per role × source via the `Task`
  tool. Each scout returns a JSON array of jobs for **its** role/source — that array is a
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
- Only jobs flagged/derivable as posted within 7 days are kept (enforcement at
  agent + DB + frontend layers).
- Batches are best-effort: a malformed scout payload is skipped without failing the run;
  the end-of-run reconciliation pass still captures the merged list.

### Data-quality gate & display ordering
- **`save_job` quality gate**: a job is persisted only when it has a valid http(s) `url`
  plus a non-empty `title` and `company`; anything else is dropped with a logged warning
  (`save_job` returns `None`; `True` = inserted, `False` = updated). There is no
  synthesized `manual:` URL key — the posting URL is mandatory and is the de-dup key.
- **URL validity at the tool layer**: `search_tools._is_valid_job_url` filters obvious
  non-posting URLs (non-http, bare domain roots, search/category/browse/index pages) out
  of jobspy/Exa/Tavily results before the agent sees them, and `jobspy_search` prefers
  `job_url_direct` (the employer's apply link) over the board's redirect `job_url` when
  the direct link is valid. The `JobItem.url` schema and both agent prompts require the
  exact direct posting URL — never a search-results or board index page.
- **Display ordering**: `db.get_user_jobs` sorts career-portal jobs
  (Workday/Greenhouse/Lever/Ashby/Company — must mirror `PORTAL_SOURCES` in `agent.py`)
  above all other sources via a portable `ORDER BY CASE source`, newest-first within each
  tier. The dashboard renders the API order; the job modal renders posting/apply links
  only for `http(s)` URLs.

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
  - Line 3: Chips row — Source chip, Remote badge (if applicable), Posted date
  - Right column: Applied toggle + "View" link
- ☐ Cards have a subtle `box-shadow` elevation on `:hover` (use `--shadow-md`)
  and a `0.1s` transition.
- ☐ The **Source chip** uses a Notion-style colored tag (blue tint via `.badge-source`).
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
- ☑ **Survives a page refresh mid-run.** The backend buffers the current run's log
  lines in an in-memory `log_history` (bounded by `LOG_HISTORY_MAX`, cleared at the
  start of each `/api/pull`); `GET /api/stream` replays that buffer to any newly
  connected client before streaming live. The dashboard's mount effect polls
  `/api/status`, and when a run is active it reconnects to the stream — the replayed
  history repopulates the console (as plain strings, so `formatLog`/`copyLog` stay
  correct), so refreshing never loses the in-flight agent session.

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

## Task 7 — Cloud deployment pipeline (Vercel + Render + Neon)

Goal: ship the app as two managed services — static frontend on **Vercel**, FastAPI backend on
**Render** — backed by **Neon Postgres**, deployed via native Git integration (push to `master`
auto-deploys; no deploy secrets in the repo). ☑ done.

### 7.1 — Dual-backend persistence (`backend/db.py`)
- `db.py` runs on SQLite by default (local/tests) and on Postgres when `DATABASE_URL` is a
  `postgres(ql)://` string. Exposes `IS_POSTGRES`, `AUTO_PK`, `BLOB_TYPE`, and
  `insert_returning_id()`; a connection wrapper rewrites `?` → `%s` for `psycopg`.
- `auth.py` / `resume.py` use `AUTO_PK` / `BLOB_TYPE` in DDL, branch migrations on `IS_POSTGRES`,
  and obtain new ids via `insert_returning_id`. Tables auto-create on boot — fresh Neon DB needs
  no manual migration. The seeded `test@test.com` user is created on first startup either way.

### 7.2 — Frontend API base (`frontend/src/auth.jsx`, `frontend/vercel.json`)
- `API_BASE = import.meta.env.VITE_API_BASE_URL` (empty locally → Vite proxy; set to the Render
  origin in Vercel). `apiUrl(path)` prepends the base; `apiFetch` adds it **and** the bearer token.
- All call sites (Dashboard, auth, Profile, ResumeOptimizer) route through `apiUrl`/`apiFetch` —
  no raw relative `fetch('/api/...')` / `EventSource('/api/...')`. Protected endpoints use
  `apiFetch`; open endpoints (`/api/status`, `/api/health`, `/api/stream`) use `apiUrl`.
- `vercel.json`: Vite framework preset, `npm ci` install, `dist` output, SPA rewrite (all
  non-asset routes → `index.html`) for React Router.

### 7.3 — Render backend (`render.yaml`, `backend/Dockerfile.render`)
- `Dockerfile.render` builds the Python venv with `uv` **and** installs Node.js 20 + the Claude
  Code CLI (`@anthropic-ai/claude-code`), since the Agent SDK spawns `claude`. Binds `$PORT`.
- `render.yaml` Blueprint: docker runtime, health check `GET /api/health`, `autoDeploy: true`,
  and two `sync: false` secrets — `CLAUDE_CODE_OAUTH_TOKEN` (OAuth-only auth, from
  `claude setup-token`; never an API key) and `DATABASE_URL` (Neon).

### 7.4 — Verification
- Backend: FastAPI `TestClient` exercises auth + jobs endpoints against SQLite (Claude call
  mocked); the dual-backend DB layer is smoke-tested. Frontend: `npm run build` + `npm run lint`.

## Dependencies added
- **Python:** `python-docx` (docx parse/generate), `psycopg[binary]` (Postgres/Neon driver).
- **JS:** `react-router-dom` (routing), `docx-preview` (render .docx in browser).
- **Fonts (CDN):** Inter (UI), Lora (serif display), monospace for the console — loaded in
  `frontend/index.html`; no new npm packages.

---

# Future Enhancements & Roadmap

This section outlines proposed features and improvements organized by priority and complexity. These enhancements build on the existing foundation and can be implemented incrementally.

## Phase 1: High-Impact, Low-Effort Quick Wins (1-2 weeks)

### 8.1 — Saved Searches & Smart Alerts
**Impact:** ⭐⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Save frequently-used search queries with custom names
- ☐ Schedule saved searches to run daily/weekly automatically
- ☐ Email digest of new matching jobs from saved searches
- ☐ Badge notification in header when new jobs match saved searches
- ☐ Optional SMS alerts via Twilio integration

**Data Model:**
```
saved_searches table:
- id, user_id, query, name, frequency (daily|weekly|manual)
- is_active, created_at, last_run_at

search_runs table:
- id, saved_search_id, jobs_found_count, run_at, was_emailed
```

**Implementation:**
- New router: `backend/searches.py` (CRUD for saved searches)
- Scheduler: `backend/scheduler.py` (APScheduler for background runs)
- Frontend page: `frontend/src/pages/SavedSearches.jsx`
- New table: `saved_searches`, `search_runs` in `backend/db.py`

---

### 8.2 — Job Bookmarking & Favorites
**Impact:** ⭐⭐⭐⭐ | **Complexity:** Low | **Effort:** 1 day

**Features:**
- ☐ Heart icon to bookmark jobs (persistent in DB)
- ☐ Bookmarked jobs filter/tab in dashboard
- ☐ Dedicated "My Bookmarks" page with count badge
- ☐ Sync bookmarks across sessions

**Data Model:**
```
bookmarks table:
- id, user_id, job_id, created_at

(jobs table addition)
- is_bookmarked boolean (derived)
```

**Endpoints:**
- `POST /api/jobs/{id}/bookmark` — toggle bookmark
- `GET /api/bookmarks` — list user's bookmarked jobs

**Implementation:**
- Add bookmarks table to `backend/db.py`
- New endpoints in `backend/main.py`
- Heart icon toggle in `frontend/src/Dashboard.jsx`
- Filter option for bookmarks

---

### 8.3 — Dark Mode Toggle
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 0.5 day

**Features:**
- ☐ Toggle button in header (sun/moon icon)
- ☐ CSS dark theme variables (complementary to light theme)
- ☐ Persist user preference to `localStorage`
- ☐ Respect system `prefers-color-scheme` setting
- ☐ Smooth transitions between modes

**Implementation:**
- Add `:root[data-theme="dark"]` color variables to `frontend/src/index.css`
- Create `frontend/src/hooks/useDarkMode.js` hook
- New component: `frontend/src/components/ThemeToggle.jsx`
- Integrate into header across all pages

---

### 8.4 — Export Jobs to CSV/PDF
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 1 day

**Features:**
- ☐ Export current filtered job list as CSV
- ☐ Export as formatted PDF
- ☐ Include metadata (query, timestamp, total count, filters applied)
- ☐ Export bookmarks only option

**Endpoints:**
- `GET /api/jobs/export?format=csv|pdf&filters=...`

**Implementation:**
- Backend: Export handler in `backend/main.py`
- Python: `reportlab` for PDF generation
- Frontend: Export button in dashboard toolbar
- Use `papaparse` for CSV formatting on frontend

**Dependencies:**
- `reportlab` (Python PDF generation)
- `papaparse` (JS CSV handling)

---

### 8.5 — Application Status Tracking
**Impact:** ⭐⭐⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2 days

**Features:**
- ☐ Track application lifecycle: `draft → applied → interviewing → offer → rejected`
- ☐ Store cover letter with each application
- ☐ Timeline of status changes with dates
- ☐ Dashboard stats updated: "Applied: 5, Interviews: 2, Offers: 1"
- ☐ Filter jobs by application status
- ☐ Application history view with timestamps

**Data Model:**
```
applications table:
- id, user_id, job_id, status (enum), cover_letter (text)
- applied_at, created_at, updated_at

application_history table:
- id, application_id, old_status, new_status, notes, changed_at
```

**Endpoints:**
- `POST /api/applications` — create application
- `PATCH /api/applications/{id}` — update status
- `GET /api/applications` — list user's applications with history

**Implementation:**
- New tables in `backend/db.py`
- New router: `backend/applications.py`
- Status dropdown on job cards in Dashboard
- Application detail modal with history timeline
- Status filter in dashboard

---

## Phase 2: Smart Matching & Intelligence (2-3 weeks)

### 9.1 — Skills Extraction & Gap Analysis
**Impact:** ⭐⭐⭐⭐ | **Complexity:** High | **Effort:** 3-4 days

**Features:**
- ☐ Extract required skills from job descriptions via Claude
- ☐ Compare against user's resume skills
- ☐ Highlight skills gaps with proficiency levels
- ☐ Suggest learning resources for missing skills
- ☐ Skill proficiency tracking (beginner/intermediate/expert)
- ☐ Endorsement system for skills

**Data Model:**
```
user_skills table:
- id, user_id, skill, proficiency (beginner|intermediate|expert), endorsed_count, years_of_exp

job_skills table:
- id, job_id, skill, is_required (boolean), extracted_at

skill_suggestions table:
- id, user_id, skill, course_url, platform, difficulty_level
```

**Implementation:**
- Enhance agent prompts to extract and structure skills
- New router: `backend/skills.py`
- Skills section in Profile page: `frontend/src/pages/Profile.jsx`
- Gap analysis component: `frontend/src/components/SkillsGapAnalysis.jsx`
- Show skills gaps on job detail modal

---

### 9.2 — Job Matching Score & Recommendations
**Impact:** ⭐⭐⭐⭐⭐ | **Complexity:** High | **Effort:** 3-5 days

**Features:**
- ☐ ML-based job match score (0-100) per job per user
- ☐ Factors: skill overlap, experience level, location fit, company fit
- ☐ Sort jobs by match score
- ☐ "Top matches today" widget on dashboard
- ☐ Match breakdown tooltip (why this score)
- ☐ User preference form: locations, experience level, industries, remote preference

**Data Model:**
```
job_match_scores table:
- id, job_id, user_id, match_score (0-100)
- skill_overlap, level_fit, location_fit, company_fit (json components)
- calculated_at

user_preferences table:
- id, user_id, preferred_locations (json), experience_level
- industries (json), remote_only, salary_min, salary_max, updated_at
```

**Implementation:**
- Scoring engine: `backend/scoring.py` (rule-based initially, ML-ready)
- Preference form in Profile: `frontend/src/pages/Profile.jsx`
- Sort filter in Dashboard
- Top matches widget
- Use `scikit-learn` if ML-based scoring is added

---

### 9.3 — Salary Extraction & Estimation
**Impact:** ⭐⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Extract salary ranges from job descriptions
- ☐ Estimate salary via Claude when range unavailable
- ☐ Display salary on job cards (show range if available)
- ☐ Filter by salary range
- ☐ Salary trends dashboard (by location/role)

**Data Model:**
```
job_salaries table:
- id, job_id, salary_min, salary_max, currency
- is_extracted (boolean), confidence_score, extracted_at
```

**Implementation:**
- Enhance scout prompts to extract salary
- Fallback Claude call for estimation
- Display salary on job cards and modal
- Salary range filter in dashboard
- Salary stats widget

---

### 9.4 — Company Research & Insights
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Fetch company metadata (size, industry, founding year, funding)
- ☐ Glassdoor ratings integration (fetched via WebFetch)
- ☐ Recent company news/LinkedIn integration
- ☐ Employee count and growth trajectory
- ☐ Company card sidebar in job details

**Data Model:**
```
company_info table:
- id, company_name, website, industry, size, founding_year
- total_funding, latest_funding, glassdoor_rating, reviews_count
- updated_at, cached_until
```

**Implementation:**
- New router: `backend/companies.py`
- Web fetch integration for company data
- Cache with 30-day TTL
- Company sidebar: `frontend/src/components/CompanyCard.jsx`
- Integrate into job detail modal

---

## Phase 3: Advanced Workflow & Integration (3-4 weeks)

### 10.1 — Cover Letter Generation & Templates
**Impact:** ⭐⭐⭐⭐ | **Complexity:** High | **Effort:** 3-4 days

**Features:**
- ☐ Generate personalized cover letters via Claude
- ☐ Store multiple templates (generic + industry-specific)
- ☐ In-place cover letter editor in UI
- ☐ Download cover letter as PDF + .docx
- ☐ Link cover letter to application

**Data Model:**
```
cover_letters table:
- id, user_id, job_id, content, template_id
- generated_at, last_edited_at

cover_templates table:
- id, user_id, name, content_template, is_default, industry_tag
```

**Implementation:**
- Claude prompt: personalize from JD + resume + profile
- New router: `backend/cover_letters.py`
- Editor component: `frontend/src/components/CoverLetterGenerator.jsx`
- Library page: `frontend/src/pages/CoverLetterLibrary.jsx`
- PDF download using `reportlab`

---

### 10.2 — Interview Scheduler & Reminders
**Impact:** ⭐⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Calendar view of scheduled interviews
- ☐ Add interview to application timeline
- ☐ Set reminders (1 day before, 1 hour before)
- ☐ Email + in-app notifications
- ☐ Export interview schedule to Google Calendar / Outlook

**Data Model:**
```
interviews table:
- id, application_id, scheduled_at, interview_type (phone|video|onsite)
- notes, reminder_sent, meeting_link, created_at

reminders table:
- id, interview_id, remind_at, type (email|notification), sent (boolean)
```

**Implementation:**
- New router: `backend/interviews.py`
- Calendar component: `frontend/src/pages/Calendar.jsx`
- Interview scheduler modal: `frontend/src/components/InterviewScheduler.jsx`
- Notification system: `backend/notifications.py`
- Calendar export integration (iCal format)
- Use `react-big-calendar` or similar

---

### 10.3 — Multiple Resume Versions
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2 days

**Features:**
- ☐ Store multiple resume versions (e.g., "Data Engineer", "ML Engineer")
- ☐ Quick-swap between versions
- ☐ Version history with rollback capability
- ☐ Clone existing resume as template
- ☐ Tag resumes by role/focus area

**Data Model:**
```
resumes table:
- id, user_id, name, docx_blob, is_default
- role_tag, created_at, updated_at

resume_versions table:
- id, resume_id, version_number, snapshot_docx, created_at, note
```

**Implementation:**
- Enhance `backend/resume.py` to support multiple versions
- Version selector in Resume Optimizer
- Resume library: `frontend/src/components/ResumeLibrary.jsx`
- Version history sidebar
- Endpoint: `GET/POST /api/resumes` for version management

---

### 10.4 — Job Comparison Tool
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Side-by-side job comparison (title, salary, benefits, company, location)
- ☐ Weighted scoring for custom priorities
- ☐ "Add to comparison" action on job cards
- ☐ Comparison export to PDF
- ☐ Visual comparison charts (salary, benefits, commute)

**Implementation:**
- Comparison state in Dashboard
- Component: `frontend/src/components/JobComparison.jsx`
- Comparison panel sidebar (show/hide)
- PDF export endpoint: `GET /api/jobs/compare/export`
- Use `recharts` for comparison visualizations

---

## Phase 4: Analytics & Insights (2-3 weeks)

### 11.1 — Dashboard Analytics & Job Market Insights
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Job market heatmap (top locations, trending skills, company hiring)
- ☐ Personal stats over time (applications sent, interview rate, offer rate)
- ☐ Salary trends by location/role/company
- ☐ Time-to-fill analytics (how long jobs stay posted)
- ☐ Skills demand radar chart

**Data Model:**
```
analytics_snapshots table:
- id, user_id, snapshot_date, applications_count, interviews_count
- offers_count, skills_snapshot (json)

market_trends table:
- id, snapshot_date, trending_skills (json), top_locations (json)
- avg_salary_by_role (json), total_jobs_posted
```

**Implementation:**
- Analytics router: `backend/analytics.py`
- Nightly aggregation job to build trends
- Analytics page: `frontend/src/pages/Analytics.jsx`
- Charts using `recharts` library
- Historical graphs for user metrics

---

### 11.2 — Job Board Performance Metrics
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 1 day

**Features:**
- ☐ Track best-performing job boards (most applications/offers)
- ☐ Response time metrics by source
- ☐ Quality score per board (% matches > 70%)
- ☐ Board recommendations: "LinkedIn has been your best source"

**Implementation:**
- Board metrics calculations in `backend/analytics.py`
- Visualization in Analytics page
- Performance comparison chart

---

## Phase 5: Advanced Integration & Automation (3-4 weeks)

### 12.1 — Email Integration & Digest
**Impact:** ⭐⭐⭐⭐ | **Complexity:** High | **Effort:** 3-4 days

**Features:**
- ☐ Daily/weekly email digest of matching jobs
- ☐ Personalized based on saved searches + preferences
- ☐ One-click apply links in email
- ☐ Unsubscribe management
- ☐ Professional HTML email templates with branding

**Implementation:**
- Email service: `backend/email.py` (SendGrid/Mailgun integration)
- Email template system
- Digest scheduler using APScheduler
- Unsubscribe token tracking in DB
- Settings page for email preferences: `frontend/src/pages/Settings.jsx`
- Dependencies: `sendgrid` or `mailgun_python`

---

### 12.2 — Webhook & JSON Feed API
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ JSON API feed of jobs (filterable by query/source/date)
- ☐ Webhooks when new matching jobs found
- ☐ API key authentication and management
- ☐ Rate limiting per API key
- ☐ Webhook delivery logs and retry logic

**Data Model:**
```
api_keys table:
- id, user_id, key, name, rate_limit, created_at, last_used_at

webhooks table:
- id, user_id, url, events (json), is_active, created_at

webhook_deliveries table:
- id, webhook_id, payload (json), status_code, response
- delivered_at, retry_count
```

**Endpoints:**
- `GET /api/v1/jobs/feed?query=...&format=json|xml`
- `POST /api/webhooks` — create webhook
- `GET /api/api-keys` — manage API keys

**Implementation:**
- Feed endpoint in `backend/main.py`
- Webhook delivery system: `backend/webhooks.py`
- API key management in Settings page
- Dependencies: `python-dateutil` for formatting

---

### 12.3 — Slack/Discord Integration
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Send new matching jobs to Slack channel
- ☐ Discord webhook for notifications
- ☐ Configurable filters per channel
- ☐ Rich job card formatting with metadata
- ☐ One-click "Apply" button in Slack

**Implementation:**
- Integration service: `backend/integrations.py`
- Slack/Discord API integration
- Webhook URLs stored in user settings
- Formatting: use message blocks/embeds for rich cards
- Settings page for integration URLs

---

## Phase 6: Performance & Reliability (2 weeks)

### 13.1 — Caching & Performance Optimization
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 2-3 days

**Features:**
- ☐ Redis cache for job results (24h TTL)
- ☐ Query result caching (save common searches)
- ☐ Frontend state caching improvements
- ☐ Database query optimization (indexes on frequently-queried columns)
- ☐ CDN optimization for static assets

**Implementation:**
- Caching layer: `backend/cache.py` with Redis
- Cache decorator for agent runner
- Database indexes: `url`, `source`, `created_at`, `user_id`
- Frontend: aggressive localStorage usage
- Docker: add Redis service to compose
- Dependencies: `redis`, `python-redis`

---

### 13.2 — Retry Logic & Error Recovery
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 1-2 days

**Features:**
- ☐ Exponential backoff for failed searches
- ☐ Partial result return (some sources succeed, others fail gracefully)
- ☐ Better error reporting to users
- ☐ Automatic retry on next scheduled run

**Implementation:**
- Enhance retry logic in `backend/agent.py`
- Better error handling in scouts
- User notification for partial failures
- Toast notification system for errors

---

### 13.3 — Rate Limiting & Throttling
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 1 day

**Features:**
- ☐ Rate limit agent runs (e.g., max 1 per 30 min per user)
- ☐ Throttle job board requests to avoid blocks
- ☐ Queue mechanism for heavy load
- ☐ Rate limit display in UI ("Run again in 25min")

**Implementation:**
- Rate limit middleware: `backend/rate_limit.py`
- Queue system (simple DB-backed or Celery)
- Integrate into `/api/pull` endpoint
- Show remaining time in UI

---

## Phase 7: User Experience & Accessibility (2 weeks)

### 14.1 — Advanced Search Operators & Query Language
**Impact:** ⭐⭐ | **Complexity:** Medium | **Effort:** 1-2 days

**Features:**
- ☐ Support `"exact phrase"` searches
- ☐ Boolean operators: `AND`, `OR`, `NOT`
- ☐ Wildcard matching: `Senior*`, `Python?`
- ☐ Field-specific search: `title:Engineer location:NYC`
- ☐ Search history + autocomplete

**Implementation:**
- Query parser: `backend/query_parser.py`
- Enhance agent prompts to handle complex queries
- Search input with suggestions
- Search history sidebar

---

### 14.2 — Keyboard Shortcuts & Command Palette
**Impact:** ⭐⭐⭐ | **Complexity:** Low | **Effort:** 1-2 days

**Features:**
- ☐ Command palette (`Cmd+K` or `Ctrl+K`)
- ☐ Quick shortcuts: `? ` (help), `n` (new search), `b` (bookmarks), `a` (applications)
- ☐ Keyboard navigation in all lists
- ☐ Focus trapping in modals

**Implementation:**
- Command palette: `frontend/src/components/CommandPalette.jsx`
- Keyboard handlers in `frontend/src/App.jsx`
- Shortcut help modal
- Use `kbar` or `cmdk` library (optional)

---

### 14.3 — Settings & Preferences Page
**Impact:** ⭐⭐⭐ | **Complexity:** Medium | **Effort:** 1-2 days

**Features:**
- ☐ Centralized settings page with tabs
- ☐ Email preferences (digest frequency, unsubscribe)
- ☐ Integration settings (Slack, Discord, webhooks)
- ☐ API key management
- ☐ Privacy settings (data retention, export)
- ☐ Account deletion option

**Implementation:**
- New page: `frontend/src/pages/Settings.jsx`
- Tabs for: Email, Integrations, API, Privacy, Account
- Endpoint: `/api/settings` (GET/PATCH)

---

## Implementation Priority Matrix

| Feature | Phase | Impact | Effort | Timeline | Status |
|---------|-------|--------|--------|----------|--------|
| Saved Searches | 1 | ⭐⭐⭐⭐ | 2-3d | Week 1-2 | ☐ |
| Job Bookmarking | 1 | ⭐⭐⭐⭐ | 1d | Week 1 | ☐ |
| Dark Mode | 1 | ⭐⭐⭐ | 0.5d | Week 1 | ☐ |
| Export (CSV/PDF) | 1 | ⭐⭐⭐ | 1d | Week 1 | ☐ |
| Application Status | 1 | ⭐⭐⭐⭐⭐ | 2d | Week 2 | ☐ |
| Skills Gap Analysis | 2 | ⭐⭐⭐⭐ | 3-4d | Week 3-4 | ☐ |
| Matching Score | 2 | ⭐⭐⭐⭐⭐ | 3-5d | Week 4-5 | ☐ |
| Salary Extraction | 2 | ⭐⭐⭐⭐ | 2-3d | Week 3 | ☐ |
| Company Insights | 2 | ⭐⭐⭐ | 2-3d | Week 3 | ☐ |
| Cover Letter Gen | 3 | ⭐⭐⭐⭐ | 3-4d | Week 6-7 | ☐ |
| Interview Scheduler | 3 | ⭐⭐⭐⭐ | 2-3d | Week 6 | ☐ |
| Multiple Resumes | 3 | ⭐⭐⭐ | 2d | Week 7 | ☐ |
| Job Comparison | 3 | ⭐⭐⭐ | 2-3d | Week 7 | ☐ |
| Analytics Dashboard | 4 | ⭐⭐⭐ | 2-3d | Week 8 | ☐ |
| Email Digest | 5 | ⭐⭐⭐⭐ | 3-4d | Week 9-10 | ☐ |
| Webhooks/API | 5 | ⭐⭐⭐ | 2-3d | Week 10 | ☐ |
| Slack Integration | 5 | ⭐⭐⭐ | 2-3d | Week 11 | ☐ |
| Caching & Perf | 6 | ⭐⭐⭐ | 2-3d | Week 12 | ☐ |
| Command Palette | 7 | ⭐⭐ | 1-2d | Week 13 | ☐ |

---

## Architecture Recommendations

### Backend
- **Background Jobs:** APScheduler for saved search runs, digests, reminders
- **Task Queue:** Optional Celery for heavy operations at scale
- **Caching:** Redis for job results and frequently-accessed data
- **Notifications:** Email (SendGrid/Mailgun) + in-app toast system
- **API Versioning:** Separate `/api/v1/` for public API

### Frontend
- **State Management:** Consider Redux/Zustand as complexity grows
- **Component Library:** Headless UI components for accessibility
- **Type Safety:** Migrate to TypeScript for better DX
- **Testing:** E2E tests for critical flows (Cypress/Playwright)

### Database
- **Indexes:** Create on `(user_id, created_at)`, `url`, `source`, `status`
- **Partitioning:** Consider partitioning `jobs` table by date if > 10M rows
- **Backups:** Automated daily backups on Neon

---

## Security & Privacy Considerations

- API keys stored **encrypted** in DB with `cryptography` library
- Webhook payloads **not stored**; only delivery logs
- Email preferences with secure **unsubscribe tokens**
- Rate limiting to prevent brute force / scraping abuse
- CORS configuration for webhook delivery
- **No PII in analytics snapshots**
- GDPR compliance: data export + account deletion

---

## Success Metrics

1. **User Engagement:** DAU, avg session time, feature adoption %
2. **Application Conversion:** % of users applying, offers received
3. **Retention:** Monthly return rate, churn rate
4. **Performance:** Page load < 2s, agent response < 60s
5. **Data Quality:** Job accuracy, freshness compliance

---

## Notes

- All enhancements maintain the Notion-inspired light design system
- No breaking changes to existing APIs or UI structure
- Features designed for graceful degradation (work offline when possible)
- Prioritize mobile-first for each feature
- Keep documentation in sync with implementation in app_spec.md and CLAUDE.md
