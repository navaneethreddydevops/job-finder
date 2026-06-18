"""Authentication: user accounts, sessions, and profile management.

Uses the same database as the jobs store (SQLite locally, Postgres/Neon in
production — see ``db.py``). Passwords are hashed with
PBKDF2-HMAC-SHA256 (stdlib only, no external crypto deps). Bearer tokens are random
url-safe strings persisted in an `auth_sessions` table.

The username is the user's email; passwords must be at least 8 characters.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from db import AUTO_PK, get_db_connection, insert_returning_id

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PBKDF2_ROUNDS = 200_000

# Seeded test account (per app_spec).
TEST_EMAIL = "test@test.com"
TEST_PASSWORD = "testtest"


# ---------------------------------------------------------------------------
# Schema / seeding
# ---------------------------------------------------------------------------
def init_auth_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()
    # Seed the test user once.
    if get_user_by_email(TEST_EMAIL) is None:
        create_user(TEST_EMAIL, TEST_PASSWORD, full_name="Test User")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    )
    return dk.hex()


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt), expected_hash)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------
def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"] or "",
        "phone": row["phone"] or "",
        "created_at": row["created_at"],
    }


def get_user_by_email(email: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
    row = cur.fetchone()
    conn.close()
    return row


def get_user_by_id(user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def create_user(email: str, password: str, full_name: str = "", phone: str = "") -> dict:
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    conn = get_db_connection()
    cur = conn.cursor()
    user_id = insert_returning_id(
        cur,
        """INSERT INTO users (email, password_hash, salt, full_name, phone, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            email.lower().strip(),
            password_hash,
            salt,
            full_name,
            phone,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return _row_to_user(get_user_by_id(user_id))


def _issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auth_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return token


def _revoke_token(token: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def _user_id_for_token(token: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM auth_sessions WHERE token = ?", (token,))
    row = cur.fetchone()
    conn.close()
    return row["user_id"] if row else None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    user_id = _user_id_for_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    row = get_user_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    user = _row_to_user(row)
    user["_token"] = token
    return user


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str | None = ""
    phone: str | None = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["auth"])


def _validate_credentials(email: str, password: str):
    if not email or not EMAIL_RE.match(email.strip()):
        raise HTTPException(status_code=400, detail="A valid email is required.")
    if not password or len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters."
        )


@router.post("/register")
async def register(req: RegisterRequest):
    _validate_credentials(req.email, req.password)
    if get_user_by_email(req.email) is not None:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    user = create_user(req.email, req.password, req.full_name or "", req.phone or "")
    token = _issue_token(user["id"])
    return {"token": token, "user": user}


@router.post("/login")
async def login(req: LoginRequest):
    row = get_user_by_email(req.email)
    if row is None or not _verify_password(req.password, row["salt"], row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = _issue_token(row["id"])
    return {"token": token, "user": _row_to_user(row)}


@router.post("/logout")
async def logout(user: dict = Depends(get_current_user)):
    _revoke_token(user["_token"])
    return {"success": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    user.pop("_token", None)
    return {"user": user}


@router.patch("/profile")
async def update_profile(req: ProfileUpdate, user: dict = Depends(get_current_user)):
    fields = {}
    if req.full_name is not None:
        fields["full_name"] = req.full_name
    if req.phone is not None:
        fields["phone"] = req.phone
    if req.email is not None:
        new_email = req.email.lower().strip()
        if not EMAIL_RE.match(new_email):
            raise HTTPException(status_code=400, detail="A valid email is required.")
        existing = get_user_by_email(new_email)
        if existing is not None and existing["id"] != user["id"]:
            raise HTTPException(status_code=400, detail="Email already in use.")
        fields["email"] = new_email
    if not fields:
        return {"user": _row_to_user(get_user_by_id(user["id"]))}

    conn = get_db_connection()
    cur = conn.cursor()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    cur.execute(
        f"UPDATE users SET {set_clause} WHERE id = ?",
        (*fields.values(), user["id"]),
    )
    conn.commit()
    conn.close()
    return {"user": _row_to_user(get_user_by_id(user["id"]))}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest, user: dict = Depends(get_current_user)
):
    row = get_user_by_id(user["id"])
    if not _verify_password(req.current_password, row["salt"], row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if not req.new_password or len(req.new_password) < 8:
        raise HTTPException(
            status_code=400, detail="New password must be at least 8 characters."
        )
    salt = secrets.token_hex(16)
    password_hash = _hash_password(req.new_password, salt)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
        (password_hash, salt, user["id"]),
    )
    conn.commit()
    conn.close()
    return {"success": True}
