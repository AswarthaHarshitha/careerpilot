from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.core.security import decrypt_secret, encrypt_secret, mask_secret, new_runner_token
from app.github.client import GitHubClient, parse_username
from app.llm import router as ai
from app.llm.provider import LLMError
from app.llm.providers import openrouter_key_status
from app.models import AIUsage, ApiToken, User, UserSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def ai_out(s: UserSettings) -> dict:
    available = ai.configured(s)
    chain, note = ai.plan(s)
    personal = {"gemini": s.gemini_key_encrypted, "openrouter": s.openrouter_key_encrypted}
    cfg = get_settings()
    models = {"gemini": cfg.gemini_model, "openrouter": cfg.openrouter_model, "ollama": cfg.ollama_model}
    providers = []
    for p in ai.PROVIDERS:
        c = available.get(p)
        providers.append(
            {
                "id": p,
                "label": ai.LABELS[p],
                "configured": c is not None,
                "source": c.source if c else "",
                "external": p != "ollama",
                "your_key": mask_secret(decrypt_secret(personal.get(p))) if p in personal else "",
                "model": models[p] or "automatic",
                "base_url": cfg.ollama_base_url if p == "ollama" else "",
            }
        )
    return {
        "provider": s.ai_provider,
        "fallback_enabled": s.ai_fallback_enabled,
        "privacy_mode": s.ai_privacy_mode,
        "active_order": [ai.LABELS[c.provider] for c in chain],
        "note": note,
        "providers": providers,
    }


def settings_out(s: UserSettings) -> dict:
    return {
        "default_template_id": s.default_template_id,
        "resume_max_pages": s.resume_max_pages,
        "max_projects": s.max_projects,
        "max_bullets_per_project": s.max_bullets_per_project,
        "timezone": s.timezone,
        "submit_confirmation_mode": s.submit_confirmation_mode,
        "deadline_reminder_days": s.deadline_reminder_days,
        "follow_up_after_days": s.follow_up_after_days,
        "notify_in_app": s.notify_in_app,
        "header_portfolio": s.header_portfolio,
        "header_github": s.header_github,
        "header_linkedin": s.header_linkedin,
        "ai": ai_out(s),
        "onboarded": s.onboarded_at is not None,
        "github": {
            "username": s.github_username,
            "token": mask_secret(decrypt_secret(s.github_token_encrypted)),
            "include_private_repos": s.include_private_repos,
            "include_forks": s.include_forks,
            "last_scan_at": s.github_last_scan_at.isoformat() if s.github_last_scan_at else None,
            "server_token": bool(get_settings().github_token),
        },
    }


@router.get("")
def get_user_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return settings_out(db.get(UserSettings, user.id))


class PrefsIn(BaseModel):
    resume_max_pages: int = Field(ge=1, le=3)
    max_projects: int = Field(ge=1, le=6)
    max_bullets_per_project: int = Field(ge=1, le=6)
    timezone: str = Field(max_length=64)
    submit_confirmation_mode: Literal["type_company", "click"]
    deadline_reminder_days: int = Field(ge=0, le=30)
    follow_up_after_days: int = Field(ge=1, le=60)
    notify_in_app: bool = True
    header_portfolio: Literal["template", "include", "exclude"] = "template"
    header_github: Literal["template", "include", "exclude"] = "template"
    header_linkedin: Literal["template", "include", "exclude"] = "template"


