"""Interview scheduling and management."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
import datetime
from backend.db import get_db_connection, get_job_for_user
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["interviews"])


class InterviewCreate(BaseModel):
    application_id: int
    scheduled_at: str
    interview_type: str  # phone, video, onsite
    notes: str | None = None
    meeting_link: str | None = None


def init_interview_db():
    """Initialize interview tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            scheduled_at TEXT,
            interview_type TEXT,
            notes TEXT,
            meeting_link TEXT,
            reminder_sent INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(application_id) REFERENCES applications(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL,
            remind_at TEXT,
            type TEXT DEFAULT 'email',
            sent INTEGER DEFAULT 0,
            FOREIGN KEY(interview_id) REFERENCES interviews(id)
        )
        """
    )

    conn.commit()
    conn.close()


init_interview_db()


@router.post("/interviews")
async def schedule_interview(data: InterviewCreate, user: dict = Depends(get_current_user)):
    """Schedule an interview."""
    try:
        now = datetime.datetime.utcnow().isoformat()
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verify application ownership
        cursor.execute(
            """
            SELECT a.id FROM applications a
            WHERE a.id = ? AND a.user_id = ?
            """,
            (data.application_id, user["id"]),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Application not found")

        cursor.execute(
            """
            INSERT INTO interviews (application_id, scheduled_at, interview_type, notes, meeting_link, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (data.application_id, data.scheduled_at, data.interview_type, data.notes, data.meeting_link, now),
        )

        interview_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return {
            "id": interview_id,
            "application_id": data.application_id,
            "scheduled_at": data.scheduled_at,
            "message": "Interview scheduled successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to schedule interview: {str(e)}")


@router.get("/interviews")
async def list_interviews(user: dict = Depends(get_current_user)):
    """List all interviews for the user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.* FROM interviews i
            JOIN applications a ON i.application_id = a.id
            WHERE a.user_id = ?
            ORDER BY i.scheduled_at ASC
            """,
            (user["id"],),
        )
        rows = cursor.fetchall()
        conn.close()

        return {"interviews": [dict(row) for row in rows], "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch interviews: {str(e)}")


@router.get("/interviews/{interview_id}")
async def get_interview(interview_id: int, user: dict = Depends(get_current_user)):
    """Get interview details."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.* FROM interviews i
            JOIN applications a ON i.application_id = a.id
            WHERE i.id = ? AND a.user_id = ?
            """,
            (interview_id, user["id"]),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        raise HTTPException(status_code=404, detail="Interview not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch interview: {str(e)}")


@router.delete("/interviews/{interview_id}")
async def cancel_interview(interview_id: int, user: dict = Depends(get_current_user)):
    """Cancel an interview."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verify ownership
        cursor.execute(
            """
            SELECT i.id FROM interviews i
            JOIN applications a ON i.application_id = a.id
            WHERE i.id = ? AND a.user_id = ?
            """,
            (interview_id, user["id"]),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Interview not found")

        cursor.execute("DELETE FROM interviews WHERE id = ?", (interview_id,))
        conn.commit()
        conn.close()

        return {"message": "Interview canceled successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to cancel interview: {str(e)}")
