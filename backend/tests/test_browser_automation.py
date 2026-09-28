"""Browser automation against the local mock career site (real Chromium via Playwright, headless)."""

import threading
import time

import pytest

from app.resume.latex_compile import resolve_engine
from tests.conftest import FIXTURES, run_tasks
from tests.support.mock_site import SUBMISSIONS, MockSite

pytestmark = pytest.mark.browser


@pytest.fixture
def site(monkeypatch):
    from app.core import config

    with MockSite() as s:
        monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        config.get_settings.cache_clear()
        yield s
    config.get_settings.cache_clear()


def test_login_and_captcha_are_handed_to_the_user(site):
    from playwright.sync_api import sync_playwright

    from app.automation.browser import detect_obstacle, inspect_form

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.goto(site.base + "/careers/login-required")
        assert "Sign in" in detect_obstacle(page)
        assert all(f.data["input_type"] != "password" for f in inspect_form(page))  # passwords are never touched
        page.goto(site.base + "/careers/captcha")
        page.wait_for_timeout(300)
        assert "CAPTCHA" in detect_obstacle(page)
        page.goto(site.base + "/careers/ibm/apply/12345")
        assert detect_obstacle(page) is None
        b.close()


def _prepare(c, site):
    c.put("/api/profile", json={
        "full_name": "Test Candidate", "first_name": "Test", "last_name": "Candidate",
        "email": "candidate@example.com", "phone": "+00 0000000000", "linkedin_url": "https://linkedin.com/in/example",
        "github_url": "https://github.com/example", "work_authorization": "Authorized to work in India",
        "requires_sponsorship": False, "years_of_experience": 0.5})
    data = (FIXTURES / "templates" / "jake_style.tex").read_bytes()
    c.post("/api/templates", files={"file": ("resume.tex", data, "text/plain")})
    run_tasks()
    r = c.post("/api/jobs", json={"url": site.base + "/careers/ibm/jobs/12345"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job"]["id"]
    run_tasks()
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["company"] == "IBM" and job["job_code"] == "12345" and job["deadline"] == "2026-11-30"
    assert job["application_url"].endswith("/careers/ibm/apply/12345")
    c.post(f"/api/jobs/{job_id}/resumes", json={})
    run_tasks()
    app_id = job["application"]["id"]
    token = c.post("/api/settings/runner-tokens", json={"name": "test"}).json()["token"]
    return app_id, token


def _start_runner(token, tmp_path, user_wait=60):
    from fastapi.testclient import TestClient

    from app.automation.runner import Api, run_loop
    from app.main import create_app

    results = []

    def target():
        with TestClient(create_app()) as http:
            api = Api("", token, http=http)
            results.extend(run_loop(api, headless=True, once=True, profile_dir=str(tmp_path / "profile"),
                                    user_wait=user_wait))

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, results


def _wait(c, app_id, pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        a = c.get(f"/api/applications/{app_id}").json()
        if pred(a):
            return a
        time.sleep(0.5)
    raise AssertionError(f"timed out; last session state: {a['session'] and a['session']['state']} "
                         f"{a['session'] and a['session']['message']}")


@pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine installed")
def test_assisted_application_submits_only_after_confirmation(auth_client, site, tmp_path):
    c = auth_client
    app_id, token = _prepare(c, site)
    r = c.post(f"/api/applications/{app_id}/automation", json={})
    assert r.status_code == 200, r.text
    thread, results = _start_runner(token, tmp_path)

    # step 2 of the form is reached and inspected
    a = _wait(c, app_id, lambda a: any(f["field_key"] == "why_company" for f in a["fields"])
              and a["session"]["state"] in ("awaiting_answers", "awaiting_review"))
    by_key = {f["field_key"]: f for f in a["fields"]}
    assert by_key["first_name"]["value"] == "Test" and by_key["first_name"]["status"] == "filled"
    assert by_key["resume"]["status"] == "filled"
    assert by_key["gender"]["sensitive"] and by_key["gender"]["value"] == ""  # never answered for the user
    assert by_key["sponsorship"]["value"] == "No" and by_key["sponsorship"]["status"] == "needs_confirmation"
    assert by_key["consent"]["status"] == "needs_confirmation"
    assert by_key["work_authorization"]["status"] == "needs_input"  # profile text matches no option exactly
    assert SUBMISSIONS == []

    # confirmation is refused while answers are pending
    r = c.post(f"/api/applications/{app_id}/automation/confirm-submit", json={"typed_confirmation": "IBM"})
    assert r.status_code == 400

    answers = {"work_authorization": ("Yes", True), "sponsorship": (None, True), "consent": (None, True),
               "why_company": ("I want to build developer platform services with Python.", True),
               "gender": ("Prefer not to say", True)}
    for key, (value, confirm) in answers.items():
        f = by_key[key]
        r = c.patch(f"/api/applications/{app_id}/fields/{f['id']}", json={"value": value, "confirm": confirm})
        assert r.status_code == 200, r.text

    a = _wait(c, app_id, lambda a: a["session"]["state"] == "awaiting_review"
              and a["session"]["filled_revision"] >= a["session"]["answers_revision"])
    time.sleep(1.0)
    assert SUBMISSIONS == []  # still nothing submitted

    r = c.post(f"/api/applications/{app_id}/automation/confirm-submit", json={"typed_confirmation": "IMB"})
    assert r.json()["error"]["code"] == "confirmation_mismatch"
    r = c.post(f"/api/applications/{app_id}/automation/confirm-submit", json={"typed_confirmation": "ibm"})
    assert r.status_code == 200, r.text

    thread.join(timeout=60)
    assert results == ["submitted"]
    a = c.get(f"/api/applications/{app_id}").json()
    assert a["status"] == "submitted" and a["submission_verified"] is True
    assert "Thank you for applying" in a["confirmation_text"]
    assert a["date_applied"] and a["follow_up_date"]

    assert len(SUBMISSIONS) == 1
    sent = SUBMISSIONS[0]
    assert sent["fields"]["first_name"] == "Test" and sent["fields"]["email"] == "candidate@example.com"
    assert sent["fields"]["sponsorship"] == "no" and sent["fields"]["authorized"] == "Yes"
    assert sent["fields"]["gender"] == "Prefer not to say"
    assert sent["fields"]["years"] == "0.5"
    assert sent["files"]["resume"]["is_pdf"] and sent["files"]["resume"]["filename"].endswith(".pdf")


@pytest.mark.skipif(resolve_engine() is None, reason="no LaTeX engine installed")
def test_cancelled_session_never_submits(auth_client, site, tmp_path):
    c = auth_client
    app_id, token = _prepare(c, site)
    c.post(f"/api/applications/{app_id}/automation", json={})
    thread, results = _start_runner(token, tmp_path)
    _wait(c, app_id, lambda a: a["session"]["state"] in ("awaiting_answers", "awaiting_review")
          and any(f["field_key"] == "resume" for f in a["fields"]))
    c.post(f"/api/applications/{app_id}/automation/cancel")
    thread.join(timeout=30)
    assert results == ["cancelled"]
    assert SUBMISSIONS == []
    assert c.get(f"/api/applications/{app_id}").json()["status"] == "in_progress"


def test_runner_cannot_report_submission_without_confirmation(auth_client, client):
    from app.core.db import session_factory
    from app.models import Application, AutomationSession, Job, User

    db = session_factory()()
    user = db.query(User).first()
    job = Job(user_id=user.id, company="X", role_title="Y", job_url="https://x.example/j", analysis_state="ready")
    db.add(job)
    db.flush()
    a = Application(user_id=user.id, job_id=job.id, status="in_progress")
    db.add(a)
    db.flush()
    s = AutomationSession(user_id=user.id, application_id=a.id, start_url=job.job_url, state="awaiting_review")
    db.add(s)
    db.commit()
    token = auth_client.post("/api/settings/runner-tokens", json={"name": "t"}).json()
    s.runner_token_id = token["id"]
    db.commit()
    sid = s.id
    db.close()
    headers = {"Authorization": f"Bearer {token['token']}"}
    r = client.post(f"/api/runner/sessions/{sid}/report", json={"state": "submitted"}, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "submit_not_confirmed"
    assert auth_client.get(f"/api/applications/{a.id}").json()["status"] == "in_progress"


def test_javascript_career_pages_are_rendered_in_a_sandbox(site):
    from app.jobs.platforms import fetch_posting

    post = fetch_posting(site.base + "/careers/spa-job/77")
    assert post.title == "Backend Developer" and post.company == "Example Systems"
    assert "FastAPI" in post.description_text
    assert any("JavaScript" in n for n in post.notes)


def test_rendered_pages_cannot_reach_private_addresses(site):
    from app.jobs.render import render_page

    html, _ = render_page(site.base + "/careers/spa-job/77")
    assert "<title>LEAKED</title>" not in html  # the metadata request was aborted by the request guard
    assert "<title>Careers (mock SPA)</title>" in html
