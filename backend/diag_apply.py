"""Manual smoke-test harness for the autonomous apply agent (Task 10).

Runs the REAL agent (Claude via OAuth + Playwright MCP headless browser) against the
dev-only mock careers form, so the full prompt → browser → submit → structured-result
path is exercised without flaky external ATS sites. Not part of CI — each variant is
a real Claude run.

Usage (backend must be running with APPLY_AGENT_MOCK=1 in .env):

    uv run python backend/diag_apply.py                # happy path → submitted
    uv run python backend/diag_apply.py captcha        # → needs_review (captcha)
    uv run python backend/diag_apply.py login          # → needs_review (login wall)
    uv run python backend/diag_apply.py weird_question # → needs_review (unanswerable)

Uses the seeded test@test.com account; seeds/updates its profile + resume, points a
job at the mock form (optionally with a ?mode= variant), and drives the endpoint the
dashboard uses, polling until the run finishes.
"""

import io
import os
import sys
import time

# Same OAuth-only rule as every backend entrypoint.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

import requests  # noqa: E402

BASE = os.getenv("APPLY_DIAG_BASE", "http://localhost:8000")
MODE = sys.argv[1] if len(sys.argv) > 1 else ""


def main():
    r = requests.post(f"{BASE}/api/login", json={"email": "test@test.com", "password": "testtest"})
    r.raise_for_status()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    # Apply-ready profile + resume for the seeded user.
    r = requests.put(f"{BASE}/api/profile/full", headers=h, json={
        "full_name": "Test User", "email": "test@test.com", "phone": "+1 555 000 1111",
        "address_city": "Austin", "address_state": "TX",
        "authorized_us": True, "requires_sponsorship": False,
        "years_experience": 10, "skills": ["Kubernetes", "AWS", "Terraform"],
    })
    r.raise_for_status()
    from docx import Document  # local import keeps startup cheap

    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("Test User — Principal DevOps Engineer. 10 years: Kubernetes, AWS, Terraform.")
    d.save(buf)
    r = requests.post(f"{BASE}/api/profile/resume", headers=h,
                      files={"resume": ("diag_resume.docx", buf.getvalue())})
    r.raise_for_status()
    assert r.json()["apply_ready"], f"profile not apply-ready: {r.json()['missing_fields']}"

    # Job pointing at the mock form (variant via ?mode=).
    url = f"{BASE}/api/dev/mock-application" + (f"?mode={MODE}" if MODE else "")
    assert requests.get(url).status_code == 200, "mock form unavailable — set APPLY_AGENT_MOCK=1"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    from db import save_job, get_user_jobs

    user_id = requests.get(f"{BASE}/api/me", headers=h).json()["user"]["id"]
    save_job({"title": f"Principal DevOps Engineer ({MODE or 'happy'})", "company": "MockCorp",
              "url": url, "source": "Greenhouse", "location": "Remote (US)"}, user_id)
    job_id = next(j["id"] for j in get_user_jobs(user_id) if j["url"] == url)

    r = requests.post(f"{BASE}/api/jobs/{job_id}/apply-agent", headers=h)
    print("start:", r.status_code, r.json())
    r.raise_for_status()

    while True:
        s = requests.get(f"{BASE}/api/jobs/{job_id}/apply-agent/status", headers=h).json()
        print("…", s["apply_status"])
        if s["apply_status"] not in ("queued", "running"):
            break
        time.sleep(5)

    print("\nFINAL:", s["apply_status"], "|", s.get("apply_error") or "")
    print("\n".join(s["progress_lines"]))
    expected = "submitted" if not MODE else "needs_review"
    print(f"\n{'PASS' if s['apply_status'] == expected else 'FAIL'} — expected {expected}")


if __name__ == "__main__":
    main()
