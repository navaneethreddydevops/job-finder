"""Webhooks and JSON feed API."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, HttpUrl
import datetime
import secrets
from backend.db import get_db_connection
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: list[str]  # 'job.new', 'job.applied', etc.
    is_active: bool = True


class APIKeyCreate(BaseModel):
    name: str
    rate_limit: int = 1000


def init_webhooks_db():
    """Initialize webhook tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            rate_limit INTEGER DEFAULT 1000,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            UNIQUE(user_id, name)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            url TEXT NOT NULL,
            events TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status_code INTEGER,
            response TEXT,
            delivered_at TEXT,
            retry_count INTEGER DEFAULT 0,
            FOREIGN KEY(webhook_id) REFERENCES webhooks(id)
        )
    """)

    conn.commit()
    conn.close()


init_webhooks_db()


def generate_api_key() -> str:
    """Generate a unique API key."""
    return f"jf_{secrets.token_urlsafe(32)}"


@router.post("/api-keys")
async def create_api_key(data: APIKeyCreate, user: dict = Depends(get_current_user)):
    """Create a new API key for user."""
    try:
        key = generate_api_key()
        now = datetime.datetime.utcnow().isoformat()

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO api_keys (user_id, key, name, rate_limit, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (user["id"], key, data.name, data.rate_limit, now),
        )
        conn.commit()
        conn.close()

        return {
            "id": cursor.lastrowid,
            "key": key,
            "name": data.name,
            "rate_limit": data.rate_limit,
            "created_at": now,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {str(e)}")


@router.get("/api-keys")
async def list_api_keys(user: dict = Depends(get_current_user)):
    """List all API keys for user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, key, name, rate_limit, created_at, last_used_at
            FROM api_keys WHERE user_id = ? ORDER BY created_at DESC""",
            (user["id"],),
        )

        keys = [
            {
                "id": row[0],
                "key": row[1][:10] + "..." if row[1] else None,
                "name": row[2],
                "rate_limit": row[3],
                "created_at": row[4],
                "last_used_at": row[5],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"keys": keys}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list API keys: {str(e)}")


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: int, user: dict = Depends(get_current_user)):
    """Delete an API key."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
            (key_id, user["id"]),
        )
        conn.commit()
        conn.close()

        return {"message": "API key deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")


@router.post("/webhooks")
async def create_webhook(data: WebhookCreate, user: dict = Depends(get_current_user)):
    """Create a new webhook."""
    try:
        now = datetime.datetime.utcnow().isoformat()
        events_str = ",".join(data.events)

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO webhooks (user_id, url, events, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (user["id"], str(data.url), events_str, data.is_active, now),
        )
        conn.commit()
        webhook_id = cursor.lastrowid
        conn.close()

        return {
            "id": webhook_id,
            "url": str(data.url),
            "events": data.events,
            "is_active": data.is_active,
            "created_at": now,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create webhook: {str(e)}")


@router.get("/webhooks")
async def list_webhooks(user: dict = Depends(get_current_user)):
    """List all webhooks for user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, url, events, is_active, created_at
            FROM webhooks WHERE user_id = ? ORDER BY created_at DESC""",
            (user["id"],),
        )

        webhooks = [
            {
                "id": row[0],
                "url": row[1],
                "events": row[2].split(","),
                "is_active": row[3],
                "created_at": row[4],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"webhooks": webhooks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list webhooks: {str(e)}")


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int, data: dict, user: dict = Depends(get_current_user)
):
    """Update a webhook."""
    try:
        events_str = ",".join(data.get("events", [])) if data.get("events") else None

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """UPDATE webhooks
            SET url = ?, events = ?, is_active = ?
            WHERE id = ? AND user_id = ?""",
            (
                data.get("url"),
                events_str,
                data.get("is_active", True),
                webhook_id,
                user["id"],
            ),
        )
        conn.commit()
        conn.close()

        return {"message": "Webhook updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update webhook: {str(e)}")


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, user: dict = Depends(get_current_user)):
    """Delete a webhook."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user["id"]),
        )
        conn.commit()
        conn.close()

        return {"message": "Webhook deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete webhook: {str(e)}")


@router.get("/v1/jobs/feed")
async def get_jobs_feed(
    query: str | None = None,
    source: str | None = None,
    api_key: str | None = None,
    format: str = "json",
):
    """Public JSON feed of jobs (requires API key or user auth)."""
    try:
        if not api_key:
            raise HTTPException(status_code=401, detail="API key required")

        conn = get_db_connection()
        cursor = conn.cursor()

        # Validate API key
        cursor.execute(
            "SELECT user_id, rate_limit FROM api_keys WHERE key = ?",
            (api_key,),
        )
        key_row = cursor.fetchone()

        if not key_row:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id, rate_limit = key_row

        # Update last_used_at
        cursor.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key = ?",
            (datetime.datetime.utcnow().isoformat(), api_key),
        )

        # Build query
        query_str = """SELECT id, title, company, location, url, salary_min, salary_max,
                       source, posted_at, posted_within_24h
                       FROM jobs WHERE user_id = ? AND posted_within_24h = 1"""
        params = [user_id]

        if query:
            query_str += " AND title LIKE ?"
            params.append(f"%{query}%")

        if source:
            query_str += " AND source = ?"
            params.append(source)

        query_str += " ORDER BY created_at DESC LIMIT 100"

        cursor.execute(query_str, params)
        jobs = cursor.fetchall()
        conn.commit()
        conn.close()

        jobs_list = [
            {
                "id": row[0],
                "title": row[1],
                "company": row[2],
                "location": row[3],
                "url": row[4],
                "salary_min": row[5],
                "salary_max": row[6],
                "source": row[7],
                "posted_at": row[8],
                "posted_within_24h": row[9],
            }
            for row in jobs
        ]

        if format == "xml":
            # Simple XML format
            xml = '<?xml version="1.0" encoding="UTF-8"?>\n<jobs>\n'
            for job in jobs_list:
                xml += f"""  <job>
    <id>{job['id']}</id>
    <title>{job['title']}</title>
    <company>{job['company']}</company>
    <location>{job['location']}</location>
    <url>{job['url']}</url>
    <source>{job['source']}</source>
  </job>\n"""
            xml += "</jobs>"
            return {"format": "xml", "data": xml}

        return {"format": "json", "jobs": jobs_list, "count": len(jobs_list)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch feed: {str(e)}")


@router.get("/webhooks/{webhook_id}/deliveries")
async def get_webhook_deliveries(
    webhook_id: int, user: dict = Depends(get_current_user)
):
    """Get delivery logs for a webhook."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Verify webhook ownership
        cursor.execute(
            "SELECT id FROM webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user["id"]),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Webhook not found")

        cursor.execute(
            """SELECT id, delivered_at, status_code, retry_count
            FROM webhook_deliveries
            WHERE webhook_id = ?
            ORDER BY delivered_at DESC
            LIMIT 50""",
            (webhook_id,),
        )

        deliveries = [
            {
                "id": row[0],
                "delivered_at": row[1],
                "status_code": row[2],
                "retry_count": row[3],
            }
            for row in cursor.fetchall()
        ]

        conn.close()
        return {"deliveries": deliveries}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch webhook deliveries: {str(e)}"
        )
