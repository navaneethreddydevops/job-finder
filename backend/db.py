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

import datetime
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
class _PgRow(dict):
    """Dict row that also supports positional access, like sqlite3.Row.

    psycopg's dict_row only allows row["col"], but several modules index rows
    positionally (row[0]). Dicts preserve insertion order and psycopg builds
    them in SELECT-column order, so integer indexing is well-defined.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):
        # sqlite3.Row iterates over VALUES (enabling `a, b = row` unpacking);
        # plain dicts iterate over keys. Match sqlite3.Row. dict(row) still
        # works correctly — the dict constructor uses keys()/__getitem__ for
        # Mapping arguments, not iteration.
        return iter(self.values())


def _pg_row_factory(cursor):
    columns = [c.name for c in cursor.description] if cursor.description else []

    def make_row(values):
        return _PgRow(zip(columns, values))

    return make_row


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        # Escape literal % (e.g. LIKE '%Senior%') before introducing %s
        # placeholders — psycopg treats bare % in the SQL as placeholder syntax.
        return self._cur.execute(sql.replace("%", "%%").replace("?", "%s"), params)

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

        conn = psycopg.connect(DATABASE_URL, row_factory=_pg_row_factory)
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


def ensure_users_table(cursor):
    """Create the users table if missing.

    Owned by auth.py conceptually, but defined here because init_db()'s tables
    declare FOREIGN KEY references to users — Postgres requires the FK target
    to exist at CREATE TABLE time (SQLite doesn't), so users must be creatable
    from db.py before any dependent table. auth.init_auth_db() calls this too
    and remains the place that seeds the test user.
    """
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id {AUTO_PK},
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            created_at TEXT
        )
        """
    )


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    ensure_users_table(cursor)
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
            key_requirements TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            source TEXT,
            description TEXT,
            applied INTEGER DEFAULT 0,
            posted_within_7d INTEGER DEFAULT 0,
            salary_min REAL,
            salary_max REAL,
            salary_currency TEXT,
            is_salary_extracted INTEGER DEFAULT 0,
            salary_confidence REAL,
            created_at TEXT,
            UNIQUE(user_id, url)
        )
        """
    )

    # Freshness flag: rename the legacy 24h column to 7d in place when present, then
    # ensure the column exists for very old databases. Works on both Postgres and
    # SQLite (>= 3.25 supports RENAME COLUMN).
    if _column_exists(cursor, "jobs", "posted_within_24h") and not _column_exists(
        cursor, "jobs", "posted_within_7d"
    ):
        cursor.execute(
            "ALTER TABLE jobs RENAME COLUMN posted_within_24h TO posted_within_7d"
        )
    if not _column_exists(cursor, "jobs", "posted_within_7d"):
        cursor.execute("ALTER TABLE jobs ADD COLUMN posted_within_7d INTEGER DEFAULT 0")

    if IS_POSTGRES:
        # Fresh Neon databases are created with the full schema above; only guard
        # against an older deployment missing the newest column.
        if not _column_exists(cursor, "jobs", "created_at"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
    else:
        # Legacy SQLite databases may predate some columns — migrate in place.
        if not _column_exists(cursor, "jobs", "salary_min"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN salary_min REAL")
            cursor.execute("ALTER TABLE jobs ADD COLUMN salary_max REAL")
            cursor.execute("ALTER TABLE jobs ADD COLUMN salary_currency TEXT")
            cursor.execute("ALTER TABLE jobs ADD COLUMN is_salary_extracted INTEGER DEFAULT 0")
            cursor.execute("ALTER TABLE jobs ADD COLUMN salary_confidence REAL")
        if not _column_exists(cursor, "jobs", "created_at"):
            cursor.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT")
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
                    key_requirements TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    source TEXT,
                    description TEXT,
                    applied INTEGER DEFAULT 0,
                    posted_within_7d INTEGER DEFAULT 0,
                    salary_min REAL,
                    salary_max REAL,
                    salary_currency TEXT,
                    is_salary_extracted INTEGER DEFAULT 0,
                    salary_confidence REAL,
                    created_at TEXT,
                    UNIQUE(user_id, url)
                )
                """
            )
            cursor.execute("DROP TABLE jobs")
            cursor.execute("ALTER TABLE jobs_new RENAME TO jobs")

    conn.commit()

    # Applications table for tracking application status
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS applications (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            status TEXT DEFAULT 'draft',
            cover_letter TEXT,
            applied_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, job_id),
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # Application history for tracking status changes
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS application_history (
            id {AUTO_PK},
            application_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT,
            notes TEXT,
            changed_at TEXT,
            FOREIGN KEY(application_id) REFERENCES applications(id)
        )
        """
    )

    # Bookmarks table for saving favorite jobs
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS bookmarks (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, job_id),
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # User skills for tracking expertise
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS user_skills (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            skill TEXT NOT NULL,
            proficiency TEXT DEFAULT 'intermediate',
            years_exp REAL,
            endorsed_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, skill),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # Job skills extracted from job descriptions
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_skills (
            id {AUTO_PK},
            job_id INTEGER NOT NULL,
            skill TEXT NOT NULL,
            is_required INTEGER DEFAULT 1,
            extracted_at TEXT,
            UNIQUE(job_id, skill),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )

    # Saved searches for recurring job searches
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS saved_searches (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            name TEXT NOT NULL,
            frequency TEXT DEFAULT 'manual',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            last_run_at TEXT,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    # Search runs for tracking automated search executions
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS search_runs (
            id {AUTO_PK},
            saved_search_id INTEGER NOT NULL,
            jobs_found_count INTEGER,
            new_jobs_count INTEGER,
            run_at TEXT,
            was_emailed INTEGER DEFAULT 0,
            FOREIGN KEY(saved_search_id) REFERENCES saved_searches(id)
        )
        """
    )

    # Per-user, per-query checkpoints for the manual /api/pull flow: the next run for
    # the same query narrows its search window to "since last successful run" and the
    # search tools skip URLs already stored — incremental search, no re-verifying
    # known jobs. (Distinct from saved_searches/search_runs, which belong to the
    # saved-searches feature.)
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS pull_checkpoints (
            id {AUTO_PK},
            user_id INTEGER NOT NULL,
            query_normalized TEXT NOT NULL,
            last_run_at TEXT,
            jobs_found INTEGER DEFAULT 0,
            UNIQUE(user_id, query_normalized),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.commit()
    conn.close()


def normalize_pull_query(query: str) -> str:
    """Canonical form of a Search Target query for checkpoint lookups."""
    return " ".join((query or "").lower().split())


def get_pull_checkpoint(user_id: int, query: str):
    """Returns {'last_run_at': str, 'jobs_found': int} for the user's last successful
    pull with this query, or None if this query has never completed a run."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_run_at, jobs_found FROM pull_checkpoints "
        "WHERE user_id = ? AND query_normalized = ?",
        (user_id, normalize_pull_query(query)),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"last_run_at": row["last_run_at"], "jobs_found": row["jobs_found"]}


def upsert_pull_checkpoint(user_id: int, query: str, jobs_found: int):
    """Records a successful pull run so the next run for the same query can search
    incrementally. Only call after a run completes without error."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pull_checkpoints (user_id, query_normalized, last_run_at, jobs_found)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, query_normalized)
        DO UPDATE SET last_run_at = excluded.last_run_at, jobs_found = excluded.jobs_found
        """,
        (user_id, normalize_pull_query(query), now, jobs_found),
    )
    conn.commit()
    conn.close()


def get_user_job_urls(user_id: int) -> set[str]:
    """Lightweight fetch of every stored job URL for a user (for cross-run dedup in the
    search tools — known URLs are filtered out server-side before the agent sees them)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM jobs WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {row["url"] for row in rows if row["url"]}


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
    posted_within_7d = 1 if job_dict.get("posted_within_7d") else 0

    inserted = False
    if row:
        # Update existing job but preserve applied status
        job_id = row["id"]
        cursor.execute(
            """
            UPDATE jobs
            SET title = ?, company = ?, location = ?, date_posted = ?,
                key_requirements = ?, contact_email = ?,
                contact_phone = ?, source = ?, description = ?, posted_within_7d = ?,
                salary_min = ?, salary_max = ?, salary_currency = ?,
                is_salary_extracted = ?, salary_confidence = ?
            WHERE id = ? AND user_id = ?
        """,
            (
                title,
                company,
                location,
                job_dict.get("date_posted"),
                key_reqs_json,
                job_dict.get("contact_email"),
                job_dict.get("contact_phone"),
                job_dict.get("source"),
                job_dict.get("description"),
                posted_within_7d,
                job_dict.get("salary_min"),
                job_dict.get("salary_max"),
                job_dict.get("salary_currency"),
                1 if job_dict.get("salary_min") or job_dict.get("salary_max") else 0,
                job_dict.get("salary_confidence", 0),
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
                key_requirements, contact_email,
                contact_phone, source, description, applied, posted_within_7d,
                salary_min, salary_max, salary_currency, is_salary_extracted, salary_confidence,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                user_id,
                title,
                company,
                location,
                url,
                job_dict.get("date_posted"),
                key_reqs_json,
                job_dict.get("contact_email"),
                job_dict.get("contact_phone"),
                job_dict.get("source"),
                job_dict.get("description"),
                posted_within_7d,
                job_dict.get("salary_min"),
                job_dict.get("salary_max"),
                job_dict.get("salary_currency", "USD"),
                1 if job_dict.get("salary_min") or job_dict.get("salary_max") else 0,
                job_dict.get("salary_confidence", 0),
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
        job["posted_within_7d"] = bool(job.get("posted_within_7d"))
        jobs.append(job)
    conn.close()
    return jobs


def get_job_for_user(job_id: int, user_id: int):
    """Get a job only if it belongs to the user. Returns None if not found or not owned."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    job = dict(row)
    try:
        job["key_requirements"] = json.loads(job["key_requirements"])
    except Exception:
        job["key_requirements"] = []
    job["applied"] = bool(job["applied"])
    job["posted_within_7d"] = bool(job.get("posted_within_7d"))
    return job


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


# ── Application Status Tracking ──────────────────────────────────────────


def create_application(user_id: int, job_id: int, status: str = "draft", cover_letter: str = None):
    """Create a new application record, or return existing one."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    cursor.execute(
        "SELECT id FROM applications WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    )
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    app_id = insert_returning_id(
        cursor,
        """
        INSERT INTO applications (user_id, job_id, status, cover_letter, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, job_id, status, cover_letter, now, now),
    )

    # Record initial status change
    cursor.execute(
        """
        INSERT INTO application_history (application_id, old_status, new_status, changed_at)
        VALUES (?, ?, ?, ?)
        """,
        (app_id, None, status, now),
    )

    conn.commit()
    conn.close()
    return app_id


def update_application_status(application_id: int, new_status: str, notes: str = None):
    """Update application status and record history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    # Get current status
    cursor.execute("SELECT status FROM applications WHERE id = ?", (application_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Application {application_id} not found")

    old_status = row["status"]
    if old_status == new_status:
        conn.close()
        return

    # Update status
    cursor.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now, application_id),
    )

    # Record history
    cursor.execute(
        """
        INSERT INTO application_history (application_id, old_status, new_status, notes, changed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (application_id, old_status, new_status, notes, now),
    )

    conn.commit()
    conn.close()


def get_application(application_id: int):
    """Get application details with history."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT a.*, j.title, j.company, j.location, j.url
        FROM applications a
        LEFT JOIN jobs j ON a.job_id = j.id
        WHERE a.id = ?
        """,
        (application_id,),
    )
    app_row = cursor.fetchone()
    if not app_row:
        conn.close()
        return None

    app = dict(app_row)

    # Get history
    cursor.execute(
        """
        SELECT * FROM application_history WHERE application_id = ? ORDER BY changed_at ASC
        """,
        (application_id,),
    )
    history_rows = cursor.fetchall()
    app["history"] = [dict(row) for row in history_rows]

    conn.close()
    return app


def get_user_applications(user_id: int, include_history: bool = False):
    """Get all applications for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT a.*, j.title, j.company, j.location, j.url, j.source
        FROM applications a
        LEFT JOIN jobs j ON a.job_id = j.id
        WHERE a.user_id = ?
        ORDER BY a.updated_at DESC
        """,
        (user_id,),
    )
    app_rows = cursor.fetchall()
    applications = []

    for app_row in app_rows:
        app = dict(app_row)
        if include_history:
            cursor.execute(
                """
                SELECT * FROM application_history WHERE application_id = ? ORDER BY changed_at ASC
                """,
                (app["id"],),
            )
            history_rows = cursor.fetchall()
            app["history"] = [dict(row) for row in history_rows]
        applications.append(app)

    conn.close()
    return applications


def get_application_stats(user_id: int):
    """Get application statistics for user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) as total_applications,
            SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) as applied_count,
            SUM(CASE WHEN status = 'interviewing' THEN 1 ELSE 0 END) as interviewing_count,
            SUM(CASE WHEN status = 'offer' THEN 1 ELSE 0 END) as offer_count,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected_count
        FROM applications
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()

    # SUM(...) over zero matching rows yields NULL, so coerce every field to an int.
    stats = dict(row) if row else {}
    return {
        "total_applications": stats.get("total_applications") or 0,
        "applied_count": stats.get("applied_count") or 0,
        "interviewing_count": stats.get("interviewing_count") or 0,
        "offer_count": stats.get("offer_count") or 0,
        "rejected_count": stats.get("rejected_count") or 0,
    }


def delete_application(application_id: int, user_id: int):
    """Delete an application (with ownership check)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute("SELECT id FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Application {application_id} not found or not owned by user {user_id}")

    # Delete history first
    cursor.execute("DELETE FROM application_history WHERE application_id = ?", (application_id,))
    # Delete application
    cursor.execute("DELETE FROM applications WHERE id = ?", (application_id,))

    conn.commit()
    conn.close()


# ── Bookmarks ───────────────────────────────────────────────────────────────


def toggle_bookmark(user_id: int, job_id: int):
    """Toggle bookmark status for a job. Returns True if now bookmarked, False if removed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    cursor.execute("SELECT id FROM bookmarks WHERE user_id = ? AND job_id = ?", (user_id, job_id))
    existing = cursor.fetchone()

    if existing:
        # Remove bookmark
        cursor.execute("DELETE FROM bookmarks WHERE user_id = ? AND job_id = ?", (user_id, job_id))
        conn.commit()
        conn.close()
        return False
    else:
        # Add bookmark
        cursor.execute(
            "INSERT INTO bookmarks (user_id, job_id, created_at) VALUES (?, ?, ?)",
            (user_id, job_id, now),
        )
        conn.commit()
        conn.close()
        return True


def is_bookmarked(user_id: int, job_id: int):
    """Check if a job is bookmarked by user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM bookmarks WHERE user_id = ? AND job_id = ?", (user_id, job_id))
    result = cursor.fetchone() is not None
    conn.close()
    return result


def get_user_bookmarks(user_id: int):
    """Get all bookmarked jobs for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT j.* FROM jobs j
        INNER JOIN bookmarks b ON j.id = b.job_id
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
        """,
        (user_id,),
    )
    rows = cursor.fetchall()
    bookmarks = []
    for row in rows:
        job = dict(row)
        try:
            job["key_requirements"] = json.loads(job["key_requirements"])
        except Exception:
            job["key_requirements"] = []
        job["applied"] = bool(job["applied"])
        job["posted_within_7d"] = bool(job.get("posted_within_7d"))
        job["is_bookmarked"] = True
        bookmarks.append(job)
    conn.close()
    return bookmarks


def get_bookmark_count(user_id: int):
    """Get count of bookmarked jobs for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM bookmarks WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0


# ── Saved Searches ──────────────────────────────────────────────────────────


def create_saved_search(user_id: int, query: str, name: str, frequency: str = "manual"):
    """Create a new saved search."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    search_id = insert_returning_id(
        cursor,
        """
        INSERT INTO saved_searches (user_id, query, name, frequency, is_active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (user_id, query, name, frequency, now),
    )
    conn.commit()
    conn.close()
    return search_id


def get_saved_searches(user_id: int, active_only: bool = False):
    """Get all saved searches for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if active_only:
        cursor.execute(
            "SELECT * FROM saved_searches WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        cursor.execute(
            "SELECT * FROM saved_searches WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
    rows = cursor.fetchall()
    searches = [dict(row) for row in rows]
    conn.close()
    return searches


def get_saved_search(search_id: int, user_id: int):
    """Get a specific saved search (with ownership check)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM saved_searches WHERE id = ? AND user_id = ?",
        (search_id, user_id),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_saved_search(search_id: int, user_id: int, query: str = None, name: str = None, frequency: str = None, is_active: int = None):
    """Update a saved search."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute(
        "SELECT id FROM saved_searches WHERE id = ? AND user_id = ?",
        (search_id, user_id),
    )
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Saved search {search_id} not found or not owned by user {user_id}")

    updates = []
    params = []
    if query is not None:
        updates.append("query = ?")
        params.append(query)
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if frequency is not None:
        updates.append("frequency = ?")
        params.append(frequency)
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(is_active)

    if updates:
        params.extend([search_id, user_id])
        cursor.execute(
            f"UPDATE saved_searches SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            params,
        )
    conn.commit()
    conn.close()


def delete_saved_search(search_id: int, user_id: int):
    """Delete a saved search and its history."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute(
        "SELECT id FROM saved_searches WHERE id = ? AND user_id = ?",
        (search_id, user_id),
    )
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Saved search {search_id} not found or not owned by user {user_id}")

    # Delete search runs first
    cursor.execute("DELETE FROM search_runs WHERE saved_search_id = ?", (search_id,))
    # Delete search
    cursor.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))

    conn.commit()
    conn.close()


