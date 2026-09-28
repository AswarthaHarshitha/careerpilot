.PHONY: setup dev-api dev-web test test-backend test-web e2e lint build runner

setup:
	cd backend && uv sync && uv run playwright install chromium
	cd frontend && npm ci

dev-api:
	cd backend && mkdir -p var && uv run alembic upgrade head && uv run uvicorn app.main:app --reload --port 8010

dev-web:
	cd frontend && API_URL=http://127.0.0.1:8010 npm run dev

test: test-backend test-web

test-backend:
	cd backend && uv run pytest

test-web:
	cd frontend && npm test

e2e:
	cd backend && uv run python ../e2e/run_e2e.py

lint:
	cd backend && uv run ruff check app tests
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build
	docker build --platform linux/amd64 -t careerpilot-api ./backend

runner:
	cd backend && uv run careerpilot-runner --api $${CAREERPILOT_API:-http://localhost:5173} --token $${CAREERPILOT_TOKEN}
