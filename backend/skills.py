"""Skills management and gap analysis endpoints."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import (
    add_user_skill,
    get_user_skills,
    get_job_skills,
    add_job_skills,
    get_skills_gap,
    delete_user_skill,
    get_job_for_user,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["skills"])


class UserSkillCreate(BaseModel):
    skill: str
    proficiency: str = "intermediate"  # beginner, intermediate, expert
    years_exp: float | None = None


class SkillGapResponse(BaseModel):
    matched_skills: list[str]
    missing_skills: list[str]
    match_percentage: float
    total_required: int


@router.post("/skills")
async def add_skill(data: UserSkillCreate, user: dict = Depends(get_current_user)):
    """Add or update a user skill."""
    try:
        add_user_skill(user["id"], data.skill, data.proficiency, data.years_exp)
        return {"skill": data.skill, "proficiency": data.proficiency, "message": "Skill added successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to add skill: {str(e)}")


@router.get("/skills")
async def list_skills(user: dict = Depends(get_current_user)):
    """Get all user skills."""
    try:
        skills = get_user_skills(user["id"])
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch skills: {str(e)}")


@router.delete("/skills/{skill}")
async def remove_skill(skill: str, user: dict = Depends(get_current_user)):
    """Delete a user skill."""
    try:
        delete_user_skill(user["id"], skill)
        return {"message": "Skill deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete skill: {str(e)}")


@router.get("/jobs/{job_id}/skills")
async def get_job_skills_endpoint(job_id: int, user: dict = Depends(get_current_user)):
    """Get extracted skills for a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        skills = get_job_skills(job_id)
        return {"skills": skills, "count": len(skills)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch job skills: {str(e)}")


@router.post("/jobs/{job_id}/skills")
async def add_job_skills_endpoint(job_id: int, data: dict, user: dict = Depends(get_current_user)):
    """Add extracted skills to a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        skills = data.get("skills", [])
        add_job_skills(job_id, skills)
        return {"job_id": job_id, "skills_added": len(skills), "message": "Skills added successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to add skills: {str(e)}")


@router.get("/jobs/{job_id}/skills-gap", response_model=SkillGapResponse)
async def analyze_skills_gap(job_id: int, user: dict = Depends(get_current_user)):
    """Analyze skill gap between user and job requirements."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        gap = get_skills_gap(user["id"], job_id)
        return SkillGapResponse(**gap)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze gap: {str(e)}")
