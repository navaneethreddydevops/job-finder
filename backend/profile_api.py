"""User careers-page profile (Task 9).

Stores everything a typical employer application form asks for — contact, links,
work authorization, preferences, experience, optional EEO self-identification, and
the canonical resume file — in the one-to-one ``user_profiles`` table. The stored
profile powers the autonomous apply agent (Task 10) and the onboarding wizard.

Named ``profile_api`` (not ``profile``) because backend/ is appended to sys.path and
a ``profile.py`` would shadow the stdlib ``profile`` module.
"""

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from auth import get_current_user
from db import (
    PROFILE_FIELDS,
    get_profile_resume,
    get_user_profile,
    is_profile_apply_ready,
    set_profile_resume,
    upsert_user_profile,
)
from resume import extract_text_from_docx

RESUME_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
VISA_STATUSES = {"", "us_citizen", "permanent_resident", "h1b", "opt_ead", "tn", "other"}

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileFullUpdate(BaseModel):
    """Partial update — only fields the client actually sent are applied.

    extra='forbid' rejects unknown keys (422) so typos never silently no-op.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_zip: str | None = None
    address_country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    authorized_us: bool | None = None
    requires_sponsorship: bool | None = None
    visa_status: str | None = None
    desired_roles: list[str] | None = None
    salary_min: int | None = None
    salary_currency: str | None = None
    notice_period: str | None = None
    availability_date: str | None = None
    willing_to_relocate: bool | None = None
    preferred_locations: list[str] | None = None
    years_experience: int | None = None
    current_title: str | None = None
    current_company: str | None = None
    education: list[dict] | None = None
    skills: list[str] | None = None
    eeo_gender: str | None = None
    eeo_race: str | None = None
    eeo_veteran: str | None = None
    eeo_disability: str | None = None
    onboarding_completed: bool | None = None
    onboarding_step: int | None = None


def _profile_response(user_id: int) -> dict:
    profile = get_user_profile(user_id)
    ready, missing = is_profile_apply_ready(profile)
    profile.pop("resume_text", None)  # can be large; fetch via the resume endpoints
    return {"profile": profile, "apply_ready": ready, "missing_fields": missing}


@router.get("/full")
async def get_full_profile(user: dict = Depends(get_current_user)):
    return _profile_response(user["id"])


@router.put("/full")
async def put_full_profile(
    req: ProfileFullUpdate, user: dict = Depends(get_current_user)
):
    fields = req.model_dump(exclude_unset=True)
    if "visa_status" in fields and fields["visa_status"] not in VISA_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid visa_status value.")
    for url_field in ("linkedin_url", "github_url", "portfolio_url"):
        value = (fields.get(url_field) or "").strip()
        if value and not value.lower().startswith(("http://", "https://")):
            fields[url_field] = f"https://{value}"
    # Only persist columns that exist (the model mirrors PROFILE_FIELDS, but keep
    # the whitelist authoritative in one place).
    fields = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    if fields:
        upsert_user_profile(user["id"], fields)
    return _profile_response(user["id"])


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


@router.post("/resume")
async def upload_resume(
    resume: UploadFile = File(...), user: dict = Depends(get_current_user)
):
    filename = (resume.filename or "").strip()
    lower = filename.lower()
    if not lower.endswith((".docx", ".pdf")):
        raise HTTPException(
            status_code=400, detail="Please upload a .docx or .pdf resume."
        )
    data = await resume.read()
    if len(data) > RESUME_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Resume must be 5 MB or smaller.")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    text = ""
    try:
        if lower.endswith(".docx"):
            text = extract_text_from_docx(data)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            text = _extract_pdf_text(data)
            mime = "application/pdf"
    except HTTPException:
        raise
    except Exception:
        # Store the blob anyway — the apply agent uploads the file itself; the
        # extracted text is a bonus for screening-question answers.
        mime = "application/pdf" if lower.endswith(".pdf") else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        text = ""

    set_profile_resume(user["id"], data, filename, mime, text)
    response = _profile_response(user["id"])
    response["resume_text_empty"] = not text
    response["resume_text_preview"] = text[:2000]
    return response


@router.get("/resume")
async def download_resume(user: dict = Depends(get_current_user)):
    stored = get_profile_resume(user["id"])
    if stored is None:
        raise HTTPException(status_code=404, detail="No resume uploaded yet.")
    blob, filename, mime, _text = stored
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/resume")
async def delete_resume(user: dict = Depends(get_current_user)):
    set_profile_resume(user["id"], None, "", "", "")
    return _profile_response(user["id"])
