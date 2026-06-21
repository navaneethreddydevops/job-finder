"""Saved searches endpoints."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import (
    create_saved_search,
    get_saved_searches,
    get_saved_search,
    update_saved_search,
    delete_saved_search,
    get_search_history,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["searches"])


class SavedSearchCreate(BaseModel):
    query: str
    name: str
    frequency: str = "manual"  # manual, daily, weekly


class SavedSearchUpdate(BaseModel):
    query: str | None = None
    name: str | None = None
    frequency: str | None = None
    is_active: int | None = None


class SavedSearchResponse(BaseModel):
    id: int
    user_id: int
    query: str
    name: str
    frequency: str
    is_active: int
    created_at: str
    last_run_at: str | None


class SearchRunResponse(BaseModel):
    id: int
    saved_search_id: int
    jobs_found_count: int | None
    new_jobs_count: int | None
    run_at: str
    was_emailed: int


@router.post("/searches")
async def create_search(data: SavedSearchCreate, user: dict = Depends(get_current_user)):
    """Create a new saved search."""
    try:
        search_id = create_saved_search(
            user_id=user["id"],
            query=data.query,
            name=data.name,
            frequency=data.frequency,
        )
        return {
            "id": search_id,
            "message": "Saved search created successfully",
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create saved search: {str(e)}",
        )


@router.get("/searches", response_model=list[SavedSearchResponse])
async def list_searches(user: dict = Depends(get_current_user)):
    """Get all saved searches for the current user."""
    try:
        searches = get_saved_searches(user["id"])
        return searches
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch searches: {str(e)}",
        )


@router.get("/searches/{search_id}", response_model=SavedSearchResponse)
async def get_search(search_id: int, user: dict = Depends(get_current_user)):
    """Get a specific saved search."""
    try:
        search = get_saved_search(search_id, user["id"])
        if not search:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found",
            )
        return search
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch search: {str(e)}",
        )


@router.patch("/searches/{search_id}")
async def update_search(
    search_id: int, data: SavedSearchUpdate, user: dict = Depends(get_current_user)
):
    """Update a saved search."""
    try:
        update_saved_search(
            search_id=search_id,
            user_id=user["id"],
            query=data.query,
            name=data.name,
            frequency=data.frequency,
            is_active=data.is_active,
        )
        updated = get_saved_search(search_id, user["id"])
        return {
            "id": updated["id"],
            "message": "Search updated successfully",
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search not found",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update search: {str(e)}",
        )


@router.delete("/searches/{search_id}")
async def delete_search(search_id: int, user: dict = Depends(get_current_user)):
    """Delete a saved search."""
    try:
        delete_saved_search(search_id, user["id"])
        return {"message": "Search deleted successfully"}
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search not found",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete search: {str(e)}",
        )


@router.get("/searches/{search_id}/history", response_model=list[SearchRunResponse])
async def get_search_history_endpoint(search_id: int, user: dict = Depends(get_current_user)):
    """Get execution history for a saved search."""
    try:
        # Verify ownership
        search = get_saved_search(search_id, user["id"])
        if not search:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search not found",
            )

        history = get_search_history(search_id)
        return history
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch history: {str(e)}",
        )
