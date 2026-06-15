import sqlite3
import json
import os

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "jobs.db"))

# Ensure the directory for the database exists
db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT UNIQUE,
            date_posted TEXT,
            c2c_viability TEXT,
            key_requirements TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            source TEXT,
            description TEXT,
            applied INTEGER DEFAULT 0,
            posted_within_24h INTEGER DEFAULT 0
        )
    """)
    # Migrate existing databases that predate the posted_within_24h column.
    cursor.execute("PRAGMA table_info(jobs)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "posted_within_24h" not in existing_cols:
        cursor.execute(
            "ALTER TABLE jobs ADD COLUMN posted_within_24h INTEGER DEFAULT 0"
        )
    conn.commit()
    conn.close()


def save_job(job_dict):
    """
    Saves or updates a job and returns True if a new row was inserted, False if an
    existing row was updated.

    De-duplication is keyed on the posting URL when present. Many scraped jobs come
    back with an empty/missing URL, so we synthesize a stable key from
    title|company|location for those — otherwise every URL-less job would collide on
    the UNIQUE(url) constraint and collapse into a single row.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    title = job_dict.get("title", "")
    company = job_dict.get("company", "")
    location = job_dict.get("location", "")

    url = (job_dict.get("url") or "").strip()
    if not url:
        url = f"manual:{title}|{company}|{location}"
    job_dict["url"] = url

    # Check if job with this URL already exists
    cursor.execute("SELECT id, applied FROM jobs WHERE url = ?", (url,))
    row = cursor.fetchone()

    key_reqs_json = json.dumps(job_dict.get("key_requirements", []))
    posted_within_24h = 1 if job_dict.get("posted_within_24h") else 0

    inserted = False
    if row:
        # Update existing job but preserve applied status
        job_id = row["id"]
        cursor.execute(
            """
            UPDATE jobs
            SET title = ?, company = ?, location = ?, date_posted = ?,
                c2c_viability = ?, key_requirements = ?, contact_email = ?,
                contact_phone = ?, source = ?, description = ?, posted_within_24h = ?
            WHERE id = ?
        """,
            (
                title,
                company,
                location,
                job_dict.get("date_posted"),
                job_dict.get("c2c_viability"),
                key_reqs_json,
                job_dict.get("contact_email"),
                job_dict.get("contact_phone"),
                job_dict.get("source"),
                job_dict.get("description"),
                posted_within_24h,
                job_id,
            ),
        )
    else:
        # Insert new job
        inserted = True
        cursor.execute(
            """
            INSERT INTO jobs (
                title, company, location, url, date_posted,
                c2c_viability, key_requirements, contact_email,
                contact_phone, source, description, applied, posted_within_24h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
            (
                title,
                company,
                location,
                url,
                job_dict.get("date_posted"),
                job_dict.get("c2c_viability"),
                key_reqs_json,
                job_dict.get("contact_email"),
                job_dict.get("contact_phone"),
                job_dict.get("source"),
                job_dict.get("description"),
                posted_within_24h,
            ),
        )
    conn.commit()
    conn.close()
    return inserted


def get_all_jobs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs ORDER BY id DESC")
    rows = cursor.fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        # Parse JSON string back to list
        try:
            job["key_requirements"] = json.loads(job["key_requirements"])
        except Exception:
            job["key_requirements"] = []
        # Convert integer flags to booleans
        job["applied"] = bool(job["applied"])
        job["posted_within_24h"] = bool(job.get("posted_within_24h"))
        jobs.append(job)
    conn.close()
    return jobs


def toggle_applied(job_id: int, applied: bool):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET applied = ? WHERE id = ?", (1 if applied else 0, job_id)
    )
    conn.commit()
    conn.close()


def delete_all_jobs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs")
    conn.commit()
    conn.close()
