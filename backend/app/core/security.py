import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_hasher = PasswordHasher()
SESSION_COOKIE = "cp_session"
CSRF_COOKIE = "cp_csrf"
CSRF_HEADER = "X-CSRF-Token"
RUNNER_TOKEN_PREFIX = "cpr_"  # noqa: S105 - a public token prefix, not a secret


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# A dummy hash so login timing does not reveal whether an email exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def validate_password_strength(password: str) -> str | None:
    if len(password) < 12:
        return "Use at least 12 characters."
    if len(password) > 256:
        return "Use at most 256 characters."
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        return "Mix upper- and lower-case letters and at least one digit."
    return None


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def encode_session(session_id: str, user_id: int, expires_at: datetime) -> str:
    settings = get_settings()
    return jwt.encode(
        {"sid": session_id, "uid": user_id, "exp": int(expires_at.timestamp())}, settings.jwt_secret, "HS256"
    )


def decode_session(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def session_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=get_settings().session_ttl_hours)


def csrf_matches(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected, provided)


def new_runner_token() -> tuple[str, str, str]:
    """Returns (plaintext token shown once, sha256 hash to store, short display prefix)."""
    raw = RUNNER_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:10]


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.secret_encryption_key
    if not key:
        # Development/test fallback derived from the JWT secret; production refuses to start without a real key.
        key = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + settings.jwt_secret).encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_secret(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt_secret(blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob).decode()
    except InvalidToken:
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "•" * 6 + value[-4:] if len(value) > 8 else "•" * len(value)
