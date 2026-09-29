# CareerPilot

A personal job-application workspace. Paste a job link. CareerPilot reads the job and compares it with your verified experience and GitHub work. It then produces a tailored resume in your own template, keeps every claim traceable to evidence, and fills the application form in a browser you can see. Nothing is submitted until you confirm it.

## The problem

Tailoring a resume for every application is slow, and the usual shortcuts create new problems:

- Generic resumes miss the role's actual requirements.
- Rewriting tools invent skills, numbers and projects that the candidate cannot defend in an interview.
- Reformatting into someone else's layout loses the template the candidate chose.

CareerPilot treats the candidate's own evidence as the source of truth. It changes the content inside the candidate's template and never changes the layout.

## What it does

**Job intake.** Accepts any job link. Supported sources:

- Greenhouse, Lever, Ashby, SmartRecruiters, Workday and Oracle Recruiting Cloud, read through their public job APIs.
- JSON-LD `JobPosting` data on any page.
- The page's main content.
- For JavaScript-only career sites, a sandboxed headless render.

Requirements, responsibilities, experience range, work mode and deadlines are extracted deterministically. A language model only fills gaps.

**Evidence.** Scans your GitHub repositories:

- Files, manifests, dependencies and READMEs are read, and skills are mapped through a taxonomy of aliases, related and parent skills.
- Each skill is recorded as *verified* (seen in code or manifests), *inferred* or *user-provided*.
- README sentences that are negated or hypothetical ("planned", "does not use") never count.
- Portfolio and profile repositories are classified as personal-profile assets. They can appear as a header link but never as a project.

**Matching.** Each requirement is marked as strong, partial, missing or needs verification, with the evidence behind it. Only projects with status *Built*, *Verified* or *Deployed* count as experience.

**Supporting projects.** When your projects do not cover a role's gaps, CareerPilot plans a focused project that does. The plan covers architecture, phases, tests, a README outline, interview talking points and an implementation prompt. A planned project is never shown on a resume. Once you link the repository, the scan verifies it skill by skill.

**Tailored resumes.**

- **Template formats:** LaTeX (a single `.tex` or a zipped Overleaf project), DOCX, PDF, and TXT/Markdown. The default is a standard Overleaf-style LaTeX layout.
- **Rendering:** LaTeX templates are parsed into spans and edited surgically, so the preamble, spacing, fonts and section order stay byte-for-byte. PDFs are analysed with PyMuPDF, fingerprinted, rebuilt as an editable LaTeX template and calibrated against the original's heading positions and page count.
- **Per-job instructions:** quick controls or plain sentences such as "put Resolve first, keep it to one page, include my portfolio". They are parsed into a preview before anything is generated. They apply to that job only, and verified evidence always outranks them. A conflict is reported, never resolved by inventing content.
- **Naming:** exports use `First_Last_Resume_Company_Role.pdf`.

**Quality gate.** Every version is checked before export. Blocking failures stop the export until they are fixed. The checks are:

- **Evidence:** every project is finished and eligible, every skill is backed, and every number is verified.
- **Instructions:** job instructions were applied.
- **Template:** the template was preserved.
- **PDF layout:** no clipping, overlap or orphaned headings.
- **Extraction:** ATS text extraction and reading order.
- **Hidden content:** no hidden or unreadably small text, and no placeholders.
- **Writing:** keyword stuffing, repeated openings, generic or inflated wording, first-person language, tense, and text copied from the job description or READMEs.

The checks are concrete pass/review/fail results, not a score, and none of them predicts hiring outcomes.

**Live resume editor.** The resume is a structured document with stable element IDs, shown next to a live preview of the compiled PDF.

- **Direct editing:** edit any bullet, field, link or section directly.
- **Assistant:** ask for a change in plain language ("make the summary 2 lines", "remove the portfolio link"). It turns the request into targeted operations on the named or selected elements only, and never regenerates the whole resume. Broad changes are previewed first. Requests to add skills without evidence are refused.
- **History:** every change is a revision, with undo/redo (⌘Z / ⌘⇧Z) and restore.
- **Provenance:** text you type yourself is tracked, and any new claim in it is flagged in the checks.
- **Saving:** edits autosave, and a queue survives reloads and network failures. If the resume changed in another tab, a conflict dialog offers to keep the current version, use the newer one, or compare them.

