"""Job comparison and analysis."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import get_db_connection, get_job_for_user
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["comparison"])


class ComparisonResponse(BaseModel):
    jobs: list[dict]
    comparison_metrics: dict


@router.post("/compare")
async def create_comparison(data: dict, user: dict = Depends(get_current_user)):
    """Compare multiple jobs."""
    try:
        job_ids = data.get("job_ids", [])
        if len(job_ids) < 2:
            raise HTTPException(status_code=400, detail="Need at least 2 jobs to compare")

        conn = get_db_connection()
        cursor = conn.cursor()

        jobs = []
        for job_id in job_ids:
            cursor.execute(
                "SELECT * FROM jobs WHERE id = ? AND user_id = ?",
                (job_id, user["id"]),
            )
            row = cursor.fetchone()
            if row:
                jobs.append(dict(row))

        conn.close()

        if len(jobs) != len(job_ids):
            raise HTTPException(status_code=404, detail="Some jobs not found")

        # Calculate comparison metrics
        salary_min_vals = [j.get("salary_min", 0) for j in jobs if j.get("salary_min")]
        salary_max_vals = [j.get("salary_max", 0) for j in jobs if j.get("salary_max")]

        metrics = {
            "avg_salary_min": sum(salary_min_vals) / len(salary_min_vals) if salary_min_vals else None,
            "avg_salary_max": sum(salary_max_vals) / len(salary_max_vals) if salary_max_vals else None,
            "remote_count": len([j for j in jobs if "remote" in (j.get("location") or "").lower()]),
            "total_jobs": len(jobs),
        }

        return {
            "jobs": jobs,
            "comparison_metrics": metrics,
            "message": "Comparison created successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create comparison: {str(e)}")


@router.post("/compare/export")
async def export_comparison(data: dict, user: dict = Depends(get_current_user)):
    """Export comparison as PDF/CSV."""
    try:
        job_ids = data.get("job_ids", [])
        format_type = data.get("format", "csv")

        # Fetch jobs
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(job_ids))}) AND user_id = ?",
            (*job_ids, user["id"]),
        )
        jobs = [dict(row) for row in cursor.fetchall()]
        conn.close()

        if format_type == "csv":
            # Create CSV content
            lines = ["Job Title,Company,Location,Salary Min,Salary Max,Source,C2C Status"]
            for job in jobs:
                lines.append(
                    f'{job.get("title")},{job.get("company")},{job.get("location")},'
                    f'{job.get("salary_min")},{job.get("salary_max")},'
                    f'{job.get("source")},{job.get("c2c_viability")}'
                )
            content = "\n".join(lines)
            filename = "job_comparison.csv"
            content_type = "text/csv"
        else:
            content = str(jobs)
            filename = "job_comparison.json"
            content_type = "application/json"

        return {
            "content": content,
            "filename": filename,
            "content_type": content_type,
            "message": "Comparison exported successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to export comparison: {str(e)}")
