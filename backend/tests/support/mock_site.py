"""Local mock career site and mock GitHub API used by the end-to-end and browser tests.

Everything served here is a clearly marked local test fixture: a fictional posting and application form, and
two fictional repositories. Submissions are recorded in memory so tests can verify exactly what was sent.
"""

import json
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SUBMISSIONS: list[dict] = []

JOB_DESCRIPTION_HTML = """
<h2>About the role</h2>
<p>Join the team building developer platform services. You will design backend APIs used across the company.</p>
<h2>What you will do</h2>
<ul>
  <li>Design and build REST APIs using Python and FastAPI</li>
  <li>Model data in PostgreSQL and write efficient SQL</li>
  <li>Write automated tests with pytest and ship with Docker</li>
</ul>
<h2>Required qualifications</h2>
<ul>
  <li>Bachelor's degree in Computer Science or a related field</li>
  <li>0-2 years of experience in software development</li>
  <li>Strong knowledge of Python, SQL and data structures and algorithms</li>
</ul>
<h2>Preferred qualifications</h2>
<ul>
  <li>Experience with AWS or another cloud platform</li>
  <li>Familiarity with Kubernetes</li>
</ul>
<p>Applications close on 30 November 2026.</p>
"""

JOB_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Software Engineer - IBM Careers (mock)</title>
<meta property="og:site_name" content="IBM Careers (mock)">
<script type="application/ld+json">%s</script></head>
<body><!-- LOCAL TEST FIXTURE: mock job page, not a real posting -->
<header><nav>Careers home</nav></header>
<main><h1>Software Engineer</h1><div class="job-meta">Job ID: 12345 &middot; Bengaluru, India &middot; Hybrid</div>
<div class="job-description">%s</div>
<a class="apply" href="/careers/ibm/apply/12345">Apply now</a></main>
<footer>Local test fixture</footer></body></html>"""

JOB_LD = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Software Engineer",
    "hiringOrganization": {"@type": "Organization", "name": "IBM"},
    "identifier": {"@type": "PropertyValue", "value": "12345"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Bengaluru", "addressCountry": "IN"}},
    "employmentType": "FULL_TIME",
    "datePosted": "2026-09-20",
    "description": JOB_DESCRIPTION_HTML,
}

APPLY_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Apply - Software Engineer (mock)</title>
<style>.step2{display:none} .error{color:#b00}</style></head>
<body><!-- LOCAL TEST FIXTURE: mock application form -->
<h1>Apply: Software Engineer</h1>
<form id="app" method="post" action="/careers/ibm/apply/12345" enctype="multipart/form-data" novalidate>
 <section class="step1">
  <div class="field"><label for="fn">First name *</label><input id="fn" name="first_name" required></div>
  <div class="field"><label for="ln">Last name *</label><input id="ln" name="last_name" required></div>
  <div class="field"><label for="em">Email *</label><input id="em" name="email" type="email" required></div>
  <div class="field"><label for="ph">Phone</label><input id="ph" name="phone" type="tel"></div>
  <div class="field"><label for="li">LinkedIn profile</label><input id="li" name="linkedin" type="url"></div>
  <div class="field"><label for="gh">GitHub URL</label><input id="gh" name="github" type="url"></div>
  <button type="button" id="next">Next</button>
 </section>
 <section class="step2">
  <div class="field"><label for="cv">Resume/CV *</label><input id="cv" name="resume" type="file" accept=".pdf" required></div>
  <div class="field"><label for="auth">Are you legally authorized to work in India? *</label>
   <select id="auth" name="authorized" required><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
  <fieldset class="field"><legend>Will you now or in the future require visa sponsorship? *</legend>
   <label><input type="radio" name="sponsorship" value="yes" required> Yes</label>
   <label><input type="radio" name="sponsorship" value="no"> No</label></fieldset>
  <div class="field"><label for="yoe">Years of experience</label><input id="yoe" name="years" type="number" step="0.5"></div>
  <div class="field"><label for="why">Why do you want to work at IBM? *</label><textarea id="why" name="why" required></textarea></div>
  <div class="field"><label for="gender">Gender (voluntary self-identification)</label>
   <select id="gender" name="gender"><option value="">Select...</option><option>Female</option><option>Male</option>
   <option>Non-binary</option><option>Prefer not to say</option></select></div>
  <div class="field"><label><input type="checkbox" id="consent" name="consent" required> I agree to the privacy policy *</label></div>
  <button type="submit">Submit application</button>
 </section>
</form>
<script>
 document.getElementById('next').addEventListener('click', () => {
   const need = ['fn','ln','em'].filter(id => !document.getElementById(id).value);
   if (need.length) { need.forEach(id => document.getElementById(id).setAttribute('aria-invalid','true')); return; }
   document.querySelector('.step1').style.display = 'none';
   document.querySelector('.step2').style.display = 'block';
 });
</script></body></html>"""