**Applications.** A job tracker with deadlines, a dashboard of today's actions, and an apply agent:

- The agent runs on your computer (`careerpilot-runner`) and fills Greenhouse, Lever, Ashby and standard HTML forms in a visible browser.
- It leaves unrecognised questions to you.
- It stops for you at CAPTCHAs and sign-ins.
- It submits only after you confirm in the web app, by default by typing the company name.
- Your employer-site passwords never reach CareerPilot.

## AI providers

CareerPilot needs no paid service. AI is optional, and it is used only where reasoning helps:

- reading unusual job descriptions
- interpreting READMEs
- rewording
- drafting answers
- the editor assistant

Dates, URLs, contact details, skill normalisation, PDF text extraction, page counting and file validation are handled in code.

- **Providers:** Gemini (primary), OpenRouter (free models, fallback) and Ollama (local). They sit behind one interface (`generate`, `generate_structured`, `summarize`, `extract`, `rewrite`, `analyze`), so the rest of the code never depends on a vendor. Model names are discovered from each provider's current model list, not hard-coded.
- **Fallback:**
  - Rate limits (HTTP 429) and temporary failures are retried with exponential backoff.
  - A provider that asks for a long wait is paused, and the next permitted provider is tried.
  - When every provider is exhausted, the step falls back to deterministic processing with a clear message. Edits, job descriptions and notes are never lost.
- **Privacy mode:** resume and profile content goes only to the local model. Every result says which provider produced it.
- **Cost control:**
  - Identical low-temperature requests are answered from a per-user cache.
  - GitHub analysis is cached per repository.
  - Structural editor commands (links, order, sections, page target, undo/redo) never call a model.
- **Usage:** Settings → AI providers shows application-side request counts, rate limits and the last error, plus the provider's own figures where it reports them (OpenRouter does). Nothing is estimated or presented as unlimited.

API keys live only on the server, in the environment or encrypted per user. They never reach the web app. See [SETUP.md](SETUP.md) for every credential.

## Architecture

```
frontend/  React 19 + TypeScript, Vite, Tailwind CSS v4, TanStack Query, React Router, Framer Motion
backend/   FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL (SQLite for local development)
  app/api          HTTP routes (auth, jobs, profile, projects, templates, resumes, editor, applications, runner)
  app/jobs         job-page adapters, HTML extraction, sandboxed JS rendering, deterministic JD parsing
  app/github       GitHub client and evidence extraction
  app/llm          provider interface, Gemini/OpenRouter/Ollama clients, fallback router, usage and cache
  app/matching     skill taxonomy and requirement matching
  app/resume       template parsers and renderers (LaTeX, DOCX, PDF, text), tailoring, instructions,
                   truth guard, quality gate, document model and editing assistant
  app/services     workflows that tie the above together
  app/tasks        database-backed task queue with worker threads (SKIP LOCKED on PostgreSQL)
  app/automation   the local apply agent (Playwright)
e2e/       one-command end-to-end check against a local mock career site and mock GitHub API
```

Slow work runs as background tasks with visible step-by-step progress: GitHub scans, job analysis, resume generation and template analysis. LaTeX compiles with Tectonic in `--untrusted` mode, with a pdfTeX compatibility shim. That shim lets templates written for Overleaf's pdfLaTeX build unchanged.

## Security

