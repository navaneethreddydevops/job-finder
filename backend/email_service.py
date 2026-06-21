"""Email integration and digest service."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr
import datetime
import secrets
from backend.db import get_db_connection
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["email"])


class EmailPreferences(BaseModel):
    digest_enabled: bool
    digest_frequency: str  # 'daily', 'weekly'
    digest_sources: list[str] | None
    receive_new_jobs: bool
    unsubscribe_token: str | None


class EmailTemplate(BaseModel):
    subject: str
    body: str
    html_body: str | None


def init_email_db():
    """Initialize email tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            digest_enabled BOOLEAN DEFAULT TRUE,
            digest_frequency TEXT DEFAULT 'daily',
            digest_sources TEXT,
            receive_new_jobs BOOLEAN DEFAULT TRUE,
            unsubscribe_token TEXT UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            job_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'sent'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            html_body TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_email_db()


def create_unsubscribe_token() -> str:
    """Generate a unique unsubscribe token."""
    return secrets.token_urlsafe(32)


@router.get("/email-preferences")
async def get_email_preferences(user: dict = Depends(get_current_user)):
    """Get email preferences for current user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT digest_enabled, digest_frequency, digest_sources,
                     receive_new_jobs, unsubscribe_token
            FROM email_preferences WHERE user_id = ?""",
            (user["id"],),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "digest_enabled": row[0],
                "digest_frequency": row[1],
                "digest_sources": row[2].split(",") if row[2] else [],
                "receive_new_jobs": row[3],
                "unsubscribe_token": row[4],
            }

        # Return defaults if not found
        return {
            "digest_enabled": True,
            "digest_frequency": "daily",
            "digest_sources": [],
            "receive_new_jobs": True,
            "unsubscribe_token": None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch email preferences: {str(e)}")


@router.patch("/email-preferences")
async def update_email_preferences(
    prefs: dict, user: dict = Depends(get_current_user)
):
    """Update email preferences for current user."""
    try:
        now = datetime.datetime.utcnow().isoformat()
        unsubscribe_token = create_unsubscribe_token()

        conn = get_db_connection()
        cursor = conn.cursor()

        digest_sources = ",".join(prefs.get("digest_sources", []))
        cursor.execute(
            """
            INSERT OR REPLACE INTO email_preferences
            (user_id, digest_enabled, digest_frequency, digest_sources,
             receive_new_jobs, unsubscribe_token, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                prefs.get("digest_enabled", True),
                prefs.get("digest_frequency", "daily"),
                digest_sources,
                prefs.get("receive_new_jobs", True),
                unsubscribe_token,
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        return {
            "message": "Email preferences updated successfully",
            "unsubscribe_token": unsubscribe_token,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update email preferences: {str(e)}")


@router.post("/email-digest/send")
async def send_digest(user: dict = Depends(get_current_user)):
    """
    Manually trigger digest send for current user.
    In production, this would be called by a scheduler (APScheduler).
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get user's email preferences
        cursor.execute(
            "SELECT digest_frequency, digest_sources FROM email_preferences WHERE user_id = ?",
            (user["id"],),
        )
        prefs_row = cursor.fetchone()

        if not prefs_row:
            raise HTTPException(status_code=404, detail="Email preferences not configured")

        digest_sources = prefs_row[1].split(",") if prefs_row[1] else []

        # Get recent jobs matching user's saved searches
        cursor.execute(
            """
            SELECT j.id, j.title, j.company, j.source, j.url, j.posted_at
            FROM jobs j
            LEFT JOIN saved_searches ss ON j.title LIKE '%' || ss.query || '%'
            WHERE j.user_id = ? AND j.posted_within_24h = 1
            AND j.created_at > datetime('now', '-24 hours')
            """,
            (user["id"],),
        )
        jobs = cursor.fetchall()

        # Record digest send
        now = datetime.datetime.utcnow().isoformat()
        cursor.execute(
            """INSERT INTO email_digests (user_id, sent_at, job_count, status)
            VALUES (?, ?, ?, ?)""",
            (user["id"], now, len(jobs), "sent"),
        )
        conn.commit()
        conn.close()

        return {
            "message": "Digest sent successfully",
            "job_count": len(jobs),
            "sent_at": now,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send digest: {str(e)}")


@router.post("/email/unsubscribe")
async def unsubscribe_from_email(token: str):
    """Unsubscribe from email digests using token."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT user_id FROM email_preferences WHERE unsubscribe_token = ?",
            (token,),
        )
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Invalid unsubscribe token")

        user_id = row[0]

        # Disable digest
        cursor.execute(
            "UPDATE email_preferences SET digest_enabled = 0 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        conn.close()

        return {"message": "Successfully unsubscribed from email digests"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to unsubscribe: {str(e)}")


@router.get("/email/digest-history")
async def get_digest_history(user: dict = Depends(get_current_user)):
    """Get digest send history for current user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, sent_at, job_count, status
            FROM email_digests
            WHERE user_id = ?
            ORDER BY sent_at DESC
            LIMIT 30""",
            (user["id"],),
        )

        history = [
            {
                "id": row[0],
                "sent_at": row[1],
                "job_count": row[2],
                "status": row[3],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch digest history: {str(e)}")


def build_digest_html(jobs: list, user_email: str) -> str:
    """Build HTML email digest template."""
    jobs_html = ""
    for job in jobs:
        jobs_html += f"""
        <div style="margin: 20px 0; padding: 15px; border: 1px solid #e0e0e0; border-radius: 8px;">
          <h3 style="margin: 0 0 10px 0; color: #1a1a1a;">{job[1]}</h3>
          <p style="margin: 5px 0; color: #666;">
            <strong>Company:</strong> {job[2]} | <strong>Source:</strong> {job[3]}
          </p>
          <p style="margin: 5px 0; color: #666;">
            <strong>Posted:</strong> {job[5]}
          </p>
          <p style="margin: 10px 0;">
            <a href="{job[4]}" target="_blank"
               style="background: #3b82f6; color: white; padding: 8px 16px; border-radius: 4px;
                      text-decoration: none; display: inline-block;">View Job</a>
          </p>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: #f9fafb; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            <h1 style="margin: 0; color: #1a1a1a;">Daily Job Digest</h1>
            <p style="margin: 10px 0 0 0; color: #666;">
                Your personalized job matches for today
            </p>
        </div>

        <div style="margin: 20px 0;">
            <p style="color: #666;">
                We found {len(jobs)} new job(s) matching your preferences:
            </p>
            {jobs_html}
        </div>

        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e0e0e0; color: #999; font-size: 12px;">
            <p>You're receiving this email because you subscribed to Job Finder digests.</p>
            <p>
                <a href="https://jobfinder.app/email/unsubscribe?token=unsubscribe_token"
                   style="color: #3b82f6; text-decoration: none;">Unsubscribe</a>
            </p>
        </div>
    </body>
    </html>
    """