THANKS_PAGE = """<!doctype html><html><body><!-- LOCAL TEST FIXTURE -->
<h1>Thank you for applying!</h1><p>Your application for Software Engineer (Job ID 12345) has been received.</p>
</body></html>"""

ERROR_PAGE = """<!doctype html><html><body><h1>Apply: Software Engineer</h1>
<div role="alert" class="error">Please complete all required fields: %s</div></body></html>"""

SPA_PAGE = """<!doctype html><html><head><title>Careers (mock SPA)</title>
<meta property="og:site_name" content="Example Systems"></head><body><!-- LOCAL TEST FIXTURE -->
<div id="app">Loading...</div>
<script>
  // A career site that builds the posting with JavaScript, and also tries to reach a cloud metadata address.
  fetch('http://169.254.169.254/latest/meta-data/').then(() => { document.title = 'LEAKED'; }).catch(() => {});
  setTimeout(() => {
    document.getElementById('app').innerHTML = `<main class="job-description"><h1>Backend Developer</h1>
      <h2>Requirements</h2><ul><li>Strong Python and SQL skills</li><li>Experience building REST APIs with FastAPI</li>
      <li>Familiarity with Docker and PostgreSQL</li><li>Good understanding of data structures and algorithms</li></ul>
      <h2>What you will do</h2><ul><li>Build and test backend services</li><li>Review code and write documentation</li></ul></main>`;
  }, 300);
</script></body></html>"""

LOGIN_PAGE = """<!doctype html><html><body><!-- LOCAL TEST FIXTURE -->
<h1>Sign in to continue</h1><form><label>Email <input name="email" type="email"></label>
<label>Password <input name="password" type="password"></label><button>Sign in</button></form></body></html>"""

CAPTCHA_PAGE = """<!doctype html><html><body><!-- LOCAL TEST FIXTURE -->
<h1>Apply</h1><iframe src="/recaptcha/api2/anchor" width="300" height="80"></iframe></body></html>"""

# --- multi-page application used by the agent end-to-end test ---------------------------------------------
# Three pages, a CAPTCHA between pages 1 and 2 that only "the user" can clear, and injected faults: the first
# page load fails (HTTP 503), the resume input rejects the first attachment, page 2 re-renders its inputs (stale
# elements) and raises an alert, and page 3 opens slowly.
AGENT = {"loads": 0, "captcha_solved": False, "submissions": []}

MULTI_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Acme Robotics - Apply</title>
<style>.page{display:none}.page.on{display:block}#captcha{margin:12px 0}</style></head>
<body><!-- LOCAL TEST FIXTURE: multi-page application -->
<h1>Backend Engineer - Acme Robotics</h1>
<form id="app" method="post" action="/careers/acme/apply/900" enctype="multipart/form-data" novalidate>
 <section class="page on" id="p1">
  <div><label for="a-fn">First name *</label><input id="a-fn" name="first_name" required></div>
  <div><label for="a-ln">Last name *</label><input id="a-ln" name="last_name" required></div>
  <div><label for="a-em">Email address *</label><input id="a-em" name="email" type="email" required></div>
  <div><label for="a-ph">Phone number</label><input id="a-ph" name="phone" type="tel"></div>
  <div><label for="a-cv">Resume *</label><input id="a-cv" name="resume" type="file" accept=".pdf" required></div>
  <button type="button" class="next" data-to="p2">Continue</button>
 </section>
 <section class="page" id="p2"><div id="p2-fields"></div><button type="button" class="next" data-to="p3">Continue</button></section>
 <section class="page" id="p3">
  <div><label for="a-why">Why do you want to join Acme Robotics? *</label><textarea id="a-why" name="why" required></textarea></div>
  <div><label for="a-src">How did you hear about us?</label><select id="a-src" name="source"><option value="">Select...</option>
   <option>Company website</option><option>LinkedIn</option><option>Referral</option></select></div>
  <div><label><input type="checkbox" id="a-ok" name="consent" required> I confirm the information is accurate *</label></div>
  <button type="submit">Submit application</button>
 </section>
 <div id="captcha" style="display:none"><iframe src="/recaptcha/api2/anchor?k=test" width="300" height="80"></iframe></div>
