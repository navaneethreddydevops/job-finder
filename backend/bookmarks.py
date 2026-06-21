"""Job bookmarking endpoints."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import (
    toggle_bookmark,
    is_bookmarked,
    get_user_bookmarks,
    get_bookmark_count,
    get_job_for_user,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["bookmarks"])


class BookmarkToggleResponse(BaseModel):
    is_bookmarked: bool
    job_id: int
    message: str


class BookmarksListResponse(BaseModel):
    bookmarks: list[dict]
    count: int


@router.post("/jobs/{job_id}/bookmark")
async def toggle_job_bookmark(job_id: int, user: dict = Depends(get_current_user)):
    """Toggle bookmark status for a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        is_now_bookmarked = toggle_bookmark(user["id"], job_id)
        message = "Bookmarked" if is_now_bookmarked else "Bookmark removed"
        return BookmarkToggleResponse(
            is_bookmarked=is_now_bookmarked,
            job_id=job_id,
            message=message,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to toggle bookmark: {str(e)}",
        )


@router.get("/jobs/{job_id}/is-bookmarked")
async def check_bookmark(job_id: int, user: dict = Depends(get_current_user)):
    """Check if a job is bookmarked by current user."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        return {"is_bookmarked": is_bookmarked(user["id"], job_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check bookmark: {str(e)}",
        )


@router.get("/bookmarks")
async def get_bookmarks(user: dict = Depends(get_current_user)):
    """Get all bookmarked jobs for the current user."""
    try:
        bookmarks = get_user_bookmarks(user["id"])
        return BookmarksListResponse(
            bookmarks=bookmarks,
            count=len(bookmarks),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch bookmarks: {str(e)}",
        )


@router.get("/bookmarks/count")
async def get_bookmarks_count(user: dict = Depends(get_current_user)):
    """Get count of bookmarked jobs."""
    try:
        count = get_bookmark_count(user["id"])
        return {"count": count}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get bookmark count: {str(e)}",
        )
