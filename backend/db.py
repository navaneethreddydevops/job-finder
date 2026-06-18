"""Persistence layer.

Supports two backends transparently:

* **SQLite** (default) — used for local development and tests. Zero setup; the file
  lives next to this module (or at ``DATABASE_PATH``). Keeps ``uv run`` and the
  FastAPI ``TestClient`` workflow friction-free.
* **Postgres / Neon** — used in production (e.g. Render). Activated automatically when
  ``DATABASE_URL`` is a ``postgres://`` / ``postgresql://`` connection string.

The two drivers differ in small ways (``?`` vs ``%s`` placeholders, ``lastrowid`` vs
``RETURNING``, ``AUTOINCREMENT`` vs identity columns, ``BLOB`` vs ``BYTEA``). A thin
wrapper around the connection/cursor smooths over the placeholder difference so the rest
of the codebase can keep writing ``?``-style SQL, and the few remaining differences are
exposed as the ``IS_POSTGRES`` / ``AUTO_PK`` / ``BLOB_TYPE`` constants and the
``insert_returning_id`` helper.
"""

import json
import os
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# Backend-specific DDL fragments. The rest of the SQL is portable.
AUTO_PK = (
    "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
    if IS_POSTGRES
    else "INTEGER PRIMARY KEY AUTOINCREMENT"
)
BLOB_TYPE = "BYTEA" if IS_POSTGRES else "BLOB"


# ---------------------------------------------------------------------------
# SQLite-only path setup
# ---------------------------------------------------------------------------
if not IS_POSTGRES:
    DB_PATH = os.getenv(
        "DATABASE_PATH", os.path.join(os.path.dirname(__file__), "jobs.db")
    )
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Postgres connection wrappers
#
# psycopg uses %s placeholders; the rest of the codebase writes ? placeholders.
# These wrappers translate on the way through so call sites stay backend-agnostic.
# (No SQL in this project contains a literal '?', so the substitution is safe.)
# ---------------------------------------------------------------------------
class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        return self._cur.execute(sql.replace("?", "%s"), params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _PgConnection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db_connection():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return _PgConnection(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def insert_returning_id(cursor, sql, params, id_col="id"):
    """Run an INSERT and return the new primary key on both backends.

    ``sql`` must NOT include a RETURNING clause — it is appended for Postgres.
    """
    if IS_POSTGRES:
        cursor.execute(sql + f" RETURNING {id_col}", params)
        return cursor.fetchone()[id_col]
    cursor.execute(sql, params)
    return cursor.lastrowid


def _column_exists(cursor, table, column):
    """Backend-agnostic check for whether a column already exists."""
    if IS_POSTGRES:
        cursor.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table, column),
        )
        return cursor.fetchone() is not None
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS jobs (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            date_posted TEXT,
            c2c_viability TEXT,
            key_requirements TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            source TEXT,
            description TEXT,
            applied INTEGER DEFAULT 0,
            posted_within_24h INTEGER DEFAULT 0,
            UNIQUE(user_id, url)
        )
        """
    )

    if IS_POSTGRES:
        # Fresh Neon databases are created with the full schema above; only guard
        # against an older deployment missing the newest column.
        if not _column_exists(cursor, "jobs", "posted_within_24h"):
            cursor.execute(
                "ALTER TABLE jobs ADD COLUMN posted_within_24h INTEGER DEFAULT 0"
            )
    else:
        # Legacy SQLite databases may predate some columns — migrate in place.
        if not _column_exists(cursor, "jobs", "posted_within_24h"):
            cursor.execute(
                "ALTER TABLE jobs ADD COLUMN posted_within_24h INTEGER DEFAULT 0"
            )
        if not _column_exists(cursor, "jobs", "user_id"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN user_id INTEGER")
            # Clean slate for multi-tenant: drop pre-user_id rows and rebuild with
            # the NOT NULL + UNIQUE(user_id, url) constraints.
            cursor.execute("DELETE FROM jobs WHERE user_id IS NULL")
            cursor.execute(
                """
                CREATE TABLE jobs_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT,
                    company TEXT,
                    location TEXT,
                    url TEXT,
                    date_posted TEXT,
                    c2c_viability TEXT,
                    key_requirements TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    source TEXT,
                    description TEXT,
                    applied INTEGER DEFAULT 0,
                    posted_within_24h INTEGER DEFAULT 0,
                    UNIQUE(user_id, url)
                )
                """
            )
            cursor.execute("DROP TABLE jobs")
            cursor.execute("ALTER TABLE jobs_new RENAME TO jobs")

    conn.commit()
    conn.close()


def save_job(job_dict, user_id: int):
    """
    Saves or updates a job for a specific user and returns True if a new row was inserted,
    False if an existing row was updated.

    De-duplication is keyed on (user_id, URL) when present. Many scraped jobs come
    back with an empty/missing URL, so we synthesize a stable key from
    title|company|location for those — otherwise every URL-less job would collide on
    the UNIQUE(user_id, url) constraint and collapse into a single row.
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

    # Check if job with this URL already exists for this user
    cursor.execute("SELECT id, applied FROM jobs WHERE url = ? AND user_id = ?", (url, user_id))
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
            WHERE id = ? AND user_id = ?
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
                user_id,
            ),
        )
    else:
        # Insert new job
        inserted = True
        cursor.execute(
            """
            INSERT INTO jobs (
                user_id, title, company, location, url, date_posted,
                c2c_viability, key_requirements, contact_email,
                contact_phone, source, description, applied, posted_within_24h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
            (
                user_id,
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


def get_user_jobs(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE user_id = ? ORDER BY id DESC", (user_id,))
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


def toggle_applied(user_id: int, job_id: int, applied: bool):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET applied = ? WHERE id = ? AND user_id = ?",
        (1 if applied else 0, job_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_user_jobs(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
