# CareerPilot API: FastAPI + background workers + Tectonic (LaTeX) for resume rendering.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    XDG_CACHE_HOME=/home/app/.cache \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl fontconfig tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

ARG TECTONIC_VERSION=0.17.0
RUN curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin tectonic \
 && tectonic --version

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra automation --no-install-project \
 && .venv/bin/playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/*

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY tests/fixtures/templates/jake_style.tex /tmp/warm.tex
RUN uv sync --frozen --no-dev --extra automation && chown -R app:app /app \
 && mkdir -p /home/app/.cache /home/app/work && chown -R app:app /home/app /tmp/warm.tex

USER app
# Download the TeX packages a typical resume uses now, so the first real compile is fast.
RUN cd /home/app/work \
 && { printf '%s\n' '\ifdefined\pdfglyphtounicode\else\def\pdfglyphtounicode#1#2{}\fi' \
                     '\ifdefined\pdfgentounicode\else\newcount\pdfgentounicode\fi'; cat /tmp/warm.tex; } > w.tex \
 && tectonic -X compile --untrusted w.tex \
 && test -f w.pdf && rm -f w.* /tmp/warm.tex

ENV PATH="/app/.venv/bin:$PATH" PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
