import os
import json
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sys
sys.path.append(os.path.dirname(__file__))
from agent import run_job_finder_agent
from db import init_db, save_job, get_all_jobs, toggle_applied, delete_all_jobs

# Initialize database schema
init_db()

app = FastAPI(title="C2C Job Finder Backend")

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Backend state variables
agent_status = {"status": "idle", "query": None}
log_queues = []

class PullRequest(BaseModel):
    query: str

async def publish_log(msg: str):
    """Broadcasts a log message to all active stream connections."""
    for q in list(log_queues):
        try:
            await q.put(msg)
        except Exception:
            pass

async def run_agent_task(query: str):
    """Background task to run the agent and save results to the SQLite database."""
    global agent_status
    try:
        async def log_callback(thought: str):
            await publish_log(thought)
        
        results = await run_job_finder_agent(query, log_callback=log_callback)
        
        # Save results to local SQLite database
        jobs_list = []
        if hasattr(results, "jobs"):
            jobs_list = results.jobs
        elif isinstance(results, dict) and "jobs" in results:
            jobs_list = results["jobs"]
        
        for job in jobs_list:
            job_dict = job.model_dump() if hasattr(job, "model_dump") else (job.dict() if hasattr(job, "dict") else dict(job))
            save_job(job_dict)
            
        await publish_log("\n[Backend] Jobs list successfully updated in the SQLite database!\n")
    except Exception as e:
        await publish_log(f"\n[Backend Error] Agent failed: {e}\n")
    finally:
        agent_status["status"] = "idle"
        agent_status["query"] = None

class ApplyRequest(BaseModel):
    applied: bool

@app.get("/api/jobs")
async def get_jobs():
    """Returns the list of jobs currently saved in the database."""
    try:
        jobs = get_all_jobs()
        return {"jobs": jobs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading database: {e}")

@app.patch("/api/jobs/{job_id}/apply")
async def mark_applied(job_id: int, req: ApplyRequest):
    """Marks a job as applied or not applied."""
    try:
        toggle_applied(job_id, req.applied)
        return {"success": True, "job_id": job_id, "applied": req.applied}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating job: {e}")

@app.post("/api/jobs/clear")
async def clear_jobs():
    """Clears all jobs from the database."""
    try:
        delete_all_jobs()
        return {"success": True, "message": "All jobs cleared from database."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing database: {e}")

@app.get("/api/status")
async def get_status():
    """Returns the current running status of the job finder agent."""
    return agent_status

@app.post("/api/pull")
async def pull_jobs(req: PullRequest, background_tasks: BackgroundTasks):
    """Triggers the job finder agent in the background."""
    global agent_status
    if agent_status["status"] == "running":
        raise HTTPException(status_code=400, detail="Agent is already running. Please wait.")
    
    agent_status["status"] = "running"
    agent_status["query"] = req.query
    
    background_tasks.add_task(run_agent_task, req.query)
    return {"message": "Job pulling started", "query": req.query}

@app.get("/api/stream")
async def stream_logs():
    """Server-Sent Events (SSE) endpoint to stream agent thought logs in real time."""
    async def event_generator():
        q = asyncio.Queue()
        log_queues.append(q)
        try:
            # Yield initial status message
            yield f"data: {json.dumps({'message': '[Connection Established] Connected to agent logs stream.'})}\n\n"
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
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")
else:
    @app.get("/")
    async def index():
        return {
            "message": "C2C Job Finder Backend is running.",
            "status": "Vite development frontend has not been compiled yet or should be run separately."
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