def record_search_run(saved_search_id: int, jobs_found: int = 0, new_jobs: int = 0):
    """Record a search run."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    cursor.execute(
        """
        INSERT INTO search_runs (saved_search_id, jobs_found_count, new_jobs_count, run_at, was_emailed)
        VALUES (?, ?, ?, ?, 0)
        """,
        (saved_search_id, jobs_found, new_jobs, now),
    )

    # Update last_run_at on the saved search
    cursor.execute(
        "UPDATE saved_searches SET last_run_at = ? WHERE id = ?",
        (now, saved_search_id),
    )

    conn.commit()
    conn.close()


def get_search_history(search_id: int, limit: int = 10):
    """Get recent runs for a saved search."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM search_runs WHERE saved_search_id = ? ORDER BY run_at DESC LIMIT ?",
        (search_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── Salary Extraction & Analysis ────────────────────────────────────────────


def update_job_salary(job_id: int, user_id: int, salary_min: float = None, salary_max: float = None, currency: str = "USD", confidence: float = 1.0):
    """Update salary info for a job."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify ownership
    cursor.execute("SELECT id FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if not cursor.fetchone():
        conn.close()
        raise ValueError(f"Job {job_id} not found or not owned by user {user_id}")

    is_extracted = 1 if (salary_min or salary_max) else 0
    cursor.execute(
        """
        UPDATE jobs
        SET salary_min = ?, salary_max = ?, salary_currency = ?,
            is_salary_extracted = ?, salary_confidence = ?
        WHERE id = ? AND user_id = ?
        """,
        (salary_min, salary_max, currency, is_extracted, confidence, job_id, user_id),
    )
    conn.commit()
    conn.close()


def get_salary_stats(user_id: int, source: str = None):
    """Get salary statistics for user's jobs."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if source:
        cursor.execute(
            """
            SELECT
                MIN(salary_min) as min_salary,
                MAX(salary_max) as max_salary,
                AVG((salary_min + salary_max) / 2.0) as avg_salary,
                COUNT(*) as total_jobs,
                SUM(CASE WHEN salary_min IS NOT NULL THEN 1 ELSE 0 END) as jobs_with_salary
            FROM jobs
            WHERE user_id = ? AND source = ? AND is_salary_extracted = 1
            """,
            (user_id, source),
        )
    else:
        cursor.execute(
            """
            SELECT
                MIN(salary_min) as min_salary,
                MAX(salary_max) as max_salary,
                AVG((salary_min + salary_max) / 2.0) as avg_salary,
                COUNT(*) as total_jobs,
                SUM(CASE WHEN salary_min IS NOT NULL THEN 1 ELSE 0 END) as jobs_with_salary
            FROM jobs
            WHERE user_id = ? AND is_salary_extracted = 1
            """,
            (user_id,),
        )

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_jobs_by_salary_range(user_id: int, min_salary: float = None, max_salary: float = None):
    """Get jobs within a salary range."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if min_salary and max_salary:
        cursor.execute(
            """
            SELECT * FROM jobs
            WHERE user_id = ? AND is_salary_extracted = 1
            AND salary_max >= ? AND salary_min <= ?
            ORDER BY salary_max DESC
            """,
            (user_id, min_salary, max_salary),
        )
    elif min_salary:
        cursor.execute(
            """
            SELECT * FROM jobs
            WHERE user_id = ? AND is_salary_extracted = 1
            AND salary_max >= ?
            ORDER BY salary_max DESC
            """,
            (user_id, min_salary),
        )
    elif max_salary:
        cursor.execute(
            """
            SELECT * FROM jobs
            WHERE user_id = ? AND is_salary_extracted = 1
            AND salary_min <= ?
            ORDER BY salary_max DESC
            """,
            (user_id, max_salary),
        )
    else:
        cursor.execute(
            """
            SELECT * FROM jobs
            WHERE user_id = ? AND is_salary_extracted = 1
            ORDER BY salary_max DESC
            """,
            (user_id,),
        )

    rows = cursor.fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        try:
            job["key_requirements"] = json.loads(job["key_requirements"])
        except Exception:
            job["key_requirements"] = []
        job["applied"] = bool(job["applied"])
        job["posted_within_7d"] = bool(job.get("posted_within_7d"))
        jobs.append(job)
    conn.close()
    return jobs


# ── Skills Management ───────────────────────────────────────────────────────


def add_user_skill(user_id: int, skill: str, proficiency: str = "intermediate", years_exp: float = None):
    """Add or update a user skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    cursor.execute(
        "SELECT id FROM user_skills WHERE user_id = ? AND skill = ?",
        (user_id, skill),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            """
            UPDATE user_skills
            SET proficiency = ?, years_exp = ?, updated_at = ?
            WHERE user_id = ? AND skill = ?
            """,
            (proficiency, years_exp, now, user_id, skill),
        )
    else:
        cursor.execute(
            """
            INSERT INTO user_skills (user_id, skill, proficiency, years_exp, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, skill, proficiency, years_exp, now, now),
        )
    conn.commit()
    conn.close()


def get_user_skills(user_id: int):
    """Get all skills for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM user_skills WHERE user_id = ? ORDER BY proficiency DESC, skill ASC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_job_skills(job_id: int):
    """Get all skills extracted from a job."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT skill, is_required FROM job_skills WHERE job_id = ? ORDER BY is_required DESC, skill ASC",
        (job_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_job_skills(job_id: int, skills: list):
    """Add extracted skills to a job. Skills format: [{skill: 'Python', is_required: 1}, ...]"""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()

    # Clear existing skills
    cursor.execute("DELETE FROM job_skills WHERE job_id = ?", (job_id,))

    # Add new skills
    for skill_item in skills:
        skill = skill_item.get("skill", "").strip() if isinstance(skill_item, dict) else str(skill_item).strip()
        if skill:
            is_required = skill_item.get("is_required", 1) if isinstance(skill_item, dict) else 1
            cursor.execute(
                """
                INSERT OR IGNORE INTO job_skills (job_id, skill, is_required, extracted_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, skill, is_required, now),
            )

    conn.commit()
    conn.close()


def get_skills_gap(user_id: int, job_id: int):
    """Analyze skill gaps between user and job requirements."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get user skills (lowercase for comparison)
    cursor.execute("SELECT skill FROM user_skills WHERE user_id = ?", (user_id,))
    user_skills = {row["skill"].lower() for row in cursor.fetchall()}

    # Get required job skills
    cursor.execute(
        "SELECT skill, is_required FROM job_skills WHERE job_id = ? AND is_required = 1",
        (job_id,),
    )
    required_skills = [(row["skill"], row["is_required"]) for row in cursor.fetchall()]

    conn.close()

    # Analyze gaps
    matched = []
    missing = []

    for skill, _ in required_skills:
        if skill.lower() in user_skills:
            matched.append(skill)
        else:
            missing.append(skill)

    return {
        "matched_skills": matched,
        "missing_skills": missing,
        "match_percentage": (len(matched) / len(required_skills) * 100) if required_skills else 100,
        "total_required": len(required_skills),
    }


def delete_user_skill(user_id: int, skill: str):
    """Delete a user skill."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_skills WHERE user_id = ? AND skill = ?",
        (user_id, skill),
    )
    conn.commit()
    conn.close()