</form>
<script>
 let rejected = false;
 const cv = document.getElementById('a-cv');
 cv.addEventListener('change', () => { if (!rejected) { rejected = true; cv.value = ''; } });
 const P2 = `<div><label for="a-li">LinkedIn URL</label><input id="a-li" name="linkedin" type="url"></div>
  <div><label for="a-auth">Are you authorized to work in India? *</label><select id="a-auth" name="authorized" required>
   <option value="">Select...</option><option>Yes</option><option>No</option></select></div>
  <div><label for="a-yoe">Years of professional experience</label><input id="a-yoe" name="years" type="number" step="0.5"></div>`;
 function show(id) { document.querySelectorAll('.page').forEach(p => p.classList.remove('on')); document.getElementById(id).classList.add('on'); }
 function gateCaptcha(then) {
   const box = document.getElementById('captcha'); box.style.display = 'block';
   const t = setInterval(async () => {
     const st = await (await fetch('/careers/acme/captcha-state')).json();
     if (st.solved) { clearInterval(t); box.style.display = 'none'; then(); }
   }, 500);
 }
 document.querySelectorAll('.next').forEach(b => b.addEventListener('click', () => {
   const to = b.dataset.to;
   if (to === 'p2') {
     const need = ['a-fn','a-ln','a-em'].filter(id => !document.getElementById(id).value);
     if (need.length || !cv.files.length) { need.forEach(id => document.getElementById(id).setAttribute('aria-invalid','true')); return; }
     document.getElementById('p1').classList.remove('on');
     gateCaptcha(() => {
       document.getElementById('p2-fields').innerHTML = P2; show('p2');
       setTimeout(() => { document.getElementById('p2-fields').innerHTML = P2; alert('Progress saved'); }, 1200);
     });
   } else { setTimeout(() => show(to), 2500); }
 }));
