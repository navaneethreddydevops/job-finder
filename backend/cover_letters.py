"""Cover letter generation and management."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
import datetime
from backend.db import get_db_connection, get_job_for_user, AUTO_PK, init_db
from backend.auth import get_current_user, init_auth_db

router = APIRouter(prefix="/api", tags=["cover-letters"])


class CoverLetterCreate(BaseModel):
    job_id: int
    job_description: str
    template_id: int | None = None


class CoverLetterResponse(BaseModel):
    id: int
    user_id: int
    job_id: int
    content: str
    generated_at: str
    last_edited_at: str | None


def init_cover_letter_db():
    """Initialize cover letter tables."""
    # This module self-initializes at import time, which can run before main.py's
    # init calls. Postgres (unlike SQLite) requires FK target tables to exist at
    # CREATE TABLE, so ensure jobs/users are created first — both are idempotent.
    init_db()
    init_auth_db()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS cover_letters (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            content TEXT,
            generated_at TEXT,
            last_edited_at TEXT,
            UNIQUE(user_id, job_id),
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS cover_templates (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            name TEXT,
            content_template TEXT,
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.commit()
    conn.close()


init_cover_letter_db()


@router.post("/cover-letters")
async def generate_cover_letter(data: CoverLetterCreate, user: dict = Depends(get_current_user)):
    """Generate a cover letter for a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(data.job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        now = datetime.datetime.utcnow().isoformat()

        # Placeholder content - in production, this would call Claude
        content = """Dear Hiring Manager,

I am writing to express my strong interest in this position. Based on the job description provided, I am confident that my skills and experience make me an excellent fit for this role.

Your key requirements:
- The position emphasizes the skills and responsibilities outlined in the job description
- I bring relevant expertise that directly addresses these needs

I would welcome the opportunity to discuss how I can contribute to your team.

Best regards"""

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO cover_letters (user_id, job_id, content, generated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET
                content = EXCLUDED.content,
                generated_at = EXCLUDED.generated_at
            """,
            (user["id"], data.job_id, content, now),
        )
        conn.commit()

        # lastrowid is unreliable after an upsert-update; fetch the id by unique key.
        cursor.execute(
            "SELECT id FROM cover_letters WHERE user_id = ? AND job_id = ?",
            (user["id"], data.job_id),
        )
        cover_letter_id = cursor.fetchone()["id"]
        conn.close()

        return {
            "id": cover_letter_id,
            "job_id": data.job_id,
            "content": content,
            "message": "Cover letter generated successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate cover letter: {str(e)}")


@router.get("/cover-letters/{job_id}")
async def get_cover_letter(job_id: int, user: dict = Depends(get_current_user)):
    """Get cover letter for a job."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM cover_letters WHERE job_id = ? AND user_id = ?",
            (job_id, user["id"]),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        raise HTTPException(status_code=404, detail="Cover letter not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch cover letter: {str(e)}")


@router.put("/cover-letters/{job_id}")
async def update_cover_letter(job_id: int, data: dict, user: dict = Depends(get_current_user)):
    """Update cover letter content."""
    try:
        # Verify user owns this job
        job = get_job_for_user(job_id, user["id"])
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        now = datetime.datetime.utcnow().isoformat()
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE cover_letters
            SET content = ?, last_edited_at = ?
            WHERE job_id = ? AND user_id = ?
            """,
            (data.get("content"), now, job_id, user["id"]),
        )
        conn.commit()
        conn.close()

        return {"message": "Cover letter updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update cover letter: {str(e)}")
