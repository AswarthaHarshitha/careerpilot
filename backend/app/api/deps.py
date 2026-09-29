from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_matches,
    decode_runner_access,
    decode_session,
    hash_token,
)
from app.models import ApiToken, AuthSession, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _session_from_cookie(request: Request, db: Session) -> tuple[User, AuthSession]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized()
    claims = decode_session(token)
    if not claims:
        raise Unauthorized("Your session has expired. Please sign in again.")
    session = db.get(AuthSession, claims.get("sid"))
    now = datetime.now(UTC)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise Unauthorized("Your session has expired. Please sign in again.")
    if session.user_id != claims.get("uid"):
        raise Unauthorized()
    user = db.get(User, session.user_id)
    if user is None:
        raise Unauthorized()
    return user, session


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    _, session = _session_from_cookie(request, db)
    return session


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, session = _session_from_cookie(request, db)
    if request.method not in SAFE_METHODS and not csrf_matches(session.csrf_token, request.headers.get(CSRF_HEADER)):
        raise Forbidden("Security check failed. Reload the page and try again.")
    request.state.user_id = user.id
    return user


def _no_browser_origin(request: Request) -> None:
    # The runner is a local program; a request carrying a browser Origin is a web page trying to act as it.
    if request.headers.get("origin"):
        raise Forbidden("Runner endpoints can't be called from a web page.")


def runner_from_token(request: Request, db: Session = Depends(get_db)) -> tuple[User, ApiToken]:
    """Authenticates the long-lived runner token. Used only to obtain a short-lived access token."""
    _no_browser_origin(request)
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise Unauthorized("Runner token missing.")
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(header[7:].strip())))
    if token is None or token.revoked_at is not None:
        raise Unauthorized("Runner token is invalid or revoked.")
    user = db.get(User, token.user_id)
    if user is None:
        raise Unauthorized()
    token.last_used_at = token.runner_seen_at = datetime.now(UTC)
    db.commit()
    return user, token


def current_runner(request: Request, db: Session = Depends(get_db)) -> tuple[User, ApiToken]:
    """Authenticates the apply agent by its short-lived access token and records that it is online."""
    _no_browser_origin(request)
    header = request.headers.get("Authorization", "")
    claims = decode_runner_access(header[7:].strip()) if header.startswith("Bearer ") else None
    if claims is None:
        raise Unauthorized("Runner access token missing or expired.")
    token = db.get(ApiToken, claims.get("tid"))
    if token is None or token.revoked_at is not None or token.user_id != claims.get("uid"):
        raise Unauthorized("Runner token is invalid or revoked.")
    user = db.get(User, token.user_id)
    if user is None:
        raise Unauthorized()
    token.last_used_at = token.runner_seen_at = datetime.now(UTC)
    db.commit()
    return user, token
