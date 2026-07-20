"""Application status tracking endpoints."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from db import (
    create_application,
    update_application_status,
    get_application,
    get_user_applications,
    get_application_stats,
    delete_application,
    get_job_for_user,
)
from auth import get_current_user

router = APIRouter(prefix="/api", tags=["applications"])


class ApplicationCreate(BaseModel):
    job_id: int
    status: str = "draft"  # draft, applied, interviewing, offer, rejected
    cover_letter: str | None = None


class ApplicationUpdate(BaseModel):
    status: str
    notes: str | None = None


class ApplicationResponse(BaseModel):
    id: int
    user_id: int
    job_id: int
    status: str
    cover_letter: str | None
    applied_at: str | None
    created_at: str
    updated_at: str
    title: str | None
    company: str | None
    location: str | None
    url: str | None
    source: str | None
    # Autonomous apply-agent lane (Task 10). apply_status is the machine status
    # (queued|running|submitted|needs_review|failed), orthogonal to `status`.
    apply_method: str | None = "manual"
    apply_status: str | None = ""
    apply_error: str | None = ""
    apply_started_at: str | None = None
    apply_finished_at: str | None = None


class ApplicationDetailResponse(ApplicationResponse):
    history: list[dict] | None = None


class ApplicationStatsResponse(BaseModel):
    total_applications: int
    applied_count: int
    interviewing_count: int
    offer_count: int
    rejected_count: int


@router.post("/applications", response_model=dict)
async def create_new_application(
    data: ApplicationCreate, user: dict = Depends(get_current_user)
):
    """Create a new application record."""

    try:
        # Verify user owns this job
        job = get_job_for_user(data.job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        app_id = create_application(
            user_id=user["id"],
            job_id=data.job_id,
            status=data.status,
            cover_letter=data.cover_letter,
        )
        return {
            "id": app_id,
            "status": data.status,
            "job_id": data.job_id,
            "message": "Application created successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create application: {str(e)}",
        )


@router.patch("/applications/{application_id}")
async def update_application(
    application_id: int, data: ApplicationUpdate, user: dict = Depends(get_current_user)
):
    """Update application status."""

    try:
        # Verify ownership
        app = get_application(application_id)
        if not app or app["user_id"] != user["id"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Application not found",
            )

        update_application_status(application_id, data.status, data.notes)
        updated = get_application(application_id)
        return {
            "id": updated["id"],
            "status": updated["status"],
            "updated_at": updated["updated_at"],
            "message": "Application updated successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update application: {str(e)}",
        )


@router.get("/applications/stats", response_model=ApplicationStatsResponse)
async def get_stats(user: dict = Depends(get_current_user)):
    """Get application statistics for the user."""

    stats = get_application_stats(user["id"])
    return ApplicationStatsResponse(**stats)


@router.get("/applications/{application_id}", response_model=ApplicationDetailResponse)
async def get_application_detail(application_id: int, user: dict = Depends(get_current_user)):
    """Get application details with history."""

    app = get_application(application_id)
    if not app or app["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    return ApplicationDetailResponse(**app)


@router.get("/applications", response_model=list[ApplicationResponse])
async def list_user_applications(
    include_history: bool = False, user: dict = Depends(get_current_user)
):
    """Get all applications for the current user."""

    applications = get_user_applications(user["id"], include_history=include_history)
    return [ApplicationResponse(**app) for app in applications]


@router.delete("/applications/{application_id}")
async def delete_application_record(application_id: int, user: dict = Depends(get_current_user)):
    """Delete an application."""

    try:
        delete_application(application_id, user["id"])
        return {"message": "Application deleted successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete application: {str(e)}",
        )
