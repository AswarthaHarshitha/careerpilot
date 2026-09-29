"""End-to-end check of the application agent with a real CareerPilot Runner process.

    cd backend && uv run python ../e2e/run_agent_e2e.py

Starts the API and a local mock career site, then runs the CareerPilot Runner as a separate process (headless
Chromium) and drives a three-page application:

    runner connects -> web app shows it connected -> application starts -> page load fails once and is retried
    -> form mapped and filled -> the resume upload is rejected once and re-attached -> page 1 continued
    -> CAPTCHA: the agent pauses and waits for the user -> the runner is killed: the web app shows it offline
    -> the runner restarts, reconnects and resumes safely -> the "user" clears the CAPTCHA and clicks Continue
    -> page 2 re-renders (stale elements) and raises an alert: recovered -> questions only the user can answer
    -> ready to submit -> explicit confirmation -> submit -> confirmation page detected -> application ID recorded
    -> tracker shows the application as submitted and verified; exactly one submission reached the site

Exits non-zero on the first failed check.
"""

import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from tests.support.mock_site import AGENT, MockSite  # noqa: E402

TEMPLATE = BACKEND / "tests" / "fixtures" / "templates" / "jake_style.tex"
PASSWORD = "E2e-" + secrets.token_urlsafe(12) + "-9a"
JD = """Backend Engineer - Acme Robotics
Location: Bengaluru, India

What you will do
- Build REST APIs in Python with FastAPI
- Model data in PostgreSQL and write tests with pytest

Requirements
- Python, SQL and Git
- Experience with Docker is a plus
"""
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


def until(fn, pred, timeout: float, what: str):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if pred(last):
            return last
        time.sleep(0.5)
    if isinstance(last, dict) and last.get("session"):
        ses = last["session"]
        detail = (f"state={ses['state']} phase={ses['phase']} message={ses['message']!r}\n    log: "
                  + "\n         ".join(f"{e['at'][11:]} {e['message']}" for e in ses["log"][-14:])
                  + "\n    fields: " + ", ".join(f"{f['field_key'] or f['label'][:20]}={f['status']}" for f in last["fields"]))
    else:
        detail = str(last)[:400]
    fail(f"timed out waiting for {what}; {detail}")


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="careerpilot-agent-e2e-")
    port = free_port()
    with MockSite() as site:
        env = {
            **os.environ,
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{tmp}/agent.db",
            "SECRET_KEY": secrets.token_urlsafe(40),
            "WORKER_ENABLED": "true",
            "FETCH_ALLOW_PRIVATE_HOSTS": "127.0.0.1",
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "OLLAMA_BASE_URL": "",
            "GITHUB_TOKEN": "",
            "ALLOW_REGISTRATION": "false",
            "CAREERPILOT_HOME": f"{tmp}/runner-home",
        }
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                       capture_output=True)
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level",
                                "warning"], cwd=BACKEND, env=env)
        runners: list[subprocess.Popen] = []
        try:
            run_flow(f"http://127.0.0.1:{port}", site, env, runners)
        finally:
            for r in runners:
                if r.poll() is None:
                    r.terminate()
                    r.wait(timeout=10)
            api.terminate()
            api.wait(timeout=10)
    print(f"\nAll {len(checks)} checks passed.")


def start_runner(base: str, token: str, env: dict, runners: list) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, "-m", "app.automation.runner", "--api", base, "--token", token, "--headless"],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    runners.append(p)
    return p


