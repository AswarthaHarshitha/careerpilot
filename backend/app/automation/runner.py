"""CareerPilot Runner - the local application agent.

Runs on your computer and drives a visible Chromium window. It connects to CareerPilot, picks up applications
you start there, opens the employer's page, maps and fills the form from your profile, uploads the tailored
resume, continues through multi-page forms, and hands control to you whenever a person is needed (sign-in,
CAPTCHA, questions only you can answer). It clicks the employer's final submit button only after you confirm
in CareerPilot, and it never clicks it twice.

    careerpilot-runner install --api https://your-careerpilot.example --token cpr_...   # once; starts at login
    careerpilot-runner                                                                 # or run in a terminal

Security: the long-lived runner token is exchanged for a 15-minute access token; every task payload is
signed with a key derived from the runner token and is verified before anything is done. The browser profile
(your logins on employer sites) stays in ~/.careerpilot/browser-profile and never leaves your machine.
"""

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from app.core.security import canonical_json

log = logging.getLogger("careerpilot.runner")
VERSION = "1.1"
USER_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 2.0
IDLE_POLL_SECONDS = 3.0
MAX_PAGE_ERRORS = 3
HOME = Path(os.environ.get("CAREERPILOT_HOME", Path.home() / ".careerpilot"))


class SessionEnded(Exception):
    """The session was cancelled or finished on the server while the agent was working."""


class UntrustedInstructions(Exception):
    """A task payload failed signature verification; nothing is done with it."""


