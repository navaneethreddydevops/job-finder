#!/usr/bin/env bash
# Deploy the Playwright MCP sidecar to Google Cloud Run (free-tier friendly).
#
# Prereqs (one time):
#   gcloud auth login
#   gcloud config set project <PROJECT_ID>          # project must have OPEN billing linked
#   gcloud config set run/region us-central1
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
#
# Usage:
#   ./deploy-cloudrun.sh                # generates a fresh PLAYWRIGHT_MCP_TOKEN
#   PLAYWRIGHT_MCP_TOKEN=... ./deploy-cloudrun.sh   # reuse an existing token (redeploys)
#
# Afterward, set on FastAPI Cloud (Project Settings -> Secrets):
#   PLAYWRIGHT_MCP_URL   = the printed service URL (no trailing slash)
#   PLAYWRIGHT_MCP_TOKEN = the printed token
# then `fastapi deploy` the backend and check /api/status -> apply_agent_available: true.
set -euo pipefail
cd "$(dirname "$0")"

SERVICE="${SERVICE:-job-finder-playwright-mcp}"
TOKEN="${PLAYWRIGHT_MCP_TOKEN:-$(openssl rand -hex 32)}"

# --max-instances 1 + --session-affinity: MCP sessions are stateful (a live browser
# context), so every request of a run must land on the same instance.
# --min-instances 0 keeps it inside the always-free tier; first apply pays a cold start.
gcloud run deploy "$SERVICE" \
  --source . \
  --memory 2Gi --cpu 2 \
  --min-instances 0 --max-instances 1 \
  --concurrency 20 --timeout 300 \
  --session-affinity \
  --allow-unauthenticated \
  --set-env-vars "PLAYWRIGHT_MCP_TOKEN=${TOKEN}"

URL=$(gcloud run services describe "$SERVICE" --format='value(status.url)')
echo
echo "Sidecar deployed."
echo "  PLAYWRIGHT_MCP_URL   = ${URL}"
echo "  PLAYWRIGHT_MCP_TOKEN = ${TOKEN}"
echo
echo "Health check:"
# /health, not /healthz — Google's front end intercepts /healthz on *.run.app.
curl -fsS "${URL}/health" && echo
