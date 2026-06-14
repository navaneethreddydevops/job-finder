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
            applied INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def save_job(job_dict):
    """
    Saves or updates a job. If URL already exists, keeps the existing applied status
    but updates other details.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if job with this URL already exists
    cursor.execute("SELECT id, applied FROM jobs WHERE url = ?", (job_dict["url"],))
    row = cursor.fetchone()
    
    key_reqs_json = json.dumps(job_dict.get("key_requirements", []))
    
    if row:
        # Update existing job but preserve applied status
        job_id = row["id"]
        cursor.execute("""
            UPDATE jobs
            SET title = ?, company = ?, location = ?, date_posted = ?, 
                c2c_viability = ?, key_requirements = ?, contact_email = ?, 
                contact_phone = ?, source = ?, description = ?
            WHERE id = ?
        """, (
            job_dict["title"],
            job_dict["company"],
            job_dict["location"],
            job_dict["date_posted"],
            job_dict["c2c_viability"],
            key_reqs_json,
            job_dict.get("contact_email"),
            job_dict.get("contact_phone"),
            job_dict["source"],
            job_dict["description"],
            job_id
        ))
    else:
        # Insert new job
        cursor.execute("""
            INSERT INTO jobs (
                title, company, location, url, date_posted, 
                c2c_viability, key_requirements, contact_email, 
                contact_phone, source, description, applied
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            job_dict["title"],
            job_dict["company"],
            job_dict["location"],
            job_dict["url"],
            job_dict["date_posted"],
            job_dict["c2c_viability"],
            key_reqs_json,
            job_dict.get("contact_email"),
            job_dict.get("contact_phone"),
            job_dict["source"],
            job_dict["description"]
        ))
    conn.commit()
    conn.close()

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
        # Convert applied integer to boolean
        job["applied"] = bool(job["applied"])
        jobs.append(job)
    conn.close()
    return jobs

def toggle_applied(job_id: int, applied: bool):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET applied = ? WHERE id = ?", (1 if applied else 0, job_id))
    conn.commit()
    conn.close()

def delete_all_jobs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs")
    conn.commit()
    conn.close()
