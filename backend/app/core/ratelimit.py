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
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")


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
