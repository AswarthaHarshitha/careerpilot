"""CareerPilot apply agent.

Runs on your computer and drives a visible Chromium window. It picks up application sessions you start in
CareerPilot, fills what your profile can answer, uploads the tailored resume, and waits. It clicks the
employer's submit button only after you confirm submission in CareerPilot.

    careerpilot-runner --api https://your-careerpilot.example --token cpr_...

The browser profile (your logins on employer sites) is stored locally in ~/.careerpilot/browser-profile and
never leaves your machine.
"""

import argparse
import base64
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import httpx

log = logging.getLogger("careerpilot.runner")
USER_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 2.0


class SessionEnded(Exception):
    """The session was cancelled or finished on the server while the agent was working."""


class Api:
    def __init__(self, base: str, token: str, http: httpx.Client | None = None):
        self.base = base.rstrip("/")
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)
        self.http.headers["Authorization"] = f"Bearer {token}"

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

    def ping(self) -> dict:
        return self._check(self.http.get(f"{self.base}/api/runner/ping"))

    def claim(self) -> dict | None:
        return self._check(self.http.post(f"{self.base}/api/runner/claim")).get("session")

    def instructions(self, sid: int) -> dict:
        return self._check(self.http.get(f"{self.base}/api/runner/sessions/{sid}"))

    def post_fields(self, sid: int, step: int, fields: list[dict]) -> dict:
        return self._check(
            self.http.post(f"{self.base}/api/runner/sessions/{sid}/fields", json={"step": step, "fields": fields})
        )

    def report(self, sid: int, **body) -> dict:
        return self._check(self.http.post(f"{self.base}/api/runner/sessions/{sid}/report", json=body))

    def download_resume(self, sid: int, dest_dir: str) -> str:
        r = self.http.get(f"{self.base}/api/runner/sessions/{sid}/resume.pdf")
        if r.status_code != 200:
            raise RuntimeError("Could not download the resume PDF.")
        m = re.search(r'filename="([^"]+)"', r.headers.get("content-disposition", ""))
        name = re.sub(r"[^A-Za-z0-9._-]", "_", m.group(1) if m else "Resume.pdf")
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as fh:
            fh.write(r.content)
        return path


def _screenshot(page) -> str | None:
    try:
        return base64.b64encode(page.screenshot(type="png", full_page=False)).decode()
    except Exception:  # noqa: BLE001
        return None