</script></body></html>"""

AGENT_THANKS = """<!doctype html><html><body><!-- LOCAL TEST FIXTURE -->
<h1>Thank you for applying!</h1><p>Your application has been received.</p><p>Application ID: ACME-2026-4821</p>
</body></html>"""


REPOS = {
    "taskflow-api": {
        "description": "Task tracking REST API with role-based access control",
        "homepage": "",
        "tree": [
            ("README.md", 1200),
            ("backend/requirements.txt", 120),
            ("backend/app/main.py", 9000),
            ("backend/app/permissions.py", 2400),
            ("backend/Dockerfile", 300),
            ("backend/tests/test_api.py", 5000),
            ("backend/alembic.ini", 200),
            (".github/workflows/ci.yml", 400),
        ],
        "files": {
            "README.md": "# TaskFlow API\n\nTask tracking backend built with FastAPI and PostgreSQL.\n\n## Features\n"
            "- Role-based access control for admins, managers and members\n"
            "- JWT authentication with refresh tokens\n- Audit log of every task change\n",
            "backend/requirements.txt": "fastapi==0.115\nsqlalchemy==2.0\npsycopg[binary]==3.2\nalembic==1.14\n"
            "pyjwt==2.10\npytest==8.3\n",
        },
    },
    "pixel-notes": {
        "description": "Note-taking web app",
        "homepage": "https://pixel-notes.example.com",
        "tree": [("README.md", 500), ("package.json", 400), ("src/App.tsx", 7000), ("vite.config.ts", 200)],
        "files": {
            "README.md": "# Pixel Notes\n\nA React and TypeScript note-taking app.\n",
            "package.json": json.dumps(
                {
                    "dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"},
                    "devDependencies": {"typescript": "^5.6.0", "vite": "^5.4.0"},
                }
            ),
        },
    },
}
GH_USER = "fixture-user"


def _repo_json(name: str) -> dict:
    r = REPOS[name]
    return {
        "name": name,
        "full_name": f"{GH_USER}/{name}",
        "description": r["description"],
        "html_url": f"https://github.com/{GH_USER}/{name}",
        "homepage": r["homepage"],
        "private": False,
        "fork": False,
        "archived": False,
        "default_branch": "main",
        "pushed_at": "2026-09-01T10:00:00Z",
        "stargazers_count": 0,
        "topics": [],
        "language": "Python",
        "owner": {"login": GH_USER},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/careers/ibm/jobs/12345":
            return self._send(200, JOB_PAGE % (json.dumps(JOB_LD), JOB_DESCRIPTION_HTML))
        if path == "/careers/ibm/apply/12345":
            return self._send(200, APPLY_PAGE)
        if path == "/careers/ibm/applied/12345":
            return self._send(200, THANKS_PAGE)
        if path == "/careers/spa-job/77":
            return self._send(200, SPA_PAGE)
        if path == "/careers/login-required":
            return self._send(200, LOGIN_PAGE)
        if path == "/careers/captcha":
            return self._send(200, CAPTCHA_PAGE)
        if path.startswith("/recaptcha/"):
            return self._send(200, "<html><body>captcha</body></html>")
        if path == "/careers/acme/apply/900":
            AGENT["loads"] += 1
            if AGENT["loads"] == 1:
                return self._send(503, "temporarily unavailable")
            return self._send(200, MULTI_PAGE)
        if path == "/careers/acme/captcha-state":
            return self._json({"solved": AGENT["captcha_solved"]})
        if path == "/careers/acme/applied/900":
            return self._send(200, AGENT_THANKS)
        if path == "/careers/redirect-internal":
            return self._send(302, "", headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        # --- mock GitHub API ---
        if path == f"/github-api/users/{GH_USER}":
            return self._json({"login": GH_USER})
        if path == f"/github-api/users/{GH_USER}/repos":
            q = parse_qs(urlsplit(self.path).query)
            return self._json([_repo_json(n) for n in REPOS] if q.get("page", ["1"])[0] == "1" else [])
        m = re.match(rf"^/github-api/repos/{GH_USER}/([\w.-]+)$", path)
        if m and m.group(1) in REPOS:
            return self._json(_repo_json(m.group(1)))
        m = re.match(rf"^/github-api/repos/{GH_USER}/([\w.-]+)/git/trees/main$", path)
        if m and m.group(1) in REPOS:
            tree = [{"path": p, "type": "blob", "size": s} for p, s in REPOS[m.group(1)]["tree"]]
            return self._json({"tree": tree, "truncated": False})
        m = re.match(rf"^/github-raw/{GH_USER}/([\w.-]+)/main/(.+)$", path)
        if m and m.group(1) in REPOS and m.group(2) in REPOS[m.group(1)]["files"]:
            return self._send(200, REPOS[m.group(1)]["files"][m.group(2)], "text/plain; charset=utf-8")
        if path.startswith("/github-"):
            return self._json({"message": "Not Found"}, 404)
        return self._send(404, "not found")

    def _json(self, obj, status=200):
        return self._send(
            status,
            json.dumps(obj),
            "application/json",
            {"x-ratelimit-remaining": "4999", "x-ratelimit-reset": "9999999999"},
        )

    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path not in ("/careers/ibm/apply/12345", "/careers/acme/apply/900"):
            return self._send(404, "not found")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        msg = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + body
        )
        fields, files = {}, {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {"filename": filename, "size": len(payload), "is_pdf": payload.startswith(b"%PDF")}
            else:
                fields[name] = payload.decode(errors="replace")
        if path == "/careers/acme/apply/900":
            AGENT["submissions"].append({"fields": fields, "files": files})
            return self._send(303, "", headers={"Location": "/careers/acme/applied/900"})
        missing = [
            k
            for k in ("first_name", "last_name", "email", "authorized", "sponsorship", "why", "consent")
            if not fields.get(k)
        ]
        if not files.get("resume", {}).get("size"):
            missing.append("resume")
        if missing:
            return self._send(200, ERROR_PAGE % ", ".join(missing))
        SUBMISSIONS.append({"fields": fields, "files": files})
        return self._send(303, "", headers={"Location": "/careers/ibm/applied/12345"})


class MockSite:
    def __init__(self, port: int = 0):
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        SUBMISSIONS.clear()
        AGENT.update(loads=0, captcha_solved=False, submissions=[])
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
