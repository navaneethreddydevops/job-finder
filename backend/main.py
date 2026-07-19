import os
import json
import asyncio
from collections import deque
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import logfire
import sys

sys.path.append(os.path.dirname(__file__))
from agent import run_job_finder_agent, ALLOWED_MODELS, DEFAULT_MODEL
from search_tools import add_known_urls
from db import (
    init_db,
    get_db_connection,
    save_job,
    get_user_jobs,
    toggle_applied,
    delete_user_jobs,
    get_pull_checkpoint,
    upsert_pull_checkpoint,
)
from auth import init_auth_db, router as auth_router, get_current_user
from resume import init_resume_db, router as resume_router
from applications import router as applications_router
from profile_api import router as profile_router
from apply_agent import router as apply_agent_router, apply_agent_available, recover_stale_apply_runs
from rate_limit import enforce_rate_limit, RateLimitConfig

# Initialize database schema
init_db()
init_auth_db()
init_resume_db()

app = FastAPI(
    title="Job Finder Backend",
    description=(
        "Autonomous job-finder agent + REST/SSE API.\n\n"
        "**Authentication:** most endpoints require a bearer token. Click the "
        "**Authorize** button and sign in with the seeded test account — "
        "`username: test@test.com`, `password: testtest` — to exercise the "
        "protected endpoints directly from this page. The Authorize dialog posts "
        "to `/api/token` and attaches the returned token to every request."
    ),
)

# Observability — Pydantic Logfire. Sends only when credentials exist: the local
# .logfire/ credentials file (from `logfire auth` + `logfire projects use`) or a
# LOGFIRE_TOKEN env var/secret in prod. Without either, telemetry is a no-op so
# the app still boots (tests, fresh clones, CI).
logfire.configure(
    service_name="job-finder-backend",
    send_to_logfire="if-token-present" if not os.path.exists(
        os.path.join(os.path.dirname(__file__), "..", ".logfire", "logfire_credentials.json")
    ) else True,
)
logfire.instrument_fastapi(app, capture_headers=False)
logfire.instrument_system_metrics()
# Traces any direct anthropic-client calls. Note: the job agent + resume optimizer
# go through the Claude Agent SDK (spawned `claude` CLI subprocess), which this
# does NOT capture — only in-process `anthropic.Anthropic()` usage.
logfire.instrument_anthropic()

# Core routers (job search, auth, resume, applications, profile, apply agent)
app.include_router(auth_router)
app.include_router(resume_router)
app.include_router(applications_router)
app.include_router(profile_router)
app.include_router(apply_agent_router)

# Apply runs are in-memory background tasks: anything left mid-flight by a previous
# process is unfinishable — mark those rows failed so the UI doesn't spin forever.
recover_stale_apply_runs()

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Backend state variables
agent_status = {"status": "idle", "query": None, "session_id": None, "model": None}
log_queues = []
LOG_HISTORY_MAX = 1500
# Per-client SSE queue bound. A healthy client drains far faster than the agent
# emits, so this only bites when a connection has stalled — where the oldest
# lines are dropped for that client instead of buffering the whole run in RAM.
LOG_QUEUE_MAX = LOG_HISTORY_MAX
# In-memory buffer of the current run's log lines so a client that reconnects
# (e.g. after a browser refresh) can replay what was already emitted. Bounded to
# cap memory; the run itself is in-memory, so surviving a browser refresh — not a
# server restart — is the intended scope. Uses deque with maxlen for efficient append.
# Entries are (seq, msg) tuples so reconnecting clients can resume from the last
# line they saw (SSE Last-Event-ID) instead of re-dumping the whole buffer.
log_history: deque = deque(maxlen=LOG_HISTORY_MAX)
# Monotonic, never-reset sequence id stamped on every published log line. It powers
# SSE resume: an EventSource that drops (platform request-duration cap, proxy blip)
# reconnects with Last-Event-ID = the last seq it received, and the stream replays
# ONLY lines with a greater seq — so a reconnect is seamless (no duplicated lines,
# no console reset). Kept monotonic ACROSS runs on purpose: a client holding a stale
# id from a prior run still gets the new run's (higher-seq) lines.
log_seq = 0


