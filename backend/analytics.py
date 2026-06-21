"""Analytics and job market insights."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
import datetime
from collections import Counter, defaultdict
from backend.db import get_db_connection
from backend.auth import get_current_user

router = APIRouter(prefix="/api", tags=["analytics"])


class PersonalStats(BaseModel):
    applications_count: int
    interviews_count: int
    offers_count: int
    rejected_count: int
    avg_score: float | None


class TrendData(BaseModel):
    date: str
    value: int


class MarketInsights(BaseModel):
    top_skills: list[dict]
    top_locations: list[dict]
    avg_salary_by_role: dict
    trending_skills: list[str]


def init_analytics_db():
    """Initialize analytics tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            applications_count INTEGER DEFAULT 0,
            interviews_count INTEGER DEFAULT 0,
            offers_count INTEGER DEFAULT 0,
            rejected_count INTEGER DEFAULT 0,
            avg_match_score REAL,
            UNIQUE(user_id, snapshot_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            trending_skills TEXT,
            top_locations TEXT,
            avg_salary_by_role TEXT,
            total_jobs_posted INTEGER,
            UNIQUE(snapshot_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS board_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            total_jobs INTEGER DEFAULT 0,
            applications_count INTEGER DEFAULT 0,
            offers_count INTEGER DEFAULT 0,
            avg_response_time_hours REAL,
            quality_score REAL,
            UNIQUE(user_id, source)
        )
    """)

    conn.commit()
    conn.close()


init_analytics_db()


def calculate_personal_stats(user_id: str) -> PersonalStats:
    """Calculate personal statistics for user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) FROM applications WHERE user_id = ?
    """, (user_id,))
    applications_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM applications WHERE user_id = ? AND status = 'interviewing'
    """, (user_id,))
    interviews_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM applications WHERE user_id = ? AND status = 'offer'
    """, (user_id,))
    offers_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM applications WHERE user_id = ? AND status = 'rejected'
    """, (user_id,))
    rejected_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT AVG(CAST(match_score AS REAL)) FROM job_match_scores
        WHERE user_id = ?
    """, (user_id,))
    avg_score_row = cursor.fetchone()
    avg_score = avg_score_row[0] if avg_score_row and avg_score_row[0] else None

    conn.close()

    return PersonalStats(
        applications_count=applications_count,
        interviews_count=interviews_count,
        offers_count=offers_count,
        rejected_count=rejected_count,
        avg_score=avg_score,
    )


def get_market_insights() -> MarketInsights:
    """Get global market insights across all users."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Top skills
    cursor.execute("""
        SELECT js.skill, COUNT(*) as count FROM job_skills js
        GROUP BY js.skill ORDER BY count DESC LIMIT 10
    """)
    top_skills = [{"skill": row[0], "count": row[1]} for row in cursor.fetchall()]

    # Top locations
    cursor.execute("""
        SELECT location, COUNT(*) as count FROM jobs
        WHERE location IS NOT NULL AND location != ''
        GROUP BY location ORDER BY count DESC LIMIT 10
    """)
    top_locations = [{"location": row[0], "count": row[1]} for row in cursor.fetchall()]

    # Average salary by role (extract role from title)
    cursor.execute("""
        SELECT
            CASE
                WHEN title LIKE '%Senior%' THEN 'Senior'
                WHEN title LIKE '%Junior%' THEN 'Junior'
                WHEN title LIKE '%Lead%' THEN 'Lead'
                ELSE 'Mid-Level'
            END as role,
            AVG(CAST(salary_min AS REAL)) as avg_min,
            AVG(CAST(salary_max AS REAL)) as avg_max
        FROM jobs
        WHERE salary_min IS NOT NULL AND salary_max IS NOT NULL
        GROUP BY role
    """)
    salary_by_role = {}
    for row in cursor.fetchall():
        role, avg_min, avg_max = row
        salary_by_role[role] = {
            "avg_min": round(avg_min, 2) if avg_min else 0,
            "avg_max": round(avg_max, 2) if avg_max else 0,
        }

    # Trending skills (recently posted jobs)
    trending_skills = []
    cursor.execute("""
        SELECT js.skill, COUNT(*) as count FROM job_skills js
        JOIN jobs j ON js.job_id = j.id
        WHERE j.created_at > datetime('now', '-7 days')
        GROUP BY js.skill ORDER BY count DESC LIMIT 10
    """)
    trending_skills = [row[0] for row in cursor.fetchall()]

    conn.close()

    return MarketInsights(
        top_skills=top_skills,
        top_locations=top_locations,
        avg_salary_by_role=salary_by_role,
        trending_skills=trending_skills,
    )


