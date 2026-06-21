"""Job matching score calculation and user preferences."""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from backend.db import get_user_skills, get_job_skills, get_db_connection
from backend.auth import get_current_user
import datetime

router = APIRouter(prefix="/api", tags=["scoring"])


def init_scoring_db():
    """Create the job_match_scores table so reads (e.g. analytics) work before
    any score has been written."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_match_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            match_score INTEGER,
            skill_overlap INTEGER,
            level_fit INTEGER,
            location_fit INTEGER,
            salary_fit INTEGER,
            calculated_at TEXT,
            UNIQUE(job_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            preferred_locations TEXT,
            experience_level TEXT,
            industries TEXT,
            remote_only INTEGER,
            salary_min REAL,
            salary_max REAL,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


class UserPreferences(BaseModel):
    preferred_locations: list[str] | None = None
    experience_level: str | None = None  # entry, mid, senior
    industries: list[str] | None = None
    remote_only: bool = False
    salary_min: float | None = None
    salary_max: float | None = None


class MatchScoreResponse(BaseModel):
    job_id: int
    match_score: int  # 0-100
    skill_overlap: int
    level_fit: int
    location_fit: int
    salary_fit: int
    components: dict


def calculate_match_score(job: dict, user_skills: list, preferences: dict = None):
    """Calculate match score for a job (0-100)."""
    if not preferences:
        preferences = {}

    score_components = {}

    # Skill overlap (40% weight)
    job_skills_list = [s.lower() for s in [item.get("skill", "") for item in (job.get("job_skills") or [])]]
    user_skills_list = [s["skill"].lower() for s in user_skills]
    if job_skills_list:
        skill_match = len([s for s in job_skills_list if s in user_skills_list]) / len(job_skills_list)
        score_components["skill_overlap"] = int(skill_match * 100)
    else:
        score_components["skill_overlap"] = 50  # Unknown skills, neutral score

    # Location fit (20% weight)
    preferred_locs = preferences.get("preferred_locations", [])
    job_location = (job.get("location") or "").lower()
    if preferences.get("remote_only"):
        location_match = 100 if "remote" in job_location else 0
    elif preferred_locs:
        location_match = 100 if any(loc.lower() in job_location for loc in preferred_locs) else 50
    else:
        location_match = 75  # Default if no preference
    score_components["location_fit"] = location_match

    # Salary fit (20% weight)
    salary_min = preferences.get("salary_min", 0)
    salary_max = preferences.get("salary_max", 999999)
    job_salary_min = job.get("salary_min", 0)
    job_salary_max = job.get("salary_max", 999999)

    if salary_min and job_salary_max and job_salary_max >= salary_min:
        salary_match = 100
    elif salary_max and job_salary_min and job_salary_min <= salary_max:
        salary_match = 75
    else:
        salary_match = 25
    score_components["salary_fit"] = salary_match

    # Experience level fit (20% weight)
    exp_level = preferences.get("experience_level", "mid")
    job_title = (job.get("title") or "").lower()
    level_keywords = {
        "entry": ["junior", "entry", "graduate", "intern"],
        "mid": ["mid", "senior", "engineer", "specialist"],
        "senior": ["senior", "lead", "principal", "staff"],
    }

    keywords = level_keywords.get(exp_level, [])
    level_match = 100 if any(kw in job_title for kw in keywords) else 60
    score_components["level_fit"] = level_match

    # Calculate weighted average
    final_score = int(
        (score_components["skill_overlap"] * 0.40)
        + (score_components["level_fit"] * 0.20)
        + (score_components["location_fit"] * 0.20)
        + (score_components["salary_fit"] * 0.20)
    )

    return final_score, score_components


def save_match_score(job_id: int, user_id: int, score: int, components: dict):
    """Save match score to database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    # Create match_scores table if not exists
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS job_match_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            match_score INTEGER,
            skill_overlap INTEGER,
            level_fit INTEGER,
            location_fit INTEGER,
            salary_fit INTEGER,
            calculated_at TEXT,
            UNIQUE(job_id, user_id)
        )
        """
    )

    cursor.execute(
        """
        INSERT OR REPLACE INTO job_match_scores
        (job_id, user_id, match_score, skill_overlap, level_fit, location_fit, salary_fit, calculated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            user_id,
            score,
            components.get("skill_overlap", 0),
            components.get("level_fit", 0),
            components.get("location_fit", 0),
            components.get("salary_fit", 0),
            now,
        ),
    )
    conn.commit()
    conn.close()


def get_match_score(job_id: int, user_id: int):
    """Get cached match score for a job."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM job_match_scores
        WHERE job_id = ? AND user_id = ?
        """,
        (job_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


@router.post("/preferences")
async def save_preferences(data: UserPreferences, user: dict = Depends(get_current_user)):
    """Save user preferences for job matching."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create preferences table if not exists
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                preferred_locations TEXT,
                experience_level TEXT,
                industries TEXT,
                remote_only INTEGER,
                salary_min REAL,
                salary_max REAL,
                updated_at TEXT
            )
            """
        )

        import json

        now = datetime.datetime.utcnow().isoformat()
        cursor.execute(
            """
            INSERT OR REPLACE INTO user_preferences
            (user_id, preferred_locations, experience_level, industries, remote_only, salary_min, salary_max, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                json.dumps(data.preferred_locations or []),
                data.experience_level,
                json.dumps(data.industries or []),
                1 if data.remote_only else 0,
                data.salary_min,
                data.salary_max,
                now,
            ),
        )
        conn.commit()
        conn.close()

        return {"message": "Preferences saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save preferences: {str(e)}")


@router.get("/preferences")
async def get_preferences(user: dict = Depends(get_current_user)):
    """Get user preferences."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user["id"],))
        row = cursor.fetchone()
        conn.close()

        if row:
            import json

            row_dict = dict(row)
            row_dict["preferred_locations"] = json.loads(row_dict.get("preferred_locations", "[]"))
            row_dict["industries"] = json.loads(row_dict.get("industries", "[]"))
            row_dict["remote_only"] = bool(row_dict.get("remote_only", 0))
            return row_dict

        return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch preferences: {str(e)}")
