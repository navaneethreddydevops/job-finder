"""Rate limiting and throttling middleware."""

from fastapi import HTTPException, status
from datetime import datetime, timedelta
from backend.db import get_db_connection, AUTO_PK
import hashlib

# In-memory rate limit tracking (simple dict-based)
_rate_limits = {}


def init_rate_limit_db():
    """Initialize rate limit tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS rate_limits (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            request_count INTEGER DEFAULT 1,
            reset_at TEXT NOT NULL,
            UNIQUE(user_id, endpoint)
        )
    """)

    conn.commit()
    conn.close()


init_rate_limit_db()


class RateLimitConfig:
    """Rate limit configuration."""
    # Max requests per endpoint per time window
    PULL_AGENT = 1  # Max 1 agent run per 30 minutes
    PULL_AGENT_WINDOW = 1800  # 30 minutes

    APPLY_AGENT = 10  # Max 10 autonomous apply runs per hour
    APPLY_AGENT_WINDOW = 3600  # 1 hour

    DEFAULT_LIMIT = 100  # 100 requests per hour
    DEFAULT_WINDOW = 3600  # 1 hour

    STRICT_LIMIT = 10  # 10 requests per minute for strict endpoints
    STRICT_WINDOW = 60  # 1 minute


def get_rate_limit_key(user_id: str, endpoint: str) -> str:
    """Generate rate limit key."""
    return hashlib.md5(f"{user_id}:{endpoint}".encode()).hexdigest()


def check_rate_limit(
    user_id: str,
    endpoint: str,
    limit: int = RateLimitConfig.DEFAULT_LIMIT,
    window: int = RateLimitConfig.DEFAULT_WINDOW,
) -> dict:
    """
    Check if user has exceeded rate limit.
    Returns: { allowed: bool, remaining: int, reset_at: str }
    """
    now = datetime.utcnow()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """SELECT request_count, reset_at FROM rate_limits
        WHERE user_id = ? AND endpoint = ?""",
        (user_id, endpoint),
    )
    row = cursor.fetchone()

    reset_at = None
    request_count = 0

    if row:
        request_count, reset_at_str = row
        reset_at = datetime.fromisoformat(reset_at_str)

        # If window has expired, reset counter
        if now > reset_at:
            request_count = 0
            reset_at = now + timedelta(seconds=window)
        else:
            request_count += 1

        cursor.execute(
            """UPDATE rate_limits
            SET request_count = ?, reset_at = ?
            WHERE user_id = ? AND endpoint = ?""",
            (request_count, reset_at.isoformat(), user_id, endpoint),
        )
    else:
        request_count = 1
        reset_at = now + timedelta(seconds=window)

        cursor.execute(
            """INSERT INTO rate_limits
            (user_id, endpoint, request_count, reset_at)
            VALUES (?, ?, ?, ?)""",
            (user_id, endpoint, request_count, reset_at.isoformat()),
        )

    conn.commit()
    conn.close()

    allowed = request_count <= limit
    remaining = max(0, limit - request_count)
    reset_timestamp = reset_at.isoformat()

    return {
        "allowed": allowed,
        "remaining": remaining,
        "limit": limit,
        "reset_at": reset_timestamp,
        "retry_after": int((reset_at - now).total_seconds()) if not allowed else 0,
    }


def enforce_rate_limit(
    user_id: str,
    endpoint: str,
    limit: int = RateLimitConfig.DEFAULT_LIMIT,
    window: int = RateLimitConfig.DEFAULT_WINDOW,
):
    """Enforce rate limit, raise exception if exceeded."""
    result = check_rate_limit(user_id, endpoint, limit, window)

    if not result["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {result['retry_after']} seconds.",
            headers={"Retry-After": str(result["retry_after"])},
        )

    return result


def reset_user_rate_limits(user_id: str):
    """Reset all rate limits for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM rate_limits WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def reset_endpoint_rate_limits(endpoint: str):
    """Reset all rate limits for an endpoint."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM rate_limits WHERE endpoint = ?", (endpoint,))
    conn.commit()
    conn.close()


def get_rate_limit_status(user_id: str, endpoint: str) -> dict:
    """Get current rate limit status for a user/endpoint."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """SELECT request_count, reset_at FROM rate_limits
        WHERE user_id = ? AND endpoint = ?""",
        (user_id, endpoint),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {
            "endpoint": endpoint,
            "request_count": 0,
            "limit": RateLimitConfig.DEFAULT_LIMIT,
            "remaining": RateLimitConfig.DEFAULT_LIMIT,
            "reset_at": None,
        }

    request_count, reset_at_str = row
    reset_at = datetime.fromisoformat(reset_at_str)

    return {
        "endpoint": endpoint,
        "request_count": request_count,
        "limit": RateLimitConfig.DEFAULT_LIMIT,
        "remaining": max(0, RateLimitConfig.DEFAULT_LIMIT - request_count),
        "reset_at": reset_at_str,
        "resets_in_seconds": int((reset_at - datetime.utcnow()).total_seconds()),
    }