- **Accounts:** Argon2 password hashing. Sessions use HTTP-only cookies backed by server-side session rows, and can be revoked individually. Requests that change state need a CSRF header token.
- **Registration:** closed after the owner account is created.
- **Isolation:** every record is scoped to its owner, and IDOR tests cover cross-account access.
- **Secrets:** user secrets (GitHub token, AI provider keys) are encrypted at rest with Fernet. They are write-only in the API, and responses show only a masked form. The web app never receives server keys, and no secret is passed through `VITE_` variables.
- **Outbound fetching:** only public http(s) addresses are allowed, and every redirect is re-checked, which prevents SSRF. The headless renderer applies the same rule to each request a page makes, and it never loads images, fonts or media.
- **Untrusted content:** job descriptions, READMEs and web pages are wrapped as untrusted data in every model prompt. A truth guard also rejects rewrites that introduce facts not present in the evidence.
- **LaTeX:** compilation is sandboxed (`--untrusted`, no shell escape).
- **Rate limits:** applied to authentication and expensive endpoints.

## Running locally

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20.19+, and [Tectonic](https://tectonic-typesetting.github.io/) for LaTeX. [SETUP.md](SETUP.md) explains every credential.

```bash
cp .env.example backend/.env      # adjust as needed
make setup                        # Python and Node dependencies, Playwright Chromium
make dev-api                      # API on http://127.0.0.1:8010 (runs migrations first)
make dev-web                      # web app on http://localhost:5173
```

The first account created becomes the owner, and a setup wizard configures the AI provider and GitHub. To use PostgreSQL locally, run `docker compose up db` and point `DATABASE_URL` at `postgresql://careerpilot:careerpilot@localhost:5433/careerpilot`.

AI is optional. Set `GEMINI_API_KEY`, `OPENROUTER_API_KEY` or `OLLAMA_BASE_URL` in `backend/.env`, or save personal keys under Settings → AI providers.

## Testing

```bash
make test        # backend (pytest) and frontend (Vitest) suites
make e2e         # full pipeline end to end on a fresh database
make lint        # ruff and TypeScript checks
```

The backend suite covers:

- The AI layer: model discovery, backoff, fallback, privacy mode, usage accounting, the cache, and key masking. The editor tests also prove that structural commands make no AI request.
- Job extraction and analysis, including platform adapters, and PDF, Word and text uploads.
- Evidence rules and matching.
- LaTeX, DOCX, PDF and text templates, and template compilation.
- The quality gate and instruction handling.
- The editor: operations, assistant scope, undo/redo, history, concurrency conflicts and export of the latest state.
- Security: authentication, CSRF, IDOR, SSRF and prompt-injection boundaries.
- Browser automation against a local mock application site.

`make e2e` runs a mock job page through extraction, analysis, GitHub evidence, a tailored PDF and autofill. It then covers confirmation, submission and the recorded application.

## Deployment

- **API:** runs on Render as a Docker web service (`render.yaml`, `backend/Dockerfile`). The image bundles Tectonic, a headless Chromium shell and PyMuPDF, runs as a non-root user and applies migrations on start.
- **Database:** PostgreSQL. Supabase's free tier is the default choice; use its session pooler connection string. Any standard PostgreSQL works, and nothing is provider-specific.
- **Web app:** deployed on Vercel from `frontend/`. `vercel.json` rewrites `/api/*` to the Render service, so the browser talks to one origin and cookies stay first-party.

Production settings: `DATABASE_URL`, `SECRET_KEY` (32+ random characters), `SECRET_ENCRYPTION_KEY` (a Fernet key), `COOKIE_SECURE=true` and `PUBLIC_WEB_URL`. With `APP_ENV=production`, the API refuses to start in any of these cases:

- `SECRET_KEY` is weak.
- `SECRET_ENCRYPTION_KEY` is missing.
- Cookies are not marked secure.
- Private-host fetching is enabled.

## Engineering decisions

- **Evidence before language.** Matching, eligibility and quality checks are deterministic, so the same input gives the same answer. A model is used only to extract text and phrase it, and every model output passes a truth guard.
- **Edit the template, don't regenerate it.** A resume template carries decisions about spacing, typography and order. Surgical span edits keep those decisions intact, and they keep diffs against the original minimal and reviewable.
- **Operations, not rewrites.** The editor and its assistant work on a structured document through small typed operations. That makes "change only this" enforceable, and it makes undo, history and conflict detection straightforward.
- **The human stays in control of submission.** Form filling runs where the user can see it, and final submission requires an explicit confirmation that the server enforces.