class Api:
    """CareerPilot API client: short-lived access tokens, automatic refresh, signature checks, retries."""

    def __init__(self, base: str, token: str, http: httpx.Client | None = None):
        self.base = base.rstrip("/")
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)
        self.token = token
        self.key = bytes.fromhex(hashlib.sha256(token.encode()).hexdigest())
        self.access = ""
        self.access_expires = 0.0

    # -- transport ------------------------------------------------------------------------------------

    def connect(self) -> dict:
        r = self.http.post(
            f"{self.base}/api/runner/connect",
            json={"host": socket.gethostname()[:120], "version": VERSION},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        data = self._check(r)
        self.access = data["access_token"]
        self.access_expires = time.time() + 12 * 60  # refresh before the 15-minute expiry
        return data

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(4):
            if not self.access or time.time() > self.access_expires:
                self.connect()
            try:
                r = self.http.request(
                    method, f"{self.base}{path}", headers={"Authorization": f"Bearer {self.access}"}, **kw
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
                continue
            if r.status_code == 401:
                self.access = ""
                continue
            if r.status_code >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            return r
        return r

    def _check(self, r: httpx.Response) -> dict:
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            if err.get("code") == "session_ended":
                raise SessionEnded(err.get("message", ""))
            raise RuntimeError(f"API {r.status_code}: {err.get('message') or r.text[:200]}")
        return r.json()

    def _verified(self, data: dict) -> dict:
        sig = data.get("signature", "")
        payload = {k: v for k, v in data.items() if k != "signature"}
        expected = hmac.new(self.key, canonical_json(payload), hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            raise UntrustedInstructions("Task payload signature did not verify; refusing to act on it.")
        return data

    # -- calls ------------------------------------------------------------------------------------------

    def ping(self) -> dict:
        return self._check(self._request("GET", "/api/runner/ping"))

    def heartbeat(self, state: str, detail: str = "") -> None:
        try:
            self._request("POST", "/api/runner/heartbeat", json={"state": state, "detail": detail[:300]})
        except Exception:  # noqa: BLE001 - presence is best effort
            pass

    def claim(self) -> dict | None:
        s = self._check(self._request("POST", "/api/runner/claim")).get("session")
        return self._verified(s) if s else None

    def instructions(self, sid: int) -> dict:
        return self._verified(self._check(self._request("GET", f"/api/runner/sessions/{sid}")))

    def post_fields(self, sid: int, step: int, fields: list[dict]) -> dict:
        r = self._request("POST", f"/api/runner/sessions/{sid}/fields", json={"step": step, "fields": fields})
        return self._verified(self._check(r))

    def event(self, sid: int, phase: str, message: str) -> None:
        try:
            self._request("POST", f"/api/runner/sessions/{sid}/events", json={"phase": phase, "message": message})
        except Exception:  # noqa: BLE001 - the timeline is best effort
            pass

    def report(self, sid: int, **body) -> dict:
        return self._verified(self._check(self._request("POST", f"/api/runner/sessions/{sid}/report", json=body)))

    def download_resume(self, sid: int, dest_dir: str) -> tuple[str, str]:
        r = self._request("GET", f"/api/runner/sessions/{sid}/resume.pdf")
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            raise RuntimeError("Could not download the resume PDF.")
        m = re.search(r'filename="([^"]+)"', r.headers.get("content-disposition", ""))
        name = re.sub(r"[^A-Za-z0-9._-]", "_", m.group(1) if m else "Resume.pdf")
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as fh:
            fh.write(r.content)
        return path, r.headers.get("x-resume-version", "")


def _screenshot(page) -> str | None:
    try:
        return base64.b64encode(page.screenshot(type="png", full_page=False)).decode()
    except Exception:  # noqa: BLE001
        return None


class SessionRunner:
    def __init__(self, api: Api, page, session: dict, workdir: str, user_wait: float = USER_WAIT_SECONDS):
        from app.automation import adapters
        from app.automation import browser as B

        self.B = B
        self.api = api
        self.page = page
        self.sid = session["session_id"]
        self.start_url = session["start_url"]
        self.adapter = adapters.adapter_for(self.start_url)
        self.adapters = adapters
        self.workdir = workdir
        self.user_wait = user_wait
        self.step = 0
        self.found: dict = {}
        self.resume_path: str | None = None
        self.filled_revision = -1
        self.resume_nonce = session.get("resume_nonce", 0)
        self.page_errors = 0

    # -- reporting --------------------------------------------------------------------------------------

    def event(self, phase: str, message: str) -> None:
        log.info("[%s] %s", phase, message)
        self.api.event(self.sid, phase, message)

    def report(self, state: str, message: str = "", **extra) -> dict:
        return self.api.report(
            self.sid,
            state=state,
            message=message,
            current_url=self.page.url,
            step=self.step,
            screenshot_b64=_screenshot(self.page),
            platform=self.adapter.name,
            **extra,
        )

    def wait_for_user(self, reason: str) -> None:
        self.event("WAITING_FOR_USER", reason)
        self.report("action_required", reason, phase="WAITING_FOR_USER")
        self.api.heartbeat("waiting", reason)

    # -- main flow -------------------------------------------------------------------------------------

    def run(self) -> str:
        B = self.B
        self.event("OPENING_PAGE", f"Opening the application page ({self.adapter.label})")
        ok, reason = B.goto(self.page, self.start_url)
        if not ok:
            self.report(
                "failed", f"Could not open the application page: {reason}. Nothing was submitted.", phase="FAILED"
            )
            return "failed"
        if self.adapter.note and self.adapter.support == "signin":
            self.event("INSPECTING_PAGE", self.adapter.note)
        self.event("INSPECTING_PAGE", "Looking for the application form")
        if not self._reach_form():
            return self.api.instructions(self.sid)["state"]
        self.resume_path, version = self.api.download_resume(self.sid, self.workdir)
        self.event(
            "INSPECTING_PAGE",
            f"Resume ready: {os.path.basename(self.resume_path)}" + (f" (version {version})" if version else ""),
        )
        ins = self._inspect_and_post()
        self._fill(ins)
        while True:
            try:
                result = self._tick()
            except (SessionEnded, UntrustedInstructions):
                raise
            except Exception as exc:  # noqa: BLE001 - page-level failures are recovered, not fatal
                if not self._recover(exc):
                    return "action_required"
                continue
            if result:
                return result

    def _tick(self) -> str | None:
        B = self.B
        page = self.page
        ins = self.api.instructions(self.sid)
        if ins["cancelled"] or ins["terminal"]:
            return ins["state"]
        if ins["submit"]:
            return self._submit()
        if ins["resume_nonce"] != self.resume_nonce:
            self.resume_nonce = ins["resume_nonce"]
            self.event("RESUMING", "Looking at the page again")
            obstacle = B.detect_obstacle(page)
            if obstacle:
                self.wait_for_user("Still waiting: " + obstacle)
                return None
            ins = self._inspect_and_post()
            self._fill(ins)
            return None
        if ins["answers_revision"] > self.filled_revision:
            self._fill(ins)
            return None
        if not ins["unresolved"] and ins["state"] in ("awaiting_review", "filling"):
            nxt = B.find_button(page, B.NEXT_TEXT)
            sub = B.find_button(page, B.SUBMIT_TEXT, prefer_submit_type=True)
            if nxt and not sub:
                self._next_step(nxt)
                return None
        obstacle = B.detect_obstacle(page)
        if obstacle and ins["state"] != "action_required":
            self.wait_for_user(obstacle)
        elif not obstacle and ins["state"] == "action_required":
            self.event("RESUMING", "The browser is ready again; continuing")
            ins = self._inspect_and_post()
            self._fill(ins)
            return None
        self.page_errors = 0
        time.sleep(POLL_SECONDS)
        return None

    def _recover(self, exc: Exception) -> bool:
        """Timeouts, detached elements, navigation errors: re-inspect and carry on, or hand over to the user."""
        self.page_errors += 1
        kind = type(exc).__name__
        log.warning("Page error %s (%s/%s): %s", kind, self.page_errors, MAX_PAGE_ERRORS, exc)
        if self.page_errors >= MAX_PAGE_ERRORS:
            self.wait_for_user(
                f"The page is not responding as expected ({kind}). The browser is open; fix what's on screen, then "
                "click Continue application."
            )
            self.page_errors = 0
            return True
        self.event("INSPECTING_PAGE", f"Recovering from a page error ({kind}); inspecting the page again")
        time.sleep(POLL_SECONDS)
        try:
            ins = self._inspect_and_post()
            self._fill(ins)
        except (SessionEnded, UntrustedInstructions):
            raise
        except Exception:  # noqa: BLE001
            pass
        return True

    def _reach_form(self) -> bool:
        B = self.B
        deadline = time.time() + self.user_wait
        apply_clicks = 0
        empty_polls = 0
        announced = None
        while time.time() < deadline:
            ins = self.api.instructions(self.sid)
            if ins["cancelled"] or ins["terminal"]:
                return False
            if ins["resume_nonce"] != self.resume_nonce:
                self.resume_nonce = ins["resume_nonce"]
                announced = None
                empty_polls = 0
            obstacle = B.detect_obstacle(self.page)
            if obstacle:
                if announced != obstacle:
                    self.wait_for_user(obstacle)
                    announced = obstacle
                time.sleep(POLL_SECONDS)
                continue
            fields = [
                f for f in B.inspect_form(self.page) if f.data["input_type"] != "checkbox" or f.data.get("option_refs")
            ]
            meaningful = [
                f
                for f in fields
                if f.data["input_type"]
                in ("text", "email", "tel", "file", "textarea", "select", "radio", "combobox", "url")
            ]
            if len(meaningful) >= 2:
                self.event("INSPECTING_PAGE", "Application form detected")
                return True
            if apply_clicks < 3 and B.click_apply(self.page, self.adapter.apply_text):
                apply_clicks += 1
                self.event("OPENING_PAGE", "Opened the application form")
                continue
            empty_polls += 1
            if announced is None and empty_polls >= 6:
                if self.adapter.support == "signin":
                    self.wait_for_user(self.adapter.note)
                    announced = self.adapter.note
                    continue
                self.report(
                    "unsupported",
                    "No application form was found on this page. The site may use a portal CareerPilot cannot "
                    "read. Open the job and apply manually.",
                    phase="BLOCKED",
                )
                return False
            time.sleep(POLL_SECONDS)
        self.report("failed", "Stopped waiting: the page never reached an application form.", phase="FAILED")
        return False

    def _inspect_and_post(self) -> dict:
        found = self.B.inspect_form(self.page)
        old_labels = {f.data["label"] for f in self.found.values()}
        new_labels = {f.data["label"] for f in found}
        if old_labels and new_labels and not (old_labels & new_labels):
            # a completely different set of fields: the form moved to its next page while we were recovering
            self.event("FILLING_FORM", f"Page {self.step + 1} completed")
            self.step += 1
            self.event("INSPECTING_PAGE", f"Page {self.step + 1} opened")
        self.event("MAPPING_FORM", f"Reading the fields on page {self.step + 1}")
        self.found = {f.ref: f for f in found}
        payload = [
            {
                "field_ref": f.ref,
                "label": f.data["label"],
                "input_type": f.data["input_type"],
                "required": f.data.get("required", False),
                "options": f.data.get("options", []),
            }
            for f in found
        ]
        return self.api.post_fields(self.sid, self.step, payload)

    def _remap(self, ref: str) -> str | None:
        """After the page re-rendered, find the same field again by label and type."""
        old = self.found.get(ref)
        if old is None:
            return None
        fresh = {f.ref: f for f in self.B.inspect_form(self.page)}
        for r, f in fresh.items():
            if f.data["label"] == old.data["label"] and f.data["input_type"] == old.data["input_type"]:
                self.found = fresh
                return r
        return None

    def _fill(self, ins: dict) -> None:
        results = []
        uploaded = None
        items = [i for i in ins["fill"] if i.get("step", 0) == self.step]
        if items:
            self.event("FILLING_FORM", f"Filling {len(items)} field(s) on page {self.step + 1}")
        for item in items:
            if item["action"] == "upload":
                self.event("UPLOADING_RESUME", f"Uploading {os.path.basename(self.resume_path or '')}")
            ref = item["field_ref"]
            ok, msg = self.B.fill_field(
                self.page, self.found, ref, item["action"], item["value"], item["input_type"], self.resume_path
            )
            if not ok and ("no longer on the page" in msg or "Could not fill" in msg):
                new_ref = self._remap(ref)  # stale element: the page re-rendered
                if new_ref:
                    ok, msg = self.B.fill_field(
                        self.page,
                        self.found,
                        new_ref,
                        item["action"],
                        item["value"],
                        item["input_type"],
                        self.resume_path,
                    )
            if item["action"] == "upload":
                uploaded = ok
                self.event("UPLOADING_RESUME", "Resume attached and recognised by the form" if ok else msg)
            results.append({"field_ref": ref, "ok": ok, "validation_message": msg})
        self.event("VALIDATING_FORM", "Checking the form for errors")
        errors = {e["ref"]: e["message"] for e in self.B.validation_errors(self.page) if e["ref"]}
        for r in results:
            if r["field_ref"] in errors and r["ok"]:
                r["validation_message"] = errors[r["field_ref"]]
        self.filled_revision = ins["answers_revision"]
        pending = ins.get("unresolved") or []
        done = sum(1 for r in results if r["ok"])
        if pending:
            msg = f"{done} field(s) filled. {len(pending)} question(s) need your answer. The browser is paused on page {self.step + 1}."
        else:
            msg = f"{done} field(s) filled on page {self.step + 1}. Review the application in CareerPilot."
        extra = {"resume_uploaded": uploaded} if uploaded is not None else {}
        self.report("awaiting_review", msg, fill_results=results, filled_revision=self.filled_revision, **extra)

    def _advanced(self, before: set[str]) -> bool:
        after = {f.data["label"] for f in self.B.inspect_form(self.page)}
        return bool(after - before) or bool(self.B.detect_obstacle(self.page))

    def _next_step(self, hit) -> None:
        frame, btn = hit
        before = {f.data["label"] for f in self.B.inspect_form(self.page)}
        loc = frame.locator(f'[data-cp-btn="{btn["ref"]}"]').first
        moved = False
        for attempt in range(2):
            if attempt and not (loc.count() and loc.is_visible()):
                break  # the page changed under us; the checks below decide what happened
            loc.click(timeout=10000)
            for _ in range(16):  # slow pages: wait up to ~8 s for the next page before trying again
                self.page.wait_for_timeout(500)
                if self._advanced(before):
                    moved = True
                    break
            if moved:
                break
        if not moved and not self._advanced(before):
            errors = self.B.validation_errors(self.page)
            detail = "; ".join(e["message"] for e in errors[:3]) or "the next page did not open"
            self.report("awaiting_answers", f"The form did not move on ({detail}). Check the browser window.")
            time.sleep(POLL_SECONDS * 2)
            return
        self._page_changed()

    def _page_changed(self) -> None:
        self.event("FILLING_FORM", f"Page {self.step + 1} completed")
        self.step += 1
        self.found = {}  # the next inspection belongs to the new page
        obstacle = self.B.detect_obstacle(self.page)
        if obstacle:
            self.wait_for_user(obstacle)
            return
        self.event("INSPECTING_PAGE", f"Page {self.step + 1} opened")
        ins = self._inspect_and_post()
        self._fill(ins)

    def _submit(self) -> str:
        B = self.B
        hit = B.find_button(self.page, B.SUBMIT_TEXT, prefer_submit_type=True)
        if not hit:
            self.report(
                "submitted_unverified",
                "You confirmed, but no submit button was found. Submit it in the browser window; nothing was clicked.",
                phase="SUBMISSION_UNCERTAIN",
            )
            return "submitted_unverified"
        # Recorded before the click, so a crash after it can never lead to a second submission.
        self.report("submitting", "Submitting the application...", phase="SUBMITTING")
        frame, btn = hit
        before = self.page.url
        frame.locator(f'[data-cp-btn="{btn["ref"]}"]').first.click(timeout=10000)
        self.event("VERIFYING_SUBMISSION", "Submit clicked; looking for the employer's confirmation")
        ok, text = False, ""
        for _ in range(8):
            self.page.wait_for_timeout(1500)
            ok, text = B.detect_confirmation(self.page)
            if ok:
                break
        if ok:
            app_id = self.adapters.confirmation_id(self.adapter, text) or self.adapters.confirmation_id(
                self.adapter, self._body()
            )
            msg = "Application submitted - the site confirmed it" + (f" (application ID {app_id})." if app_id else ".")
            self.event("SUBMITTED", msg)
            self.report("submitted", msg, result_text=text, confirmation_id=app_id, phase="SUBMITTED")
            return "submitted"
        errors = B.validation_errors(self.page)
        if errors and self.page.url == before:
            self.report(
                "submitted_unverified",
                "The site did not accept the submission: "
                + "; ".join(e["message"] for e in errors[:3])
                + ". Fix it in the browser window.",
                phase="SUBMISSION_UNCERTAIN",
            )
            return "submitted_unverified"
        self.report(
            "submitted_unverified",
            "Submission status is uncertain: no confirmation was found. Check the employer's portal or your email.",
            result_text=text,
            phase="SUBMISSION_UNCERTAIN",
        )
        return "submitted_unverified"

    def _body(self) -> str:
        try:
            return self.page.inner_text("body", timeout=3000)
        except Exception:  # noqa: BLE001
            return ""


def browser_profile_dir() -> str:
    path = Path(os.environ.get("CAREERPILOT_PROFILE_DIR", HOME / "browser-profile"))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return str(path)


def run_loop(
    api: Api,
    headless: bool = False,
    once: bool = False,
    idle_poll: float = IDLE_POLL_SECONDS,
    profile_dir: str | None = None,
    user_wait: float = USER_WAIT_SECONDS,
    stop=None,
) -> list[str]:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw, tempfile.TemporaryDirectory(prefix="cp-runner-") as workdir:
        ctx = pw.chromium.launch_persistent_context(
            profile_dir or browser_profile_dir(),
            headless=headless,
            accept_downloads=False,
            viewport={"width": 1280, "height": 900},
        )
        try:
            while not (stop and stop.is_set()):
                try:
                    session = api.claim()
                except (httpx.HTTPError, RuntimeError) as exc:
                    log.warning("CareerPilot is unreachable (%s); retrying", exc)
                    time.sleep(idle_poll * 2)
                    continue
                if session is None:
                    if once:
                        break
                    time.sleep(idle_poll)
                    continue
                page = ctx.new_page()
                page.on("dialog", lambda d: d.dismiss())  # unexpected alerts never block the agent
                log.info("Session %s: %s at %s", session["session_id"], session["role_title"], session["company"])
                api.heartbeat("working", f"{session['role_title']} at {session['company']}")
                try:
                    state = SessionRunner(api, page, session, workdir, user_wait).run()
                except SessionEnded:
                    state = api.instructions(session["session_id"])["state"]
                except UntrustedInstructions as exc:
                    log.error("%s", exc)
                    state = "failed"
                except Exception as exc:  # noqa: BLE001
                    log.error("Session %s failed: %s", session["session_id"], exc)
                    try:
                        api.report(
                            session["session_id"],
                            state="failed",
                            phase="FAILED",
                            message=f"The runner hit an error ({type(exc).__name__}). Nothing was submitted.",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    state = "failed"
                results.append(state)
                api.heartbeat("idle", "Ready")
                log.info("Session %s finished: %s", session["session_id"], state)
                if once:
                    break
        finally:
            ctx.close()
    return results


# --- installation as a background service -------------------------------------------------------------

LABEL = "com.careerpilot.runner"


def config_path() -> Path:
    return HOME / "runner.json"


def save_config(api: str, token: str) -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)
    p = config_path()
    p.write_text(json.dumps({"api": api, "token": token}))
    os.chmod(p, 0o600)
    return p


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def _service_command() -> list[str]:
    return [sys.executable, "-m", "app.automation.runner"]


def install_service() -> str:
    """Starts the runner at login and keeps it running (launchd on macOS, systemd --user on Linux)."""
    cwd = str(Path(__file__).resolve().parents[2])
    logf = str(HOME / "runner.log")
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{a}</string>" for a in _service_command())
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
            f"<key>ProgramArguments</key><array>{args}</array>"
            f"<key>WorkingDirectory</key><string>{cwd}</string>"
            "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
            f"<key>StandardOutPath</key><string>{logf}</string><key>StandardErrorPath</key><string>{logf}</string>"
            "</dict></plist>\n"
        )
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True)
        return f"Installed and started ({plist}). It starts automatically when you log in. Log: {logf}"
    if system == "Linux":
        unit = Path.home() / ".config" / "systemd" / "user" / "careerpilot-runner.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\nDescription=CareerPilot Runner\nAfter=network-online.target\n\n"
            f"[Service]\nWorkingDirectory={cwd}\nExecStart={' '.join(_service_command())}\nRestart=always\n"
            "RestartSec=5\n\n[Install]\nWantedBy=default.target\n"
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "careerpilot-runner"], check=True)
        return f"Installed and started ({unit}). It starts automatically when you log in."
    return (
        "Automatic start is set up by hand on this system. Create a scheduled task that runs at log-on:\n"
        f'  schtasks /Create /SC ONLOGON /TN CareerPilotRunner /TR "{" ".join(_service_command())}"'
    )


