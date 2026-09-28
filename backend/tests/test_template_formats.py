"""Template formats: PDF (analysed and rebuilt), Markdown, Overleaf project archives, and the standard layout."""

import io
import zipfile

import pytest

from app.resume.latex_compile import compile_latex, resolve_engine
from tests.conftest import FIXTURES, SAMPLE_JD, run_tasks

pytestmark = [pytest.mark.latex, pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine")]
TEX = (FIXTURES / "templates" / "jake_style.tex").read_text()


@pytest.fixture(scope="module")
def fixture_pdf() -> bytes:
    res = compile_latex(TEX)
    assert res.pdf
    return res.pdf


def test_pdf_template_is_analysed_rebuilt_and_used(auth_client, fixture_pdf):
    c = auth_client
    r = c.post("/api/templates", files={"file": ("My_Resume.pdf", fixture_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    run_tasks()
    task = c.get(f"/api/tasks/{r.json()['task_id']}").json()
    assert task["status"] == "succeeded", task
    tpl = c.get(f"/api/templates/{r.json()['template']['id']}").json()
    assert [s["kind"] for s in tpl["sections"]] == ["summary", "experience", "projects", "skills", "education",
                                                     "achievements"]
    fp = tpl["fingerprint"]
    assert fp["layout"] == "One-column" and fp["text"] == "Selectable" and fp["pages"] == 1
    cmp = tpl["comparison"]
    assert cmp["pages_rebuilt"] == cmp["pages_original"] == 1
    assert cmp["headings_found"] == cmp["headings_total"] == 6
    assert cmp["text_preserved"] > 90 and cmp["mean_heading_offset_pt"] < 12
    assert c.get(f"/api/templates/{tpl['id']}/original.pdf").content.startswith(b"%PDF")
    assert c.get(f"/api/templates/{tpl['id']}/preview.pdf").content.startswith(b"%PDF")

    # the rebuilt template drives resume generation like any other
    c.post(f"/api/templates/{tpl['id']}/import-profile")
    r = c.post("/api/jobs", json={"description": SAMPLE_JD, "company": "Acme", "role_title": "Backend Engineer"})
    run_tasks()
    job_id = r.json()["job"]["id"]
    t = c.post(f"/api/jobs/{job_id}/resumes", json={"template_id": tpl["id"]}).json()
    run_tasks()
    task = c.get(f"/api/tasks/{t['task_id']}").json()
    assert task["status"] == "succeeded", task
    v = c.get(f"/api/resumes/{task['result']['resume_version_id']}").json()
    assert v["has_pdf"] and v["page_count"] == 1
    checks = {x["id"]: x["status"] for x in v["quality_report"]["checks"]}
    assert checks["ats_extraction"] == "pass" and checks["layout"] == "pass" and checks["template"] == "pass"
    pdf = c.get(f"/api/resumes/{v['id']}/pdf?download=true")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "Resume_Acme_Backend_Engineer.pdf" in pdf.headers["content-disposition"]


def test_markdown_template(auth_client):
    md = """# Test Candidate
candidate@example.com | +00 0000000000 | https://github.com/example

## Summary
Backend engineer building APIs with Python.

## Experience
**Example Labs** | Jan 2026 - Mar 2026
Software Engineering Intern | Remote
- Built REST API endpoints with FastAPI and PostgreSQL.
- Technologies: Python, FastAPI, PostgreSQL

## Projects
**Taskboard** https://github.com/example/taskboard
- Developed a task tracker with React and FastAPI.

## Skills
Languages: Python, SQL
Frameworks: FastAPI, React

## Education
**Example University** | 2022 - 2026
B.Tech in Computer Science
"""
    c = auth_client
    r = c.post("/api/templates", files={"file": ("resume.md", md.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    run_tasks()
    tpl = c.get(f"/api/templates/{r.json()['template']['id']}").json()
    assert tpl["page_count"] == 1 and not tpl["render_error"]
    kinds = [s["kind"] for s in tpl["sections"]]
    assert kinds[:4] == ["summary", "experience", "projects", "skills"]


def test_overleaf_zip_inlines_inputs(auth_client):
    main = TEX.replace("\\section{Summary}\nSoftware engineering graduate who builds backend services and full-stack "
                       "web applications with Python, Java and SQL.", "\\input{sections/summary}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", main)
        z.writestr("sections/summary.tex", "\\section{Summary}\nBuilds backend services with Python.")
    c = auth_client
    r = c.post("/api/templates", files={"file": ("project.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    run_tasks()
    tpl = c.get(f"/api/templates/{r.json()['template']['id']}").json()
    assert tpl["sections"][0]["kind"] == "summary" and tpl["page_count"] == 1


def test_standard_layout_from_profile(auth_client):
    c = auth_client
    c.put("/api/profile", json={"full_name": "Test Candidate", "email": "candidate@example.com",
                                "github_url": "https://github.com/example", "summary": "Builds backend services."})
    c.post("/api/profile/experiences", json={"organization": "Example Labs", "title": "Intern",
                                             "start_date": "Jan 2026", "end_date": "Mar 2026",
                                             "bullets": ["Built REST APIs with FastAPI."]})
    c.post("/api/profile/skills", json={"name": "Python", "category": "Programming Languages"})
    r = c.post("/api/templates/from-profile")
    assert r.status_code == 200, r.text
    run_tasks()
    tpl = c.get(f"/api/templates/{r.json()['template']['id']}").json()
    assert not tpl["render_error"] and tpl["page_count"] == 1
    assert [s["kind"] for s in tpl["sections"]][:3] == ["summary", "experience", "skills"]
