# Setting up CareerPilot

CareerPilot runs entirely on free and open-source components. You need Python, Node, a database (SQLite works locally) and, optionally, one AI provider. Every credential goes into `backend/.env` or your hosting provider's environment settings. Never put credentials in source files, READMEs, issues, commits or chat messages. `.env` files are git-ignored.

```bash
cp .env.example backend/.env
```

## Required

### 1. Tools

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node 20.19+ and npm
- [Tectonic](https://tectonic-typesetting.github.io/en-US/install.html) for LaTeX resumes (`brew install tectonic` on macOS)

```bash
make setup      # Python and Node dependencies, Playwright Chromium
```

### 2. Secrets for sign-in and stored keys

Local development works without these. In production both are required:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print('SECRET_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

`SECRET_ENCRYPTION_KEY` encrypts the API keys and tokens you save in Settings. Keep it stable: if it changes, those saved keys can no longer be read and have to be entered again.

### 3. Gemini API key (recommended AI provider)

1. Open [Google AI Studio](https://aistudio.google.com/apikey) and sign in.
2. Choose **Create API key** and copy it.
3. Set `GEMINI_API_KEY=` in `backend/.env`.

Leave `GEMINI_MODEL` empty. CareerPilot then picks the current Flash model from Google's model list, and moves to the lighter model when Flash is overloaded. The free tier has per-minute and per-day limits. When one is reached, CareerPilot waits briefly and retries, then falls back to the next configured provider if you allow fallback.

### 4. GitHub token

The token is optional for public repositories, but it raises GitHub's rate limit from 60 to 5,000 requests an hour.

1. On GitHub, open **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Repository access** to *Public repositories*. For private ones, choose *Only select repositories* and pick them.
3. Under **Permissions**, set *Contents: Read-only*. *Metadata: Read-only* is added automatically. Grant nothing else.
4. Set `GITHUB_TOKEN=` in `backend/.env`, or save it under **Settings → GitHub** in the app.

The token is used only by the API. Repositories are read through the GitHub API and never cloned. Private repositories are read only if you turn on *Include private repositories*. Scan results are cached: a repository is read again only when it changed, after 30 days, or when you ask for a rescan.

### 5. Database

- **Local:** the default `DATABASE_URL=sqlite:///./var/careerpilot.db` needs no setup.
- **Local PostgreSQL:** run `docker compose up db` and use `postgresql://careerpilot:careerpilot@localhost:5433/careerpilot`.
- **Supabase (free tier):**
  1. In your Supabase project, choose **Connect** and copy the **Session pooler** connection string. The direct `db.<project>.supabase.co` host is IPv6-only, and many hosts, including Render, cannot reach it.
  2. URL-encode special characters in the password. For example, `@` becomes `%40` and `#` becomes `%23`.
- **Other PostgreSQL:** Neon and any standard provider work the same way. The application uses only standard PostgreSQL.

Migrations run automatically when the API starts in Docker. Locally, `make dev-api` runs them.

### 6. Start

```bash
make dev-api    # API on http://127.0.0.1:8010
make dev-web    # web app on http://localhost:5173
```

Open http://localhost:5173 and create your account. The first account becomes the owner, and registration then closes. A setup wizard walks through the AI provider, GitHub and templates.

## Optional

### OpenRouter (fallback AI provider)

1. Create a key at [openrouter.ai/keys](https://openrouter.ai/keys). Free models need no credits.
2. Set `OPENROUTER_API_KEY=`, or save the key under **Settings → AI providers**.

Leave `OPENROUTER_MODEL` empty. CareerPilot then uses OpenRouter's free-model router, or a free model from the current list. Free usage has a daily request limit. **Settings → AI providers** shows the figure OpenRouter reports for your key next to CareerPilot's own request counts.

### Ollama (local model)

1. Install [Ollama](https://ollama.com/download), then pull a model, for example `ollama pull llama3.2`.
2. Set `OLLAMA_BASE_URL=http://localhost:11434`. `OLLAMA_MODEL` is optional; by default the first installed model is used.

Ollama must be reachable from the machine running the API, so it suits local use. **Privacy mode** (Settings → AI providers) sends resume and profile content only to Ollama and never to an external provider.

### Provider order and fallback

`AI_PROVIDER_ORDER=gemini,openrouter,ollama` sets the default order. In **Settings → AI providers** you can:

- pick one provider
- turn fallback on or off
- turn privacy mode on
- test each connection

Structural editor commands never use an AI provider: removing a link, moving a project, deleting a section, changing the page target, and undo/redo all run locally.

### OCR for scanned PDFs

PDF text is extracted locally with PyMuPDF. For scanned PDFs without a text layer, install [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) (`brew install tesseract`), and CareerPilot will OCR those pages.

### Apply agent

Form filling runs on your own computer so that you can sign in, solve CAPTCHAs and watch every step. In the app, create a runner token under **Settings → Browser automation**, then run:

```bash
cd backend
uv sync --extra automation && uv run playwright install chromium
uv run careerpilot-runner --api https://your-app.example --token <token>
```

### Deployment credentials

| Service | Where to create it | Used for |
| --- | --- | --- |
| Render | Dashboard → Account settings → API keys | API service (Docker, `render.yaml`) |
| Vercel | Account settings → Tokens | Web app (`frontend/`) |
| Supabase | Project → Connect → Session pooler | PostgreSQL |

On Render, set these environment variables:

- `DATABASE_URL`
- `SECRET_KEY`
- `SECRET_ENCRYPTION_KEY`
- `COOKIE_SECURE=true`
- `PUBLIC_WEB_URL`
- `CORS_ORIGINS`
- the AI keys you use

On Vercel, set the project root to `frontend/`. `vercel.json` rewrites `/api/*` to the Render service, so the web app needs no environment variables and never holds a secret.
