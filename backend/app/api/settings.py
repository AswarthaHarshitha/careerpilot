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
from app.core.fetch import validate_url
from app.core.security import decrypt_secret, encrypt_secret, mask_secret, new_runner_token
from app.github.client import GitHubClient, parse_username
from app.llm.provider import LLMError, get_provider, resolve_config
from app.models import ApiToken, User, UserSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def settings_out(s: UserSettings) -> dict:
    server = resolve_config(None)
    return {
        "default_template_id": s.default_template_id, "resume_max_pages": s.resume_max_pages,
        "max_projects": s.max_projects, "max_bullets_per_project": s.max_bullets_per_project, "timezone": s.timezone,
        "submit_confirmation_mode": s.submit_confirmation_mode, "deadline_reminder_days": s.deadline_reminder_days,
        "follow_up_after_days": s.follow_up_after_days, "notify_in_app": s.notify_in_app,
        "header_portfolio": s.header_portfolio, "header_github": s.header_github, "header_linkedin": s.header_linkedin,
        "ai": {"provider": s.llm_provider, "model": s.llm_model, "base_url": s.llm_base_url,
               "api_key": mask_secret(decrypt_secret(s.llm_api_key_encrypted)),
               "server_provider": server.provider if server else "none",
               "server_model": server.model if server else ""},
        "github": {"username": s.github_username, "token": mask_secret(decrypt_secret(s.github_token_encrypted)),
                   "include_private_repos": s.include_private_repos, "include_forks": s.include_forks,
                   "last_scan_at": s.github_last_scan_at.isoformat() if s.github_last_scan_at else None,
                   "server_token": bool(get_settings().github_token)},
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
    provider: Literal["server", "none", "gemini", "openai_compatible"]
    model: str = Field(default="", max_length=120)
    base_url: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # None keeps the stored key; "" clears it


@router.put("/ai")
def update_ai(body: AIIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(UserSettings, user.id)
    if body.base_url:
        validate_url(body.base_url)
    s.llm_provider, s.llm_model, s.llm_base_url = body.provider, body.model.strip(), body.base_url.strip()
    if body.api_key is not None:
        s.llm_api_key_encrypted = encrypt_secret(body.api_key.strip()) if body.api_key.strip() else None
    db.commit()
    return settings_out(s)


@router.post("/ai/test")
def test_ai(user: User = Depends(current_user), db: Session = Depends(get_db)):
    provider = get_provider(db.get(UserSettings, user.id))
    if provider is None:
        raise AppError("No AI provider is configured. CareerPilot works without one, using deterministic analysis.",
                       code="no_provider")
    try:
        data = provider.complete_json("Reply with JSON {\"ok\": true}.", "Connection test.", max_tokens=20)
    except LLMError as exc:
        raise AppError(str(exc), code="provider_error") from exc
    return {"ok": bool(data.get("ok")), "provider": provider.name}


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
    rows = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
                      .order_by(ApiToken.created_at.desc()))
    return [{"id": t.id, "name": t.name, "prefix": t.prefix, "created_at": t.created_at.isoformat(),
             "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None} for t in rows]


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
    return {"id": t.id, "name": t.name, "prefix": prefix, "token": raw,
            "note": "Copy this token now; it is not shown again."}


@router.delete("/runner-tokens/{tid}")
def revoke_token(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ApiToken, tid)
    if t is None or t.user_id != user.id:
        raise NotFound("Token")
    t.revoked_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
