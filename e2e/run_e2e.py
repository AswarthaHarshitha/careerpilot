"""End-to-end check of the whole CareerPilot pipeline on this machine.

    make e2e          (or: cd backend && uv run python ../e2e/run_e2e.py)

Starts a mock career site and mock GitHub API (local test fixtures), runs database migrations on a fresh
SQLite database, starts the real API server with its background workers, then drives the full workflow
over HTTP:

    mock IBM job page -> JD extraction -> analysis -> profile match -> GitHub evidence -> project selection
    -> tailored resume in the LaTeX template -> PDF -> apply agent opens the mock form (headless Chromium)
    -> autofill + resume upload -> user answers and confirms -> submission -> confirmation detection
    -> application record verified

Exits non-zero on the first failed check.
"""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from tests.support.mock_site import GH_USER, SUBMISSIONS, MockSite  # noqa: E402

TEMPLATE = BACKEND / "tests" / "fixtures" / "templates" / "jake_style.tex"
PASSWORD = "E2e-" + secrets.token_urlsafe(12) + "-9a"
checks: list[str] = []


def ok(msg: str) -> None:
    checks.append(msg)
    print(f"  ✓ {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"  ✗ {msg}", flush=True)
    raise SystemExit(1)


def expect(cond, msg: str) -> None:
    ok(msg) if cond else fail(msg)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_task(c: httpx.Client, task_id: int, timeout: float = 240) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        t = c.get(f"/api/tasks/{task_id}").json()
        if t["status"] in ("succeeded", "failed"):
            return t
        time.sleep(0.5)
    fail(f"task {task_id} did not finish in {timeout}s")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="careerpilot-e2e-")
    port = free_port()
    with MockSite() as site:
        env = {
            **os.environ,
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{tmp}/e2e.db",
            "JWT_SECRET": secrets.token_urlsafe(40),
            "WORKER_ENABLED": "true",
            "FETCH_ALLOW_PRIVATE_HOSTS": "127.0.0.1",
            "GITHUB_API_BASE": f"{site.base}/github-api",
            "GITHUB_RAW_BASE": f"{site.base}/github-raw",
            "GITHUB_TOKEN": "",
            "LLM_PROVIDER": "none",
            "ALLOW_REGISTRATION": "false",
        }
        print("CareerPilot end-to-end test")
        print(f"  mock site {site.base} · api http://127.0.0.1:{port} · data {tmp}\n")
        mig = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env,
                             capture_output=True, text=True)
        expect(mig.returncode == 0, "database migrations apply to a fresh database")
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level",
                                "warning"], cwd=BACKEND, env=env)
        try:
            run_flow(f"http://127.0.0.1:{port}", site, tmp)
        finally:
            api.terminate()
            api.wait(timeout=10)
    print(f"\nAll {len(checks)} checks passed.")


