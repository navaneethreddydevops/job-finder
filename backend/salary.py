"""Salary extraction and analysis endpoints."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import (
    update_job_salary,
    get_salary_stats,
    get_jobs_by_salary_range,
    get_job_for_user,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["salary"])


class SalaryUpdate(BaseModel):
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "USD"
    confidence: float = 1.0


class SalaryStatsResponse(BaseModel):
    min_salary: float | None
    max_salary: float | None
    avg_salary: float | None
    total_jobs: int
    jobs_with_salary: int


class JobsWithSalary(BaseModel):
    jobs: list[dict]
    count: int


@router.patch("/jobs/{job_id}/salary")
async def update_job_salary_endpoint(
    job_id: int, data: SalaryUpdate, user: dict = Depends(get_current_user)
):
    """Update salary information for a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        update_job_salary(
            job_id=job_id,
            user_id=user["id"],
            salary_min=data.salary_min,
            salary_max=data.salary_max,
            currency=data.currency,
            confidence=data.confidence,
        )
        return {
            "job_id": job_id,
            "salary_min": data.salary_min,
            "salary_max": data.salary_max,
            "currency": data.currency,
            "message": "Salary updated successfully",
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update salary: {str(e)}",
        )


@router.get("/salary/stats", response_model=SalaryStatsResponse)
async def get_salary_statistics(
    source: str | None = None, user: dict = Depends(get_current_user)
):
    """Get salary statistics for the user's jobs."""
    try:
        stats = get_salary_stats(user["id"], source)
        if not stats:
            return SalaryStatsResponse(
                min_salary=None,
                max_salary=None,
                avg_salary=None,
                total_jobs=0,
                jobs_with_salary=0,
            )
        return SalaryStatsResponse(**stats)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch salary stats: {str(e)}",
        )


@router.get("/salary/range", response_model=JobsWithSalary)
async def get_jobs_in_salary_range(
    min_salary: float | None = None,
    max_salary: float | None = None,
    user: dict = Depends(get_current_user),
):
    """Get jobs within a salary range."""
    try:
        jobs = get_jobs_by_salary_range(user["id"], min_salary, max_salary)
        return JobsWithSalary(jobs=jobs, count=len(jobs))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch jobs: {str(e)}",
        )
