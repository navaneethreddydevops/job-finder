import os
import json
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sys

sys.path.append(os.path.dirname(__file__))
from agent import run_job_finder_agent
from db import (
    init_db,
    save_job,
    get_user_jobs,
    toggle_applied,
    delete_user_jobs,
    get_application_stats,
)
from auth import init_auth_db, router as auth_router, get_current_user
from resume import init_resume_db, router as resume_router
from applications import router as applications_router
from bookmarks import router as bookmarks_router
from searches import router as searches_router
from salary import router as salary_router
from skills import router as skills_router
from scoring import router as scoring_router, init_scoring_db
from cover_letters import router as cover_letters_router, init_cover_letter_db
from interviews import router as interviews_router, init_interview_db
from comparison import router as comparison_router
from analytics import router as analytics_router, init_analytics_db
from email_service import router as email_router
from webhooks import router as webhooks_router, init_webhooks_db
from integrations import router as integrations_router, init_integrations_db
from rate_limit import enforce_rate_limit, RateLimitConfig

# Initialize database schema
init_db()
init_auth_db()
init_resume_db()
init_scoring_db()
init_cover_letter_db()
init_interview_db()
init_analytics_db()
init_webhooks_db()
init_integrations_db()

app = FastAPI(title="Job Finder Backend")

# All routers
app.include_router(auth_router)
app.include_router(resume_router)
app.include_router(applications_router)
app.include_router(bookmarks_router)
app.include_router(searches_router)
app.include_router(salary_router)
app.include_router(skills_router)
app.include_router(scoring_router)
app.include_router(cover_letters_router)
app.include_router(interviews_router)
app.include_router(comparison_router)
app.include_router(analytics_router)
app.include_router(email_router)
app.include_router(webhooks_router)
app.include_router(integrations_router)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Backend state variables
agent_status = {"status": "idle", "query": None, "session_id": None}
log_queues = []
# In-memory buffer of the current run's log lines so a client that reconnects
# (e.g. after a browser refresh) can replay what was already emitted. Bounded to
# cap memory; the run itself is in-memory, so surviving a browser refresh — not a
# server restart — is the intended scope.
log_history: list[str] = []
LOG_HISTORY_MAX = 1500


class PullRequest(BaseModel):
    # Optional: the agent always searches the default Principal roles; a non-empty
    # query is added as an extra role on top of those defaults.
    query: str = ""
    # Job type filters: which types of jobs to search for
    job_types: list[str] = ["fulltime", "remote"]
    # Time period in days: how far back to search (7-90 days)
    time_period_days: int = 7


async def publish_log(msg: str):
    """Broadcasts a log message to all active stream connections and buffers it so
    reconnecting clients can replay the current run's history."""
    print(msg, flush=True)
    log_history.append(msg)
    if len(log_history) > LOG_HISTORY_MAX:
        del log_history[: len(log_history) - LOG_HISTORY_MAX]
    for q in list(log_queues):
        try:
            await q.put(msg)
        except Exception:
            pass


async def run_agent_task(query: str, user_id: int, job_types: list[str] = None, time_period_days: int = 7):
    """Background task to run the agent and save results to the SQLite database."""
    if job_types is None:
        job_types = ["fulltime", "remote"]
    global agent_status
    import uuid

    try:

        async def log_callback(thought: str):
            await publish_log(thought)

        async def batch_callback(jobs_batch):
            """Persist a scout's batch of jobs the moment it finishes, so the UI fills
            in incrementally instead of waiting for the whole agent run to complete."""
            inserted = 0
            for job in jobs_batch:
                job_dict = (
                    job.model_dump()
                    if hasattr(job, "model_dump")
                    else (job.dict() if hasattr(job, "dict") else dict(job))
                )
                try:
                    if save_job(job_dict, user_id):
                        inserted += 1
                except Exception:
                    pass
            await publish_log(
                f"\n[Backend] Saved a batch of {len(jobs_batch)} jobs "
                f"({inserted} new). Database now holds {len(get_user_jobs(user_id))} total jobs.\n"
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
                f"{inserted_count} new, {updated_count} already saved from a batch. Database now holds "
                f"{len(get_user_jobs(user_id))} total jobs.\n"
            )
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


@app.get("/api/health")
async def health_check():
    """Returns the health status of the backend."""
    return {"status": "ok"}


@app.get("/api/status")
async def get_status():
    """Returns the current running status of the job finder agent."""
    return agent_status


@app.get("/api/applications/stats")
async def get_application_stats_endpoint(user: dict = Depends(get_current_user)):
    """Returns application statistics for the current user."""
    try:
        stats = get_application_stats(user["id"])
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading stats: {e}")


@app.get("/api/jobs/export")
async def export_jobs(
    format: str = "csv",
    user: dict = Depends(get_current_user),
):
    """Export jobs as CSV or JSON."""
    try:
        import json
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

    agent_status["status"] = "running"
    agent_status["query"] = req.query
    # Start each run with a clean log buffer so the console doesn't replay a stale run.
    log_history.clear()

    background_tasks.add_task(run_agent_task, req.query, user["id"], req.job_types, req.time_period_days)
    return {
        "message": "Job pulling started",
        "query": req.query,
        "rate_limit": {
            "remaining": rate_limit_info["remaining"],
            "reset_at": rate_limit_info["reset_at"],
        },
    }


@app.get("/api/stream")
async def stream_logs():
    """Server-Sent Events (SSE) endpoint to stream agent thought logs in real time."""

    async def event_generator():
        q = asyncio.Queue()
        # Snapshot history and register the live queue with NO await between them:
        # both are synchronous, so in single-threaded asyncio no other coroutine can
        # publish in the gap — the replay contains no lost or duplicated lines.
        history_snapshot = list(log_history)
        log_queues.append(q)
        try:
            # Yield initial status message
            yield f"data: {json.dumps({'message': '[Connection Established] Connected to agent logs stream.'})}\n\n"
            # Replay the current run's buffered logs so a reconnecting client (e.g. after
            # a page refresh) sees everything emitted before it connected.
            for msg in history_snapshot:
                yield f"data: {json.dumps({'message': msg})}\n\n"
            while True:
                msg = await q.get()
                yield f"data: {json.dumps({'message': msg})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in log_queues:
                log_queues.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
