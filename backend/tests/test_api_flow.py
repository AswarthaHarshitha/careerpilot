import pytest

from app.core.db import session_factory
from app.models import Project, ProjectEvidence
from app.resume.latex_compile import resolve_engine
from tests.conftest import FIXTURES, SAMPLE_JD, run_tasks


def upload_template(c, name="jake_style.tex"):
    data = (FIXTURES / "templates" / name).read_bytes()
    r = c.post("/api/templates", files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    run_tasks()
    return r.json()["template"]


def add_github_project(user_email="owner@example.com"):
    """Inserts a scanned project the way the GitHub scanner would (network-free)."""
    from sqlalchemy import select

    from app.models import User

    db = session_factory()()
    uid = db.scalar(select(User.id).where(User.email == user_email))
    p = Project(user_id=uid, name="resolve", display_name="Resolve", slug="resolve", origin="github",
                description="Issue-resolution platform with SLA tracking", repo_full_name="example/resolve",
                repo_url="https://github.com/example/resolve")
    db.add(p)
    db.flush()
    for key, label, src in (("fastapi", "FastAPI", "dependency"), ("postgresql", "PostgreSQL", "dependency"),
                            ("docker", "Docker", "file_tree"), ("pytest", "pytest", "dependency"),
                            ("python", "Python", "language")):
        db.add(ProjectEvidence(project_id=p.id, kind="technology", label=label, canonical=key, category="",
                               status="verified", source_type=src, source_detail="test fixture"))
    db.commit()
    pid = p.id
    db.close()
    return pid


def test_profile_crud(auth_client):
    c = auth_client
    r = c.put("/api/profile", json={"full_name": "Test Owner", "email": "owner@example.com", "phone": "+1 555 0100",
                                    "linkedin_url": "linkedin.com/in/test", "years_of_experience": 0.5})
    assert r.status_code == 200 and r.json()["linkedin_url"] == "https://linkedin.com/in/test"
    assert c.post("/api/profile/skills", json={"name": "Postgres", "category": "Databases"}).json()["canonical"] == \
        "postgresql"
    assert c.post("/api/profile/skills", json={"name": "PostgreSQL"}).status_code == 409
    e = c.post("/api/profile/experiences", json={"organization": "Example Labs", "title": "Intern",
                                                 "bullets": ["Built APIs"]}).json()
    assert e["bullets"] == ["Built APIs"]
    profile = c.get("/api/profile").json()
    assert "Education" in profile["completeness"]["missing"]


def test_template_upload_rejects_pdf_and_unsafe(auth_client):
    c = auth_client
    r = c.post("/api/templates", files={"file": ("resume.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
    r = c.post("/api/templates", files={"file": ("resume.pdf", b"%PDF-1.4 broken", "application/pdf")})
    assert r.status_code == 200  # accepted; the analysis then explains why it cannot be read
    run_tasks()
    task = c.get(f"/api/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "failed" and task["result"]["error"]["code"] == "pdf_unreadable"
    bad = (FIXTURES / "templates" / "jake_style.tex").read_text().replace("\\begin{document}",
                                                                         "\\immediate\\write18{id}\\begin{document}")
    r = c.post("/api/templates", files={"file": ("x.tex", bad.encode(), "text/plain")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsafe_template"
    r = c.post("/api/templates", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_size_limit(auth_client, monkeypatch):
    from app.core import config

    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1000")
    config.get_settings.cache_clear()
    r = auth_client.post("/api/templates", files={"file": ("big.tex", b"x" * 5000, "text/plain")})
    assert r.status_code == 413


def test_import_profile_from_template(auth_client):
    c = auth_client
    tpl = upload_template(c)
    r = c.post(f"/api/templates/{tpl['id']}/import-profile")
    counts = r.json()["imported"]
    assert counts["experiences"] == 1 and counts["education"] == 1 and counts["skills"] >= 8
    assert counts["projects_created"] == 2
    again = c.post(f"/api/templates/{tpl['id']}/import-profile").json()["imported"]
    assert again["experiences"] == 0 and again["skills"] == 0 and again["projects_created"] == 0
    profile = c.get("/api/profile").json()
    assert profile["personal"]["full_name"] == "Test Owner"  # filled at sign-up; import never overwrites
    assert profile["personal"]["phone"] == "+00 0000000000"  # was empty, so it was imported


@pytest.mark.latex
@pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine installed")
def test_full_resume_flow(auth_client):
    c = auth_client
    tpl = upload_template(c)
    assert c.get(f"/api/templates/{tpl['id']}/preview.pdf").status_code == 200
    c.post(f"/api/templates/{tpl['id']}/import-profile")
    add_github_project()

    r = c.post("/api/jobs", json={"description": SAMPLE_JD, "company": "Acme Tools", "role_title": "Backend Engineer"})
    assert r.status_code == 200, r.text
    job_id, task_id = r.json()["job"]["id"], r.json()["task_id"]
    run_tasks()
    task = c.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "succeeded", task
    assert all(s["state"] == "done" for s in task["steps"])

    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["deadline"] == "2026-10-15" and job["job_code"] == "REQ-20431"
    status = {r["canonical"]: r["match"]["status"] for r in job["requirements"] if r["canonical"]}
    assert status["fastapi"] == "strong" and status["aws"] == "missing"
    k8s = next(r for r in job["requirements"] if r["canonical"] == "kubernetes")["match"]
    assert k8s["status"] == "partial" and "Docker" in k8s["rationale"]  # related, not the same skill
    assert job["recommended_projects"][0]["title"] == "Resolve"
    assert job["application"]["status"] == "saved"

    r = c.post(f"/api/jobs/{job_id}/resumes", json={})
    run_tasks()
    gen = c.get(f"/api/tasks/{r.json()['task_id']}").json()
    assert gen["status"] == "succeeded", gen
    vid = gen["result"]["resume_version_id"]
    v = c.get(f"/api/resumes/{vid}").json()
    assert v["has_pdf"] and v["page_count"] == 1 and v["label"].startswith("Acme Tools — Backend Engineer")
    assert v["ats_report"]["summary"]["jd_coverage"] in ("Strong", "Good", "Fair", "Weak")
    assert c.get(f"/api/resumes/{vid}/pdf").headers["content-type"] == "application/pdf"
    assert b"\\documentclass" in c.get(f"/api/resumes/{vid}/source").content
    projects = next(s for s in v["content"]["sections"] if s["kind"] == "projects")
    assert projects["entries"][0]["fields"]["primary"] == "Resolve"

    # the original resume is never overwritten
    versions = c.get("/api/resumes").json()
    assert any(x["is_base"] for x in versions)

    # edit a draft, then approve (locks it), duplicate, compare
    content = v["content"]
    content["sections"][0]["text"] = "Backend-focused engineer."
    assert c.patch(f"/api/resumes/{vid}", json={"content": content}).status_code == 200
    assert c.post(f"/api/resumes/{vid}/approve").status_code == 200
    assert c.patch(f"/api/resumes/{vid}", json={"content": content}).json()["error"]["code"] == "version_locked"
    dup = c.post(f"/api/resumes/{vid}/duplicate").json()
    diff = c.get(f"/api/resumes/compare?a={vid}&b={dup['id']}").json()
    assert diff["differences"] == []
    app_row = c.get("/api/applications").json()[0]
    assert app_row["status"] == "ready_to_apply" and app_row["resume_version"]["id"] == vid


def test_job_import_validation(auth_client):
    c = auth_client
    assert c.post("/api/jobs", json={"description": "too short"}).json()["error"]["code"] == "description_too_short"
    assert c.post("/api/jobs", json={}).json()["error"]["code"] == "missing_input"


def test_tracker_status_changes_are_logged(auth_client):
    c = auth_client
    r = c.post("/api/jobs", json={"description": SAMPLE_JD})
    run_tasks()
    app_id = c.get(f"/api/jobs/{r.json()['job']['id']}").json()["application"]["id"]
    a = c.patch(f"/api/applications/{app_id}", json={"status": "submitted"}).json()
    assert a["status"] == "submitted" and a["date_applied"] and a["follow_up_date"]
    assert a["submission_verified"] is False  # set by hand, not confirmed on a portal
    assert any(e["kind"] == "status" for e in a["events"])
    a = c.patch(f"/api/applications/{app_id}", json={"status": "interview"}).json()
    assert a["status"] == "interview"
    assert c.patch(f"/api/applications/{app_id}", json={"status": "hired!"}).status_code == 400
    dash = c.get("/api/dashboard").json()
    assert dash["stats"]["interviews"] == 1
    analytics = c.get("/api/analytics").json()
    assert analytics["interview_conversion"] == {"interviews": 1, "submitted": 1}


def test_suggested_project_is_never_evidence_until_verified(auth_client):
    c = auth_client
    r = c.post("/api/jobs", json={"description": SAMPLE_JD})
    run_tasks()
    job_id = r.json()["job"]["id"]
    s = c.post(f"/api/jobs/{job_id}/suggested-projects", json={"gap_keys": ["kubernetes", "aws"]}).json()
    assert s["status"] == "idea" and "Kubernetes" in s["plan"]["coding_prompt"]
    assert c.get("/api/projects").json() == []  # a plan is not a project
    assert c.post(f"/api/jobs/{job_id}/suggested-projects", json={"gap_keys": ["rust"]}).status_code == 400