def run_flow(base: str, site: MockSite, tmp: str) -> None:
    c = httpx.Client(base_url=base, timeout=60)
    for _ in range(60):
        try:
            if c.get("/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    health = c.get("/api/health").json()
    expect(health["status"] == "ok" and health["latex_engine"] != "missing", "API is healthy with a LaTeX engine")

    r = c.post("/api/auth/register", json={"email": "e2e@example.com", "password": PASSWORD, "display_name": "E2E"})
    expect(r.status_code == 200, "owner account created")
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    # --- profile & template --------------------------------------------------------------------
    r = c.post("/api/templates", files={"file": ("resume.tex", TEMPLATE.read_bytes(), "text/plain")})
    tpl = r.json()["template"]
    t = wait_task(c, r.json()["task_id"])
    expect(t["status"] == "succeeded", "resume template parsed and compiled")
    expect([s["kind"] for s in tpl["sections"]] == ["summary", "experience", "projects", "skills", "education",
                                                     "achievements"], "template sections recognised")
    imported = c.post(f"/api/templates/{tpl['id']}/import-profile").json()["imported"]
    expect(imported["experiences"] == 1 and imported["skills"] > 5, "career profile imported from the template")
    c.put("/api/profile", json={**c.get("/api/profile").json()["personal"], "full_name": "Test Candidate",
                                "first_name": "Test", "last_name": "Candidate", "email": "candidate@example.com",
                                "work_authorization": "Authorized to work in India", "requires_sponsorship": False,
                                "years_of_experience": 0.5})

    # --- GitHub evidence ------------------------------------------------------------------------
    r = c.put("/api/settings/github", json={"username": GH_USER, "include_private_repos": False,
                                            "include_forks": False})
    expect(r.status_code == 200, "GitHub account connected (mock API)")
    t = wait_task(c, c.post("/api/github/scan", json={}).json()["task_id"])
    expect(t["status"] == "succeeded" and t["result"]["repositories"] == 2, "repositories scanned")
    projects = {p["name"]: p for p in c.get("/api/projects").json()}
    tf = c.get(f"/api/projects/{projects['taskflow-api']['id']}").json()
    verified = {e["canonical"] for e in tf["evidence"] if e["status"] == "verified"}
    expect({"fastapi", "postgresql", "docker", "pytest"} <= verified, "FastAPI/PostgreSQL/Docker/pytest verified from "
                                                                       "the repository")
    inferred = {e["canonical"] for e in tf["evidence"] if e["status"] == "inferred"}
    expect("rbac" in inferred or "rbac" in verified, "access-control evidence recorded with its trust level")

    # --- job ingestion & analysis ---------------------------------------------------------------
    r = c.post("/api/jobs", json={"url": f"{site.base}/careers/ibm/jobs/12345"})
    job_id = r.json()["job"]["id"]
    t = wait_task(c, r.json()["task_id"])
    expect(t["status"] == "succeeded", "job page fetched and analysed")
    job = c.get(f"/api/jobs/{job_id}").json()
    expect(job["company"] == "IBM" and job["role_title"] == "Software Engineer" and job["job_code"] == "12345",
           "company, role and job ID extracted")
    expect(job["deadline"] == "2026-11-30" and "30 November 2026" in job["deadline_source"],
           "deadline taken only from the posting text")
    req = {q["canonical"]: q for q in job["requirements"] if q["canonical"]}
    expect(req["python"]["importance"] == "required" and req["kubernetes"]["importance"] == "preferred",
           "requirements classified as required / preferred")
    expect(req["fastapi"]["match"]["status"] == "strong", "FastAPI matched with strong evidence")
    expect(req["aws"]["match"]["status"] == "missing", "missing skills reported as gaps")
    expect(job["recommended_projects"][0]["title"] == "Taskflow Api", "most relevant project selected")

    # --- resume ---------------------------------------------------------------------------------
    t = wait_task(c, c.post(f"/api/jobs/{job_id}/resumes", json={}).json()["task_id"])
    expect(t["status"] == "succeeded", "tailored resume generated")
    vid = t["result"]["resume_version_id"]
    v = c.get(f"/api/resumes/{vid}").json()
    pdf = c.get(f"/api/resumes/{vid}/pdf")
    expect(pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF"), "PDF rendered")
    src = c.get(f"/api/resumes/{vid}/source").text
    original = TEMPLATE.read_text()
    expect(src.split("\\begin{document}")[0] == original.split("\\begin{document}")[0],
           "template preamble (layout, fonts, macros) preserved exactly")
    expect("Taskflow Api" in src and "\\resumeProjectHeading" in src, "selected project rendered in the template's "
                                                                       "own entry format")
    expect(v["ats_report"]["summary"]["jd_coverage"] in ("Strong", "Good", "Fair", "Weak"),
           "ATS check produced diagnostics")
    expect(v["recruiter_report"]["summary"]["potential_gaps"] >= 0, "recruiter review produced diagnostics")
    expect("aws" not in src.lower(), "no unsupported skill (AWS) added to the resume")
    expect(c.post(f"/api/resumes/{vid}/approve").status_code == 200, "resume approved")

    # --- assisted application -------------------------------------------------------------------
    app_id = job["application"]["id"]
    token = c.post("/api/settings/runner-tokens", json={"name": "e2e"}).json()["token"]
    expect(c.post(f"/api/applications/{app_id}/automation", json={}).status_code == 200, "application prepared")

    from app.automation.runner import Api, run_loop

    results: list[str] = []
    runner = threading.Thread(target=lambda: results.extend(run_loop(
        Api(base, token), headless=True, once=True, profile_dir=f"{tmp}/browser", user_wait=120)), daemon=True)
    runner.start()

    def app_state():
        return c.get(f"/api/applications/{app_id}").json()

    end = time.time() + 120
    a = app_state()
    while time.time() < end and not any(f["field_key"] == "why_company" for f in a["fields"]):
        time.sleep(0.7)
        a = app_state()
    expect(any(f["field_key"] == "why_company" for f in a["fields"]), "agent reached step 2 of the mock form")
    fields = {f["field_key"]: f for f in a["fields"]}
    expect(fields["first_name"]["status"] == "filled" and fields["resume"]["status"] == "filled",
           "profile fields and resume upload filled automatically")
    expect(fields["gender"]["value"] == "" and fields["consent"]["status"] == "needs_confirmation",
           "sensitive and consent questions left for the user")
    expect(SUBMISSIONS == [], "nothing submitted before confirmation")

    for key, value in (("work_authorization", "Yes"), ("sponsorship", None), ("consent", None),
                       ("why_company", "I want to build developer platform services."),
                       ("gender", "Prefer not to say")):
        r = c.patch(f"/api/applications/{app_id}/fields/{fields[key]['id']}", json={"value": value, "confirm": True})
        if r.status_code != 200:
            fail(f"answering {key}: {r.text}")
    ok("user answered and confirmed the remaining questions")

    end = time.time() + 60
    while time.time() < end:
        s = app_state()["session"]
        if s["state"] == "awaiting_review" and s["filled_revision"] >= s["answers_revision"]:
            break
        time.sleep(0.5)
    expect(app_state()["session"]["state"] == "awaiting_review", "application ready for review")
    r = c.post(f"/api/applications/{app_id}/automation/confirm-submit", json={"typed_confirmation": "IBM"})
    expect(r.status_code == 200, "user confirmed final submission")
    runner.join(timeout=90)
    expect(results == ["submitted"], "agent submitted and detected the confirmation page")

    a = app_state()
    expect(a["status"] == "submitted" and a["submission_verified"], "application tracked as submitted (verified)")
    expect(a["resume_version"]["id"] == vid and a["date_applied"], "submission linked to the resume version and date")
    expect(len(SUBMISSIONS) == 1, "exactly one submission reached the career site")
    sent = SUBMISSIONS[0]
    expect(sent["fields"]["email"] == "candidate@example.com" and sent["fields"]["sponsorship"] == "no",
           "submitted values match the profile and the user's answers")
    expect(sent["files"]["resume"]["is_pdf"], "the tailored PDF was uploaded")
    dash = c.get("/api/dashboard").json()
    expect(dash["pipeline"][2]["count"] == 1, "dashboard pipeline shows the application as applied")


if __name__ == "__main__":
    main()
