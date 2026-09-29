"""Command palette agent: natural language -> tool (with permissions) -> result."""

import pytest

from app.resume.latex_compile import resolve_engine
from tests.conftest import SAMPLE_JD, run_tasks
from tests.test_api_flow import upload_template


def say(c, text, **context):
    r = c.post("/api/agent/command", json={"text": text, "context": context})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def job(auth_client):
    c = auth_client
    r = c.post(
        "/api/jobs",
        json={
            "description": SAMPLE_JD,
            "company": "Acme",
            "role_title": "Backend Engineer",
            "application_url": "https://93.184.215.14/apply/1",
        },
    )
    run_tasks()
    return c, r.json()["job"]["id"]


def test_read_tools_answer_from_current_state(job):
    c, job_id = job
    r = say(c, "Show skill gaps", job_id=job_id)
    assert r["tool"] == "get_skill_gaps" and r["permission"] == "READ" and "Docker" in r["report"]
    r = say(c, "What is stopping the application?", job_id=job_id)
    assert r["tool"] == "application_blockers"
    assert "no resume" in r["report"] and "Runner is offline" in r["report"]
    # without a page context the most recent job is used, and the agent says so
    r = say(c, "show skill gaps")
    assert r["report"].startswith("Using your most recent job: Backend Engineer at Acme")


def test_notes_and_ideas_are_kept_separate(job):
    c, job_id = job
    r = say(c, "Add a note: recruiter call on Friday", job_id=job_id)
    assert r["tool"] == "add_job_note" and r["permission"] == "WRITE"
    assert "recruiter call on Friday" in c.get(f"/api/jobs/{job_id}").json()["notes"]
    r = say(c, "Add career idea: build a job-queue service in Go")
    assert r["tool"] == "add_career_idea"
    assert "recruiter" not in say(c, "Add career idea: learn Kubernetes")["report"]


def test_high_impact_actions_are_never_executed(job):
    c, job_id = job
    r = say(c, "submit the application now", job_id=job_id)
    assert r["tool"] == "submit_application" and r["permission"] == "HIGH_IMPACT" and not r["ok"]
    assert "Nothing was submitted" in r["report"] and r["navigate"].startswith("/applications/")


def test_apply_without_a_resume_explains_what_to_do(job):
    c, job_id = job
    r = say(c, "Apply for this job", job_id=job_id)
    assert r["tool"] == "start_application" and r["permission"] == "EXTERNAL" and not r["ok"]
    assert "Generate one first" in r["report"]


def test_scan_github_needs_a_connected_account(job):
    c, _ = job
    r = say(c, "rescan my github")
    assert r["tool"] == "scan_github" and not r["ok"] and "Connect your GitHub" in r["report"]


def test_model_picks_a_tool_for_unusual_phrasing(job):
    from app.llm import provider
    from tests.conftest import FakeLLM

    c, job_id = job
    provider.set_test_provider(FakeLLM([{"tool": "get_skill_gaps"}]))
    r = say(c, "where am I weakest for this one?", job_id=job_id)
    assert r["tool"] == "get_skill_gaps"
    provider.set_test_provider(FakeLLM([{"tool": "rm_rf", "question": "Did you mean the resume or the job?"}]))
    r = say(c, "blorp", job_id=job_id)
    assert r["tool"] is None and r["report"] == "Did you mean the resume or the job?"


@pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine")
def test_resume_commands_edit_the_current_resume(job):
    c, job_id = job
    upload_template(c)
    r = say(c, "Generate tailored resume", job_id=job_id)
    assert r["tool"] == "generate_resume" and r["navigate"].startswith(f"/jobs/{job_id}")
    run_tasks()
    r = say(c, "make the summary one line", job_id=job_id)
    assert r["tool"] == "edit_resume" and "summary" in r["report"].lower(), r
    vid = int(r["navigate"].rsplit("/", 1)[-1])
    r = say(c, "undo last resume change", resume_id=vid)
    assert r["tool"] == "undo_resume_change" and "make the summary one line" in r["report"]
    r = say(c, "run ATS check", resume_id=vid)
    assert r["tool"] == "run_ats_check" and "ATS extraction" in r["report"]
    r = say(c, "open resume", job_id=job_id)
    assert r["tool"] == "navigate" and r["navigate"] == f"/resumes/{vid}"
