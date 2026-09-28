import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_session, current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound, Unauthorized
from app.core.ratelimit import limit
from app.core.security import (
    SESSION_COOKIE,
    burn_password_check,
    encode_session,
    hash_password,
    new_session_id,
    session_expiry,
    validate_password_strength,
    verify_password,
)
from app.models import AuthSession, User, UserProfile, UserSettings

router = APIRouter(prefix="/api/auth", tags=["auth"])
MAX_FAILED = 8
LOCK_MINUTES = 15


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


def _user_out(u: User, session: AuthSession) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "csrf_token": session.csrf_token,
            "created_at": u.created_at.isoformat()}


def _start_session(db: Session, response: Response, request: Request, user: User) -> AuthSession:
    sid = new_session_id()
    expires = session_expiry()
    session = AuthSession(id=sid, user_id=user.id, expires_at=expires, csrf_token=secrets.token_urlsafe(24),
                          user_agent=(request.headers.get("user-agent") or "")[:300])
    db.add(session)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    settings = get_settings()
    response.set_cookie(SESSION_COOKIE, encode_session(sid, user.id, expires), httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/",
                        max_age=settings.session_ttl_hours * 3600)
    return session


@router.get("/state")
def auth_state(db: Session = Depends(get_db)):
    users = db.scalar(select(func.count(User.id))) or 0
    return {"needs_setup": users == 0, "registration_open": users == 0 or get_settings().allow_registration}


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limit(request, "register", 5, 3600)
    users = db.scalar(select(func.count(User.id))) or 0
    if users > 0 and not get_settings().allow_registration:
        raise AppError("Registration is closed. This CareerPilot instance already has its owner.",
                       code="registration_closed", status_code=403)
    problem = validate_password_strength(body.password)
    if problem:
        raise AppError(problem, code="weak_password")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise AppError("An account with this email already exists.", code="email_taken", status_code=409)
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name.strip())
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id))
    db.add(UserProfile(user_id=user.id, email=email, full_name=body.display_name.strip()))
    db.commit()
    session = _start_session(db, response, request, user)
    return _user_out(user, session)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limit(request, "login", 10, 60)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(UTC)
    if user is None:
        burn_password_check(body.password)
        raise Unauthorized("Email or password is incorrect.")
    if user.locked_until and user.locked_until > now:
        raise AppError("Too many failed attempts. Try again in a few minutes.", code="locked", status_code=429)
    if not verify_password(user.password_hash, body.password):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_login_count = 0
        db.commit()
        raise Unauthorized("Email or password is incorrect.")
    user.failed_login_count = 0
    user.locked_until = None
    session = _start_session(db, response, request, user)
    return _user_out(user, session)


@router.post("/logout")
def logout(response: Response, session: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    s = db.get(AuthSession, session.id)
    s.revoked_at = datetime.now(UTC)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user), session: AuthSession = Depends(current_session)):
    return _user_out(user, session)


@router.post("/password")
def change_password(body: PasswordIn, user: User = Depends(current_user),
                    session: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    if not verify_password(user.password_hash, body.current_password):
        raise AppError("Your current password is incorrect.", code="wrong_password", status_code=400)
    problem = validate_password_strength(body.new_password)
    if problem:
        raise AppError(problem, code="weak_password")
    user.password_hash = hash_password(body.new_password)
    now = datetime.now(UTC)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != session.id,
                                                  AuthSession.revoked_at.is_(None))):
        s.revoked_at = now
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def list_sessions(user: User = Depends(current_user), session: AuthSession = Depends(current_session),
                  db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
                                                AuthSession.expires_at > now).order_by(AuthSession.created_at.desc()))
    return [{"id": s.id[:8], "created_at": s.created_at.isoformat(), "user_agent": s.user_agent,
             "current": s.id == session.id} for s in rows]


@router.delete("/sessions/{prefix}")
def revoke_session(prefix: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id.startswith(prefix[:8]),
                                            AuthSession.revoked_at.is_(None)))
    if s is None or len(prefix) < 8:
        raise NotFound("Session")
    s.revoked_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