@router.put("/preferences")
def update_prefs(body: PrefsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return settings_out(s)


class AIIn(BaseModel):
    provider: Literal["auto", "gemini", "openrouter", "ollama", "none"]
    fallback_enabled: bool = True
    privacy_mode: bool = False
    # None keeps the stored key; "" removes it. Keys are write-only: responses only ever show a masked form.
    gemini_key: str | None = Field(default=None, max_length=500)
    openrouter_key: str | None = Field(default=None, max_length=500)


@router.put("/ai")
def update_ai(body: AIIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    s.ai_provider, s.ai_fallback_enabled, s.ai_privacy_mode = body.provider, body.fallback_enabled, body.privacy_mode
    if body.gemini_key is not None:
        s.gemini_key_encrypted = encrypt_secret(body.gemini_key.strip()) if body.gemini_key.strip() else None
    if body.openrouter_key is not None:
        s.openrouter_key_encrypted = (
            encrypt_secret(body.openrouter_key.strip()) if body.openrouter_key.strip() else None
        )
    db.commit()
    return settings_out(s)


class AITestIn(BaseModel):
    provider: Literal["gemini", "openrouter", "ollama"]


@router.post("/ai/test")
def test_ai(body: AITestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    provider = ai.single(s, body.provider)
    label = ai.LABELS[body.provider]
    if provider is None:
        hint = {
            "gemini": "Add a Gemini API key here or set GEMINI_API_KEY on the server.",
            "openrouter": "Add an OpenRouter API key here or set OPENROUTER_API_KEY on the server.",
            "ollama": "Install Ollama next to the API and set OLLAMA_BASE_URL (for example http://localhost:11434).",
        }[body.provider]
        raise AppError(f"{label} is not configured.", code="not_configured", hint=hint)
    started = datetime.now(UTC)
    try:
        data = provider.complete_json('Reply with JSON {"ok": true}.', "Connection test.", max_tokens=200)
    except LLMError as exc:
        raise AppError(f"{label}: {exc}", code="provider_error") from exc
    ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    return {"ok": bool(data.get("ok")), "provider": label, "latency_ms": ms}


@router.get("/ai/usage")
def ai_usage(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Application-side request counts (what CareerPilot sent), plus figures a provider reports itself."""
    s = db.get(UserSettings, user.id)
    now = datetime.now(UTC)
    today, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    rows = db.scalars(select(AIUsage).where(AIUsage.user_id == user.id, AIUsage.day.like(month + "%"))).all()
    chain, note = ai.plan(s)
    available = ai.configured(s)
    out = []
    for p in ai.PROVIDERS:
        mine = [r for r in rows if r.provider == p]
        last_err = max((r for r in mine if r.last_error_at), key=lambda r: r.last_error_at, default=None)
        c = available.get(p)
        cooling = max(0, int(ai.cooling_until(c) - now.timestamp())) if c else 0
        entry = {
            "id": p,
            "label": ai.LABELS[p],
            "configured": c is not None,
            "requests_today": sum(r.requests for r in mine if r.day == today),
            "requests_month": sum(r.requests for r in mine),
            "rate_limited_month": sum(r.rate_limited for r in mine),
            "failed_month": sum(r.failed for r in mine),
            "last_error": last_err.last_error if last_err else "",
            "last_error_at": last_err.last_error_at.isoformat() if last_err else None,
            "cooling_down_seconds": cooling,
            "reported": None,
        }
        if p == "openrouter" and c is not None:
            entry["reported"] = openrouter_key_status(c.cfg.api_key)
        out.append(entry)
    return {
        "current_provider": ai.LABELS[chain[0].provider] if chain else "None (deterministic processing)",
        "fallback": (
            "Off"
            if not s.ai_fallback_enabled
            else " → ".join(ai.LABELS[c.provider] for c in chain)
            if len(chain) > 1
            else "On, but no other permitted provider is configured"
        ),
        "privacy_mode": s.ai_privacy_mode,
        "note": note,
        "providers": out,
    }


@router.post("/onboarding/complete")
def complete_onboarding(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    s.onboarded_at = s.onboarded_at or datetime.now(UTC)
    db.commit()
    return settings_out(s)


class GitHubIn(BaseModel):
    username: str = Field(max_length=200)
    token: str | None = Field(default=None, max_length=300)
    include_private_repos: bool = False
    include_forks: bool = False


@router.put("/github")
def update_github(body: GitHubIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    username = parse_username(body.username) if body.username.strip() else ""
    token = decrypt_secret(s.github_token_encrypted) if body.token is None else body.token.strip()
    if username:
        gh = GitHubClient(token=token)
        if gh.get_user(username) is None:
            raise AppError(f"GitHub user '{username}' was not found.", code="github_user_not_found")
    if body.include_private_repos and not token:
        raise AppError("Private repositories need a GitHub token with read access.", code="token_required")
    s.github_username = username
    if body.token is not None:
        s.github_token_encrypted = encrypt_secret(token) if token else None
    s.include_private_repos, s.include_forks = body.include_private_repos, body.include_forks
    db.commit()
    return settings_out(s)


# --- runner tokens --------------------------------------------------------------------------------


@router.get("/runner-tokens")
def list_tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return [
        {
            "id": t.id,
            "name": t.name,
            "prefix": t.prefix,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        }
        for t in rows
    ]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/runner-tokens")
def create_token(body: TokenIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    active = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))).all()
    if len(active) >= 10:
        raise AppError("Revoke an old token first (limit 10).", code="too_many_tokens")
    raw, hashed, prefix = new_runner_token()
    t = ApiToken(user_id=user.id, name=body.name.strip(), token_hash=hashed, prefix=prefix)
    db.add(t)
    db.commit()
    return {
        "id": t.id,
        "name": t.name,
        "prefix": prefix,
        "token": raw,
        "note": "Copy this token now; it is not shown again.",
    }


@router.delete("/runner-tokens/{tid}")
def revoke_token(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ApiToken, tid)
    if t is None or t.user_id != user.id:
        raise NotFound("Token")
    t.revoked_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
