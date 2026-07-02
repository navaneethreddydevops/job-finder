"""Slack and Discord integrations."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import datetime
import aiohttp
from backend.db import get_db_connection, AUTO_PK
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["integrations"])


class IntegrationCreate(BaseModel):
    type: str  # 'slack' or 'discord'
    webhook_url: str
    channel_name: str
    is_active: bool = True
    filter_min_score: int | None = None


def init_integrations_db():
    """Initialize integrations tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS integrations (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            webhook_url TEXT NOT NULL,
            channel_name TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            filter_min_score INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, type)
        )
    """)

    conn.commit()
    conn.close()


init_integrations_db()


@router.post("/integrations")
async def create_integration(
    data: IntegrationCreate, user: dict = Depends(get_current_user)
):
    """Create a new integration (Slack/Discord)."""
    try:
        if data.type not in ["slack", "discord"]:
            raise HTTPException(status_code=400, detail="Invalid integration type")

        now = datetime.datetime.utcnow().isoformat()

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO integrations
            (user_id, type, webhook_url, channel_name, is_active, filter_min_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, type) DO UPDATE SET
                webhook_url = EXCLUDED.webhook_url,
                channel_name = EXCLUDED.channel_name,
                is_active = EXCLUDED.is_active,
                filter_min_score = EXCLUDED.filter_min_score""",
            (
                user["id"],
                data.type,
                data.webhook_url,
                data.channel_name,
                data.is_active,
                data.filter_min_score,
                now,
            ),
        )
        conn.commit()
        # lastrowid is unreliable after an upsert-update; fetch the id by unique key.
        cursor.execute(
            "SELECT id FROM integrations WHERE user_id = ? AND type = ?",
            (user["id"], data.type),
        )
        integration_id = cursor.fetchone()["id"]
        conn.close()

        return {
            "id": integration_id,
            "type": data.type,
            "channel_name": data.channel_name,
            "is_active": data.is_active,
            "created_at": now,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create integration: {str(e)}")


@router.get("/integrations")
async def list_integrations(user: dict = Depends(get_current_user)):
    """List all integrations for user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, type, channel_name, is_active, filter_min_score, created_at
            FROM integrations WHERE user_id = ? ORDER BY created_at DESC""",
            (user["id"],),
        )

        integrations = [
            {
                "id": row[0],
                "type": row[1],
                "channel_name": row[2],
                "is_active": row[3],
                "filter_min_score": row[4],
                "created_at": row[5],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"integrations": integrations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list integrations: {str(e)}")


@router.patch("/integrations/{integration_id}")
async def update_integration(
    integration_id: int, data: dict, user: dict = Depends(get_current_user)
):
    """Update an integration."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """UPDATE integrations
            SET webhook_url = ?, channel_name = ?, is_active = ?, filter_min_score = ?
            WHERE id = ? AND user_id = ?""",
            (
                data.get("webhook_url"),
                data.get("channel_name"),
                data.get("is_active", True),
                data.get("filter_min_score"),
                integration_id,
                user["id"],
            ),
        )
        conn.commit()
        conn.close()

        return {"message": "Integration updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update integration: {str(e)}")


@router.delete("/integrations/{integration_id}")
async def delete_integration(
    integration_id: int, user: dict = Depends(get_current_user)
):
    """Delete an integration."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM integrations WHERE id = ? AND user_id = ?",
            (integration_id, user["id"]),
        )
        conn.commit()
        conn.close()

        return {"message": "Integration deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete integration: {str(e)}")


def format_job_as_slack_block(job: dict) -> dict:
    """Format a job as Slack Block Kit."""
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{job.get('title')}*\n{job.get('company')} • {job.get('location')}\n"
                    f"<{job.get('url')}|View Job>",
        },
    }


def format_job_as_discord_embed(job: dict) -> dict:
    """Format a job as Discord embed."""
    return {
        "title": job.get("title"),
        "description": f"{job.get('company')} • {job.get('location')}",
        "url": job.get("url"),
        "color": 3447003,
        "fields": [
            {"name": "Company", "value": job.get("company"), "inline": True},
            {"name": "Location", "value": job.get("location"), "inline": True},
            {"name": "Posted", "value": job.get("posted_at", "N/A"), "inline": False},
        ],
    }


async def send_to_slack(webhook_url: str, job: dict):
    """Send a job notification to Slack."""
    try:
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🎯 New Job Match",
                    },
                },
                format_job_as_slack_block(job),
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View Job"},
                            "url": job.get("url"),
                            "style": "primary",
                        }
                    ],
                },
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                return response.status == 200
    except Exception as e:
        print(f"Failed to send Slack notification: {e}")
        return False


async def send_to_discord(webhook_url: str, job: dict):
    """Send a job notification to Discord."""
    try:
        payload = {
            "embeds": [format_job_as_discord_embed(job)],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                return response.status == 204
    except Exception as e:
        print(f"Failed to send Discord notification: {e}")
        return False


@router.post("/integrations/test")
async def test_integration(
    integration_id: int, user: dict = Depends(get_current_user)
):
    """Test an integration with a sample job."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT type, webhook_url FROM integrations WHERE id = ? AND user_id = ?",
            (integration_id, user["id"]),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Integration not found")

        integration_type, webhook_url = row

        # Sample job data
        sample_job = {
            "title": "Senior Software Engineer",
            "company": "Tech Company Inc",
            "location": "San Francisco, CA",
            "url": "https://example.com/job/123",
            "posted_at": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        }

        if integration_type == "slack":
            success = await send_to_slack(webhook_url, sample_job)
        elif integration_type == "discord":
            success = await send_to_discord(webhook_url, sample_job)
        else:
            raise HTTPException(status_code=400, detail="Invalid integration type")

        if success:
            return {"message": "Test message sent successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send test message")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to test integration: {str(e)}")
