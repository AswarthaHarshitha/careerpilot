"""In-process sliding-window rate limiting for sensitive endpoints (login, registration, imports).

Single-instance deployments only; the account lockout in the auth router is the durable second layer.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.errors import AppError

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """The caller's address as seen by our own proxies. The first X-Forwarded-For entry is whatever the client
    sent, so it is never trusted: use the header Vercel sets itself, else the entry the nearest proxy appended."""
    vercel = request.headers.get("x-vercel-forwarded-for", "").split(",")[0].strip()
    if vercel:
        return vercel
    fwd = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    if fwd:
        return fwd[-1]
    return request.client.host if request.client else "unknown"


def limit(request: Request, bucket: str, max_hits: int, window_seconds: int) -> None:
    key = f"{bucket}:{client_ip(request)}"
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= max_hits:
            raise AppError("Too many attempts. Wait a minute and try again.", code="rate_limited", status_code=429)
        q.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