class SessionRunner:
    def __init__(self, api: Api, page, session: dict, workdir: str, user_wait: float = USER_WAIT_SECONDS):
        from app.automation import browser as B

        self.B = B
        self.api = api
        self.page = page
        self.sid = session["session_id"]
        self.start_url = session["start_url"]
        self.workdir = workdir
        self.user_wait = user_wait
        self.step = 0
        self.found: dict = {}
        self.resume_path: str | None = None
        self.filled_revision = -1

    def report(self, state: str, message: str = "", **extra) -> dict:
        return self.api.report(
            self.sid,
            state=state,
            message=message,
            current_url=self.page.url,
            step=self.step,
            screenshot_b64=_screenshot(self.page),
            **extra,
        )

    def run(self) -> str:
        B = self.B
        page = self.page
        page.goto(self.start_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        if not self._reach_form():
            return "stopped"
        self.resume_path = self.api.download_resume(self.sid, self.workdir)
        ins = self._inspect_and_post()
        self._fill(ins)
        while True:
            ins = self.api.instructions(self.sid)
            if ins["cancelled"] or ins["terminal"]:
                return ins["state"]
            if ins["submit"]:
                return self._submit()
            if ins["answers_revision"] > self.filled_revision:
                self._fill(ins)
                continue
            if not ins["unresolved"] and ins["state"] in ("awaiting_review", "filling"):
                nxt = B.find_button(page, B.NEXT_TEXT)
                sub = B.find_button(page, B.SUBMIT_TEXT, prefer_submit_type=True)
                if nxt and not sub:
                    self._next_step(nxt)
                    continue
            obstacle = B.detect_obstacle(page)
            if obstacle and ins["state"] != "action_required":
                self.report("action_required", obstacle)
            time.sleep(POLL_SECONDS)

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
            obstacle = B.detect_obstacle(self.page)
            if obstacle:
                if announced != obstacle:
                    self.report("action_required", obstacle)
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
                return True
            if apply_clicks < 3 and B.click_apply(self.page):
                apply_clicks += 1
                self.report("running", "Opened the application form...")
                continue
            empty_polls += 1
            if announced is None and empty_polls >= 6:
                self.report(
                    "unsupported",
                    "No application form was found on this page. The site may use a portal "
                    "CareerPilot cannot read. Open the job and apply manually.",
                )
                return False
            time.sleep(POLL_SECONDS)
        self.report("failed", "Stopped waiting: the page never reached an application form.")
        return False

    def _inspect_and_post(self) -> dict:
        found = self.B.inspect_form(self.page)
        self.found = {f.ref: f for f in found}
        payload = []
        for f in found:
            d = f.data
            payload.append(
                {
                    "field_ref": f.ref,
                    "label": d["label"],
                    "input_type": d["input_type"],
                    "required": d.get("required", False),
                    "options": d.get("options", []),
                }
            )
        return self.api.post_fields(self.sid, self.step, payload)

    def _fill(self, ins: dict) -> None:
        results = []
        for item in ins["fill"]:
            if item.get("step", 0) != self.step:
                continue
            ok, msg = self.B.fill_field(
                self.page,
                self.found,
                item["field_ref"],
                item["action"],
                item["value"],
                item["input_type"],
                self.resume_path,
            )
            results.append({"field_ref": item["field_ref"], "ok": ok, "validation_message": msg})
        errors = {e["ref"]: e["message"] for e in self.B.validation_errors(self.page) if e["ref"]}
        for r in results:
            if r["field_ref"] in errors and r["ok"]:
                r["validation_message"] = errors[r["field_ref"]]
        self.filled_revision = ins["answers_revision"]
        pending = ins.get("unresolved") or []
        msg = (
            "Filled what your profile answers. Review the application in CareerPilot."
            if not pending
            else f"{len(pending)} field(s) need your answer in CareerPilot."
        )
        self.report("awaiting_review", msg, fill_results=results, filled_revision=self.filled_revision)

    def _next_step(self, hit) -> None:
        frame, btn = hit
        before = {f.ref for f in self.B.inspect_form(self.page)}
        frame.locator(f'[data-cp-btn="{btn["ref"]}"]').first.click(timeout=10000)
        self.page.wait_for_timeout(2000)
        after = {f.ref for f in self.B.inspect_form(self.page)}
        if not (after - before):
            errors = self.B.validation_errors(self.page)
            detail = "; ".join(e["message"] for e in errors[:3]) or "the next step did not open"
            self.report("awaiting_answers", f"The form did not move on ({detail}). Check the browser window.")
            time.sleep(POLL_SECONDS * 2)
            return
        self.step += 1
        ins = self._inspect_and_post()
        self._fill(ins)

    def _submit(self) -> str:
        B = self.B
        hit = B.find_button(self.page, B.SUBMIT_TEXT, prefer_submit_type=True)
        if not hit:
            self.report(
                "submitted_unverified",
                "You confirmed, but no submit button was found. Submit it in the browser window; nothing was clicked.",
            )
            return "submitted_unverified"
        self.report("submitting", "Submitting the application...")
        frame, btn = hit
        before = self.page.url
        frame.locator(f'[data-cp-btn="{btn["ref"]}"]').first.click(timeout=10000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        self.page.wait_for_timeout(1500)
        ok, text = B.detect_confirmation(self.page)
        if ok:
            self.report("submitted", "Application submitted - the site confirmed it.", result_text=text)
            return "submitted"
        errors = B.validation_errors(self.page)
        if errors and self.page.url == before:
            self.report(
                "submitted_unverified",
                "The site did not accept the submission: "
                + "; ".join(e["message"] for e in errors[:3])
                + ". Fix it in the browser window.",
            )
            return "submitted_unverified"
        self.report(
            "submitted_unverified",
            "Submission status could not be verified. Please check the application portal.",
            result_text=text,
        )
        return "submitted_unverified"


def browser_profile_dir() -> str:
    path = Path(os.environ.get("CAREERPILOT_PROFILE_DIR", Path.home() / ".careerpilot" / "browser-profile"))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return str(path)


def run_loop(
    api: Api,
    headless: bool = False,
    once: bool = False,
    idle_poll: float = 5.0,
    profile_dir: str | None = None,
    user_wait: float = USER_WAIT_SECONDS,
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
            while True:
                session = api.claim()
                if session is None:
                    if once:
                        break
                    time.sleep(idle_poll)
                    continue
                page = ctx.new_page()
                log.info("Session %s: %s at %s", session["session_id"], session["role_title"], session["company"])
                try:
                    state = SessionRunner(api, page, session, workdir, user_wait).run()
                except SessionEnded:
                    state = api.instructions(session["session_id"])["state"]
                except Exception as exc:  # noqa: BLE001
                    log.error("Session %s failed: %s", session["session_id"], exc)
                    try:
                        api.report(
                            session["session_id"],
                            state="failed",
                            message=f"The apply agent hit an error ({type(exc).__name__}). Nothing was submitted.",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    state = "failed"
                results.append(state)
                log.info("Session %s finished: %s", session["session_id"], state)
                if once:
                    break
        finally:
            ctx.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CareerPilot apply agent")
    parser.add_argument("--api", default=os.environ.get("CAREERPILOT_API", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("CAREERPILOT_TOKEN", ""))
    parser.add_argument(
        "--headless",
        action="store_true",
        help="for automated tests only; you cannot log in or solve CAPTCHAs in headless mode",
    )
    parser.add_argument("--once", action="store_true", help="process one session and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not args.token:
        print(
            "Create a runner token in CareerPilot (Settings > Browser automation) and pass it with --token or "
            "CAREERPILOT_TOKEN.",
            file=sys.stderr,
        )
        return 2
    api = Api(args.api, args.token)
    try:
        who = api.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to CareerPilot at {args.api}: {exc}", file=sys.stderr)
        return 1
    log.info("Connected as %s. Waiting for applications (Ctrl+C to stop)...", who["user"])
    try:
        run_loop(api, headless=args.headless, once=args.once)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
