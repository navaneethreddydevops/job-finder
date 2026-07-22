# Playwright MCP browser sidecar

The managed backend host (FastAPI Cloud) has no Node/Chromium, so the auto-apply agent
(`backend/apply_agent.py`) drives a browser through this sidecar. `server.mjs`:

1. **launches Chromium itself** with a CDP debug port (`--remote-debugging-port`), so we
   own the browser and can attach a second CDP client for the live view;
2. **spawns `@playwright/mcp`** in streamable-HTTP mode pointed at that browser via
   `--cdp-endpoint` (`--isolated` — each MCP session gets its own browser context); and
3. **fronts both** with a small Node proxy that:
   - enforces `Authorization: Bearer $PLAYWRIGHT_MCP_TOKEN` on every route except health
     (the container refuses to start without the token) — **this token is the real auth
     gate**, which is why the platform-level endpoint can stay publicly reachable;
   - accepts resume uploads at `POST /upload` (raw body + `x-filename` header → a path
     under the MCP `--output-dir`, swept after 2 h);
   - **`GET /screencast?match=<substr>`** — a live **MJPEG** stream (`multipart/x-mixed-replace`)
     of the browser via Chrome DevTools `Page.startScreencast` (~5–10 fps JPEG). `match`
     locks the stream to the page target whose URL contains the substring (the run's job
     host), so concurrent applies each get their own view. The backend
     (`GET /api/jobs/{id}/apply-agent/live-stream`) relays this to the dashboard so the
     bearer token stays server-side. JPEG quality/size are tunable via `SC_QUALITY` /
     `SC_MAX_WIDTH` / `SC_MAX_HEIGHT`; the defaults (50 / 1024 / 768) are comfortable for
     the default 3-way apply concurrency on the 2 vCPU / 2 GiB shape below;
   - exposes an unauthenticated `GET /healthz` (`/health` alias for Cloud Run);
   - proxies everything else (`/mcp`) to the internal MCP port (rewriting `Host` to
     satisfy Playwright's DNS-rebinding guard).

> **Cloud Run 5-minute request cap:** the `/screencast` stream is cut at `--timeout 300`;
> the dashboard auto-reconnects (same pattern as the SSE log stream), so the live view
> resumes seamlessly. `--session-affinity` keeps a run's stream on the instance holding
> its browser. `ws` + `playwright-core` are installed in the image for the CDP bridge.

## Deploy — Google Cloud Run (recommended, free tier)

Cloud Run's always-free tier (180k vCPU-sec + 360k GiB-sec + 2M requests/month) covers
roughly 300 five-minute apply runs a month at the 2 vCPU / 2 GiB size below — $0 for
normal usage. Requires a GCP project with an **open** billing account linked.

```bash
# one-time setup
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud config set run/region us-central1
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# deploy (prints the URL + token to configure on FastAPI Cloud)
./deploy-cloudrun.sh
```

Sizing rationale: `--max-instances 1 --session-affinity` because MCP sessions are
stateful (a live browser context) and must always hit the same instance;
`--min-instances 0` for free-tier scale-to-zero (the first apply after idle pays a
~10–20 s cold start); 2 Gi / 2 vCPU because Chromium doesn't run reliably on the
512 MB / 0.1 vCPU shapes that no-card free tiers offer.

## Deploy — Fly.io (paid alternative)

`fly.toml` is kept for Fly.io (no free tier anymore; ~$5–10/mo for an always-on
shared-cpu-2x 2 GB machine, no cold starts):

```bash
fly launch --copy-config --no-deploy
fly secrets set PLAYWRIGHT_MCP_TOKEN=$(openssl rand -hex 32)
fly deploy
```

## Wire up the backend

On FastAPI Cloud (Project Settings → Secrets) set:

- `PLAYWRIGHT_MCP_URL` — the sidecar origin, no trailing slash
- `PLAYWRIGHT_MCP_TOKEN` — the same token

Redeploy (`fastapi deploy`); `GET /api/status` should now report
`apply_agent_available: true` and the dashboard's Auto-Apply buttons become active.

Locally no sidecar is needed — with Node/`npx` on PATH the backend spawns Playwright
MCP itself (stdio transport).