def uninstall_service() -> str:
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        plist.unlink(missing_ok=True)
        return "Removed. The runner no longer starts at login."
    if system == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", "careerpilot-runner"], capture_output=True)
        (Path.home() / ".config" / "systemd" / "user" / "careerpilot-runner.service").unlink(missing_ok=True)
        return "Removed. The runner no longer starts at login."
    return "Delete the CareerPilotRunner scheduled task: schtasks /Delete /TN CareerPilotRunner"


def start_service() -> str:
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"], check=True)
        return "Started."
    if platform.system() == "Linux":
        subprocess.run(["systemctl", "--user", "restart", "careerpilot-runner"], check=True)
        return "Started."
    return "Run: schtasks /Run /TN CareerPilotRunner"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CareerPilot Runner - the local application agent")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "install", "uninstall", "start", "status"])
    parser.add_argument("--api", default=os.environ.get("CAREERPILOT_API", ""))
    parser.add_argument("--token", default=os.environ.get("CAREERPILOT_TOKEN", ""))
    parser.add_argument(
        "--headless",
        action="store_true",
        help="for automated tests only; you cannot sign in or solve CAPTCHAs in headless mode",
    )
    parser.add_argument("--once", action="store_true", help="process one application and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config()
    api_url = args.api or cfg.get("api", "")
    token = args.token or cfg.get("token", "")

    if args.command == "uninstall":
        print(uninstall_service())
        return 0
    if args.command == "start":
        print(start_service())
        return 0
    if not token or not api_url:
        print(
            "Create a runner token in CareerPilot (Settings > Browser automation), then run:\n"
            "  careerpilot-runner install --api <CareerPilot URL> --token <token>",
            file=sys.stderr,
        )
        return 2
    api = Api(api_url, token)
    try:
        who = api.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to CareerPilot at {api_url}: {exc}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(f"Connected to {api_url} as {who['user']} (token '{who['token']}').")
        return 0
    if args.command == "install":
        save_config(api_url, token)
        print(install_service())
        return 0
    log.info("Connected as %s. Waiting for applications (Ctrl+C to stop)...", who["user"])
    api.heartbeat("idle", "Ready")
    try:
        run_loop(api, headless=args.headless, once=args.once)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