def run_flow(base: str, site: MockSite, env: dict, runners: list) -> None:
    c = httpx.Client(base_url=base, timeout=120)
    until(lambda: _health(c), lambda h: h, 60, "API")
    r = c.post("/api/auth/register", json={"email": "agent@example.com", "password": PASSWORD, "display_name": "A"})
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    c.post("/api/settings/onboarding/complete")

    # --- prerequisites: profile, template, job, resume ---------------------------------------------------
    r = c.post("/api/templates", files={"file": ("resume.tex", TEMPLATE.read_bytes(), "text/plain")})
    until(lambda: c.get(f"/api/tasks/{r.json()['task_id']}").json(), lambda t: t["status"] == "succeeded", 240,
          "template")
    c.post(f"/api/templates/{r.json()['template']['id']}/import-profile")
    c.put("/api/profile", json={**c.get("/api/profile").json()["personal"], "full_name": "Test Candidate",
                                "first_name": "Test", "last_name": "Candidate", "email": "candidate@example.com",
                                "phone": "+91 90000 00000", "linkedin_url": "https://linkedin.com/in/test-candidate",
                                "work_authorization": "Authorized to work in India", "requires_sponsorship": False,
                                "years_of_experience": 0.5})
    r = c.post("/api/jobs", json={"description": JD, "company": "Acme Robotics", "role_title": "Backend Engineer",
                                  "application_url": f"{site.base}/careers/acme/apply/900"})
    job_id = r.json()["job"]["id"]
    until(lambda: c.get(f"/api/tasks/{r.json()['task_id']}").json(), lambda t: t["status"] == "succeeded", 120, "job")
    t = c.post(f"/api/jobs/{job_id}/resumes", json={}).json()
    t = until(lambda: c.get(f"/api/tasks/{t['task_id']}").json(), lambda t: t["status"] in ("succeeded", "failed"),
              240, "resume")
    expect(t["status"] == "succeeded", "tailored resume generated for the job")
    app_id = c.get(f"/api/jobs/{job_id}").json()["application"]["id"]

    # --- 1-3. runner connects and the web app sees it -----------------------------------------------------
    status = c.get("/api/agent/status").json()
    expect(not status["runner"]["connected"] and status["runner"]["state"] == "offline",
           "before the runner starts, the web app shows it offline")
    token = c.post("/api/settings/runner-tokens", json={"name": "e2e-laptop"}).json()["token"]
    runner = start_runner(base, token, env, runners)
    status = until(lambda: c.get("/api/agent/status").json(), lambda s: s["runner"]["connected"], 60,
                   "runner connection")
    expect(status["runner"]["state"] == "idle", "1-3. runner started; web app shows it CONNECTED and ready")

    # --- 4-10. start, open (with a failed first load), inspect, map, fill, upload --------------------------
    expect(c.post(f"/api/applications/{app_id}/automation", json={}).status_code == 200, "4. application started")
    a = until(lambda: c.get(f"/api/applications/{app_id}").json(),
              lambda a: a["session"]["phase"] == "WAITING_FOR_USER", 120, "the CAPTCHA pause")
    log = " | ".join(e["message"] for e in a["session"]["log"])
    expect("Opening the application page" in log and AGENT["loads"] >= 2,
           "5-6. browser launched; the failed first page load (HTTP 503) was retried")
    expect("Application form detected" in log and "fields identified on page 1" in log, "7-8. form inspected and "
                                                                                          "fields mapped")
    by_key = {f["field_key"]: f for f in a["fields"]}
    expect(by_key["first_name"]["value"] == "Test" and by_key["email"]["provenance"] == "CAREER_PROFILE",
           "9. fields filled from the career profile, with provenance")
    expect(a["session"]["resume_uploaded"] and "Resume attached and recognised by the form" in log,
           "10. resume uploaded (the form rejected the first attachment; it was re-attached and verified)")
    expect("Page 1 completed" in log, "11. page 1 completed and continued")
    expect("CAPTCHA" in a["session"]["message"] or "human" in a["session"]["message"].lower(),
           "12. paused for a user-required action (CAPTCHA) with the reason shown")
    expect(AGENT["submissions"] == [], "nothing submitted so far")

    # --- runner failure: offline, then reconnect and resume safely -----------------------------------------
    runner.send_signal(signal.SIGKILL)
    runner.wait(timeout=10)
    s = until(lambda: c.get("/api/agent/status").json(), lambda s: not s["runner"]["connected"], 30, "offline status")
    a = c.get(f"/api/applications/{app_id}").json()
    expect(s["runner"]["state"] == "offline" and a["session"]["phase"] == "RUNNER_OFFLINE",
           "runner killed: the web app shows Runner Offline (not 'waiting')")
    runner = start_runner(base, token, env, runners)
    until(lambda: c.get("/api/agent/status").json(), lambda s: s["runner"]["connected"], 60, "reconnection")
    a = until(lambda: c.get(f"/api/applications/{app_id}").json(),
              lambda a: "reconnected" in " ".join(e["message"] for e in a["session"]["log"]).lower()
              and a["session"]["phase"] == "WAITING_FOR_USER", 150, "resume after reconnect")
    expect(True, "runner restarted, reconnected and resumed the task where it needed the user again")

    # --- 13. the user clears the CAPTCHA and continues -----------------------------------------------------
    AGENT["captcha_solved"] = True
    expect(c.post(f"/api/applications/{app_id}/automation/continue").status_code == 200,
           "13. user completed the CAPTCHA and clicked Continue application")
    answers = {"why_company": "I want to build reliable backend services for robotics.", "work_authorization": "Yes"}
    asked: set[str] = set()
    end = time.time() + 240
    while time.time() < end:
        a = c.get(f"/api/applications/{app_id}").json()
        if a["session"]["phase"] == "READY_TO_SUBMIT" and any(f["field_key"] == "why_company" for f in a["fields"]):
            break
        pending = [f for f in a["fields"] if f["status"] in ("needs_input", "needs_confirmation")
                   and (f["required"] or f["field_key"] == "consent") and f["id"] not in asked]
        if a["session"]["phase"] == "WAITING_FOR_USER" and pending:
            for f in pending:
                asked.add(f["id"])
                r = c.patch(f"/api/applications/{app_id}/fields/{f['id']}",
                            json={"value": answers.get(f["field_key"]), "confirm": True,
                                  "remember": f["field_key"] == "work_authorization"})
                expect(r.status_code == 200, f"user answered on page {f['step'] + 1}: {f['label'][:50]}")
        time.sleep(0.5)
    log = " | ".join(e["message"] for e in a["session"]["log"])
    by_key = {f["field_key"]: f for f in a["fields"]}
    if os.environ.get("E2E_DEBUG"):
        print("\n".join(f"    {e['at'][11:19]} [{e.get('phase', '')}] {e['message']}" for e in a["session"]["log"]))
        print("    fields:", [(f["field_key"], f["step"], f["status"], f["value"][:30]) for f in a["fields"]])
    expect(by_key["linkedin"]["value"].startswith("https://linkedin.com") and "Page 2 completed" in log,
           "page 2 filled after it re-rendered its inputs and raised an alert (stale elements recovered)")
    expect("Page 3 opened" in log, "11. continued across multiple pages (page 3 opened after a slow load)")
    judged = {k for k, f in by_key.items() if f["value_source"] == "user" and f["status"] == "confirmed"}
    expect({"why_company", "work_authorization"} <= judged and by_key["consent"]["status"] == "confirmed",
           "questions needing judgment (authorization, motivation, consent) were answered by the user, not guessed")
    expect(len({f["label"] for f in a["fields"]}) == len(a["fields"]), "no duplicate fields after the restart")

    # --- 14-15. review and explicit confirmation -----------------------------------------------------------
    a = until(lambda: c.get(f"/api/applications/{app_id}").json(),
              lambda a: a["session"]["phase"] == "READY_TO_SUBMIT"
              and a["session"]["filled_revision"] >= a["session"]["answers_revision"], 90, "ready to submit")
    expect(a["session"]["fields_done"] == a["session"]["fields_total"] and a["session"]["resume_uploaded"],
           f"14. review page reached: {a['session']['fields_done']}/{a['session']['fields_total']} fields, resume "
           "uploaded")
    time.sleep(2)
    expect(AGENT["submissions"] == [], "15. nothing is submitted without explicit confirmation")
    r = c.post(f"/api/applications/{app_id}/automation/confirm-submit", json={"typed_confirmation": "Acme Robotics"})
    expect(r.status_code == 200, "15. user explicitly confirmed submission")

    # --- 16-20. submit, verify, record -----------------------------------------------------------------------
    a = until(lambda: c.get(f"/api/applications/{app_id}").json(),
              lambda a: a["session"]["state"] in ("submitted", "submitted_unverified", "failed"), 90, "submission")
    expect(a["session"]["state"] == "submitted", "16-18. submitted; the confirmation page was detected")
    expect(a["confirmation_id"] == "ACME-2026-4821", f"19. application ID recorded ({a['confirmation_id']})")
    expect(a["status"] == "submitted" and a["submission_verified"], "20. tracker shows Submitted (verified)")
    expect(len(AGENT["submissions"]) == 1, "exactly one submission reached the employer site")
    sent = AGENT["submissions"][0]
    expect(sent["fields"]["first_name"] == "Test" and sent["fields"]["authorized"] == "Yes"
           and sent["files"]["resume"]["is_pdf"], "submitted values match the profile, the user's answers and the "
                                                    "tailored PDF")
    saved = {x["question_key"]: x["answer"] for x in c.get("/api/profile").json()["answers"]}
    expect(saved.get("work_authorization") == "Yes" and "why_company" not in saved,
           "only the answer the user asked to remember was saved for future applications")
    s = until(lambda: c.get("/api/agent/status").json(), lambda s: s["runner"]["state"] == "idle", 30, "runner idle")
    expect(s["task"] is None, "runner is back to Ready with no task in progress")


def _health(c: httpx.Client) -> bool:
    try:
        return c.get("/api/health").status_code == 200
    except httpx.HTTPError:
        return False


if __name__ == "__main__":
    main()