@router.get("/analytics/personal")
async def get_personal_analytics(user: dict = Depends(get_current_user)):
    """Get personal analytics for current user."""
    try:
        stats = calculate_personal_stats(user["id"])

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get application trends (last 30 days)
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM applications
            WHERE user_id = ? AND created_at > datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date
        """, (user["id"],))
        application_trends = [
            {"date": row[0], "count": row[1]} for row in cursor.fetchall()
        ]

        # Get interview trends
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM applications
            WHERE user_id = ? AND status = 'interviewing'
            AND created_at > datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date
        """, (user["id"],))
        interview_trends = [
            {"date": row[0], "count": row[1]} for row in cursor.fetchall()
        ]

        # Get offer trends
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM applications
            WHERE user_id = ? AND status = 'offer'
            AND created_at > datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date
        """, (user["id"],))
        offer_trends = [
            {"date": row[0], "count": row[1]} for row in cursor.fetchall()
        ]

        conn.close()

        return {
            "stats": stats,
            "application_trends": application_trends,
            "interview_trends": interview_trends,
            "offer_trends": offer_trends,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@router.get("/analytics/market")
async def get_market_analytics():
    """Get market insights (global, no auth required)."""
    try:
        insights = get_market_insights()
        return insights
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch market insights: {str(e)}")


@router.get("/analytics/board-performance")
async def get_board_performance(user: dict = Depends(get_current_user)):
    """Get job board performance metrics for user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get applications by source
        cursor.execute("""
            SELECT j.source, COUNT(a.id) as applications,
                   SUM(CASE WHEN a.status = 'offer' THEN 1 ELSE 0 END) as offers
            FROM jobs j
            LEFT JOIN applications a ON j.id = a.job_id AND a.user_id = ?
            WHERE j.user_id = ?
            GROUP BY j.source
            ORDER BY applications DESC
        """, (user["id"], user["id"]))

        board_metrics = []
        for row in cursor.fetchall():
            source, applications, offers = row
            board_metrics.append({
                "source": source,
                "applications": applications or 0,
                "offers": offers or 0,
                "conversion_rate": (offers / applications * 100) if applications > 0 else 0,
            })

        conn.close()

        return {"board_metrics": board_metrics}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch board metrics: {str(e)}")


@router.get("/analytics/skills-demand")
async def get_skills_demand():
    """Get skills demand radar data."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT js.skill, COUNT(*) as count FROM job_skills js
            GROUP BY js.skill ORDER BY count DESC LIMIT 20
        """)

        skills_data = [{"skill": row[0], "demand": row[1]} for row in cursor.fetchall()]
        conn.close()

        return {"skills": skills_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch skills demand: {str(e)}")


@router.get("/analytics/salary-trends")
async def get_salary_trends():
    """Get salary trends by location and role."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Salary by location
        cursor.execute("""
            SELECT location,
                   AVG(CAST(salary_min AS REAL)) as avg_min,
                   AVG(CAST(salary_max AS REAL)) as avg_max,
                   COUNT(*) as count
            FROM jobs
            WHERE salary_min IS NOT NULL AND salary_max IS NOT NULL
            AND location IS NOT NULL AND location != ''
            GROUP BY location
            ORDER BY count DESC
            LIMIT 15
        """)

        salary_by_location = [
            {
                "location": row[0],
                "avg_min": round(row[1], 2) if row[1] else 0,
                "avg_max": round(row[2], 2) if row[2] else 0,
                "count": row[3],
            }
            for row in cursor.fetchall()
        ]

        conn.close()

        return {"salary_trends": salary_by_location}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch salary trends: {str(e)}")