class PullRequest(BaseModel):
    # The Search Target role from Agent Controls — the ONLY role searched. If empty,
    # the agent falls back to the default Principal roles in agent.py.
    query: str = ""
    # Job type filters: which types of jobs to search for
    job_types: list[str] = ["fulltime", "remote"]
    # Time period in days: how far back to search (7-90 days)
    time_period_days: int = 7
    # Orchestrator model (dashboard Model picker). Must be one of agent.ALLOWED_MODELS;
    # unknown values fall back to DEFAULT_MODEL so older clients keep working.
    model: str = DEFAULT_MODEL


async def publish_log(msg: str):
    """Broadcasts a log message to all active stream connections and buffers it so
    reconnecting clients can replay the current run's history. Deque automatically
    evicts oldest entries when maxlen is exceeded."""
    global log_seq
    print(msg, flush=True)
    log_seq += 1
    item = (log_seq, msg)
    log_history.append(item)
    for q in list(log_queues):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            # Stalled client: evict its oldest line to make room, never block
            # the agent run on a slow consumer.
            try:
                q.get_nowait()
                q.put_nowait(item)
            except Exception:
                pass
        except Exception:
            pass


def _effective_window_days(user_id: int, query: str, time_period_days: int) -> int:
    """Incremental search: if this user+query completed a run before, narrow the search
    window to the time since that run (+12h buffer for late-indexed posts), floored at
    1 day and never wider than the requested window. First run → full window."""
    import math
    from datetime import datetime, timezone

    try:
        checkpoint = get_pull_checkpoint(user_id, query)
    except Exception:
        checkpoint = None
    if not checkpoint or not checkpoint.get("last_run_at"):
        return time_period_days
    try:
        last_run = datetime.strptime(checkpoint["last_run_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except Exception:
        return time_period_days
    hours_since = max(0.0, (datetime.now(timezone.utc) - last_run).total_seconds() / 3600)
    return min(time_period_days, max(1, math.ceil((hours_since + 12) / 24)))


async def run_agent_task(query: str, user_id: int, job_types: list[str] = None, time_period_days: int = 7, model: str = None):
    """Background task to run the agent and save results to the database."""
    if job_types is None:
        job_types = ["fulltime", "remote"]
    global agent_status
    import uuid

    try:
        total_jobs_count = 0

        effective_days = _effective_window_days(user_id, query, time_period_days)
        if effective_days < time_period_days:
            await publish_log(
                f"\n[Backend] Incremental search: last successful run for this query was "
                f"recent — narrowing the window from {time_period_days} to "
                f"{effective_days} day(s) to avoid re-searching old ground.\n"
            )
        time_period_days = effective_days

        async def log_callback(thought: str):
            await publish_log(thought)

        async def batch_callback(jobs_batch):
            """Persist a scout's batch of jobs the moment it finishes, so the UI fills
            in incrementally instead of waiting for the whole agent run to complete."""
            nonlocal total_jobs_count
            inserted = 0
            saved_urls = []
            for job in jobs_batch:
                job_dict = (
                    job.model_dump()
                    if hasattr(job, "model_dump")
                    else (job.dict() if hasattr(job, "dict") else dict(job))
                )
                try:
                    if save_job(job_dict, user_id):
                        inserted += 1
                    url = job_dict.get("url") or ""
                    # Dropped jobs (no valid URL) must not enter the known-urls context.
                    if url.startswith(("http://", "https://")):
                        saved_urls.append(url)
                except Exception:
                    pass
            total_jobs_count += inserted
            # Feed saved URLs back into the search tools' run context so a job found
            # via one source isn't re-surfaced by a later tool call from another.
            try:
                add_known_urls(saved_urls)
            except Exception:
                pass
            await publish_log(
                f"\n[Backend] Saved a batch of {len(jobs_batch)} jobs "
                f"({inserted} new). Database now holds {total_jobs_count} total jobs.\n"
            )

        # Initialize a new session ID every time to prevent corrupted resume states
        is_resume = False
        agent_status["session_id"] = str(uuid.uuid4())

        # Pass the tracked session_id and search parameters
        results = await run_job_finder_agent(
            query,
            user_id=user_id,
            log_callback=log_callback,
            session_id=agent_status["session_id"],
            is_resume=is_resume,
            batch_callback=batch_callback,
            job_types=job_types,
            time_period_days=time_period_days,
            model=model,
        )

        if results is None:
            await publish_log(
                "\n[Backend] Agent returned no results. This may indicate a Claude OAuth login issue or agent error.\n"
            )
        else:
            # Save results to local SQLite database
            jobs_list = []
            if hasattr(results, "jobs"):
                jobs_list = results.jobs
            elif isinstance(results, dict) and "jobs" in results:
                jobs_list = results["jobs"]

            inserted_count = 0
            for job in jobs_list:
                job_dict = (
                    job.model_dump()
                    if hasattr(job, "model_dump")
                    else (job.dict() if hasattr(job, "dict") else dict(job))
                )
                if save_job(job_dict, user_id):
                    inserted_count += 1

            updated_count = len(jobs_list) - inserted_count
            await publish_log(
                f"\n[Backend] Reconciliation: agent's final merged list had {len(jobs_list)} jobs — "
                f"{inserted_count} new, {updated_count} already saved from a batch or dropped by the "
                f"quality gate. Database now holds "
                f"{len(get_user_jobs(user_id))} total jobs.\n"
            )
            # Checkpoint ONLY on success: the next run for this query narrows its
            # window to "since this run". A failed run never narrows the next window.
            try:
                upsert_pull_checkpoint(user_id, query, len(jobs_list))
            except Exception:
                pass
    except Exception as e:
        await publish_log(f"\n[Backend Error] Agent failed: {e}\n")
    finally:
        agent_status["status"] = "idle"
        agent_status["query"] = None
        # Keep the session_id alive for the next request


class ApplyRequest(BaseModel):
    applied: bool


@app.get("/api/jobs")
async def get_jobs(user: dict = Depends(get_current_user)):
    """Returns the list of jobs for the authenticated user."""
    try:
        from db import get_user_applications

        jobs = get_user_jobs(user["id"])
        applications = get_user_applications(user["id"])

        # Create a lookup map: job_id -> application status
        app_map = {app["job_id"]: app for app in applications}

        # Enrich each job with application status
        for job in jobs:
            app = app_map.get(job["id"])
            if app:
                job["application_status"] = app["status"]
                job["application_id"] = app["id"]
            else:
                job["application_status"] = None
                job["application_id"] = None

        return {"jobs": jobs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading database: {e}")


@app.patch("/api/jobs/{job_id}/apply")
async def mark_applied(job_id: int, req: ApplyRequest, user: dict = Depends(get_current_user)):
    """Marks a job as applied or not applied for the authenticated user."""
    try:
        toggle_applied(user["id"], job_id, req.applied)
        return {"success": True, "job_id": job_id, "applied": req.applied}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating job: {e}")


@app.post("/api/jobs/clear")
async def clear_jobs(user: dict = Depends(get_current_user)):
    """Clears all jobs for the authenticated user."""
    try:
        delete_user_jobs(user["id"])
        return {"success": True, "message": "Your jobs cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing database: {e}")


def _check_database() -> bool:
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"Health check: database unreachable: {e}", file=sys.stderr)
        return False


@app.get("/api/health")
async def health_check(response: Response):
    """Real health status: exercises the database, reports per-component state."""
    db_ok = await asyncio.to_thread(_check_database)
    operational = db_ok
    if not operational:
        response.status_code = 503
    return {
        "status": "operational" if operational else "degraded",
        "components": {
            "api": "ok",
            "database": "ok" if db_ok else "error",
        },
        "agent": agent_status.get("status", "idle"),
    }


@app.get("/api/status")
async def get_status():
    """Returns the current running status of the job finder agent."""
    return {**agent_status, "apply_agent_available": apply_agent_available()}


@app.get("/api/jobs/export")
async def export_jobs(
    format: str = "csv",
    user: dict = Depends(get_current_user),
):
    """Export jobs as CSV or JSON."""
    try:
        import csv
        import io
        from datetime import datetime

        jobs = get_user_jobs(user["id"])

        if format.lower() == "json":
            # Export as JSON
            export_data = {
                "exported_at": datetime.utcnow().isoformat(),
                "total_jobs": len(jobs),
                "jobs": jobs,
            }
            return export_data

        elif format.lower() == "csv":
            # Export as CSV
            output = io.StringIO()
            if jobs:
                fieldnames = [
                    "title",
                    "company",
                    "location",
                    "source",
                    "date_posted",
                    "url",
                    "applied",
                ]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for job in jobs:
                    writer.writerow(
                        {
                            "title": job.get("title", ""),
                            "company": job.get("company", ""),
                            "location": job.get("location", ""),
                            "source": job.get("source", ""),
                            "date_posted": job.get("date_posted", ""),
                            "url": job.get("url", ""),
                            "applied": "Yes" if job.get("applied") else "No",
                        }
                    )
            return {
                "csv": output.getvalue(),
                "filename": f"jobs_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported format. Use 'csv' or 'json'",
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@app.post("/api/pull")
async def pull_jobs(req: PullRequest, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    """Triggers the job finder agent in the background for the authenticated user."""
    # Enforce rate limit: max 1 agent run per 30 minutes per user
    rate_limit_info = enforce_rate_limit(
        user["id"],
        "/api/pull",
        limit=RateLimitConfig.PULL_AGENT,
        window=RateLimitConfig.PULL_AGENT_WINDOW,
    )

    global agent_status
    if agent_status["status"] == "running":
        raise HTTPException(
            status_code=400, detail="Agent is already running. Please wait."
        )

    model = req.model if req.model in ALLOWED_MODELS else DEFAULT_MODEL

    agent_status["status"] = "running"
    agent_status["query"] = req.query
    agent_status["model"] = model
    # Start each run with a clean log buffer so the console doesn't replay a stale run.
    log_history.clear()

    background_tasks.add_task(run_agent_task, req.query, user["id"], req.job_types, req.time_period_days, model)
    return {
        "message": "Job pulling started",
        "query": req.query,
        "model": model,
        "rate_limit": {
            "remaining": rate_limit_info["remaining"],
            "reset_at": rate_limit_info["reset_at"],
        },
    }


@app.get("/api/stream")
async def stream_logs(request: Request, last_event_id: str | None = None):
    """Server-Sent Events (SSE) endpoint to stream agent thought logs in real time.

    Resumable: a reconnecting client tells us the last line it saw so we replay ONLY
    newer ones (no duplicates, no console reset). Native EventSource auto-reconnects
    send that via the ``Last-Event-ID`` header; our manual reconnect passes it as the
    ``last_event_id`` query param. The header is authoritative when present (it's the
    freshest on a native reconnect); the query param seeds the very first open.
    """
    resume_raw = request.headers.get("last-event-id") or last_event_id
    try:
        resume_seq = int(resume_raw) if resume_raw else 0
    except (TypeError, ValueError):
        resume_seq = 0

    async def event_generator():
        q = asyncio.Queue(maxsize=LOG_QUEUE_MAX)
        # Snapshot history and register the live queue with NO await between them:
        # both are synchronous, so in single-threaded asyncio no other coroutine can
        # publish in the gap — the replay contains no lost or duplicated lines.
        history_snapshot = list(log_history)
        log_queues.append(q)
        try:
            # Comment line (ignored by EventSource) just to open the stream promptly.
            yield ": connected\n\n"
            # Replay the current run's buffered logs the client hasn't seen yet, so a
            # reconnect (page refresh, dropped connection) repopulates seamlessly.
            for seq, msg in history_snapshot:
                if seq <= resume_seq:
                    continue
                yield f"id: {seq}\ndata: {json.dumps({'message': msg})}\n\n"
            while True:
                # Wait for the next log line, but never sit silent for long: proxies and
                # load balancers idle-close quiet connections, which showed up as the UI
                # "disconnecting" mid-run. An SSE comment line (": keep-alive") is ignored
                # by EventSource but keeps the connection alive through intermediaries.
                try:
                    seq, msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if seq <= resume_seq:
                    continue
                yield f"id: {seq}\ndata: {json.dumps({'message': msg})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in log_queues:
                log_queues.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            # Defeat proxy/CDN buffering so lines (and keep-alives) reach the browser
            # immediately; without these, intermediaries buffer the stream and the
            # connection looks dead until it's idle-closed.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Serve React frontend build files in production (look in the parent directory)
frontend_dist = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "dist"
)
if os.path.exists(frontend_dist):
    from fastapi.responses import FileResponse

    # Serve hashed assets directly.
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # SPA fallback: any non-/api path returns index.html so client-side routes
    # (e.g. /login, /resume/optimizer) work on direct navigation / refresh.
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = os.path.join(frontend_dist, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
else:

    @app.get("/")
    async def index():
        return {
            "message": "Job Finder Backend is running.",
            "status": "Ok.",
        }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
