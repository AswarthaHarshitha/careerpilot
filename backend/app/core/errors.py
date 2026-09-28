"""Errors that carry a message a person can act on.

API responses for failures always have the shape
{"error": {"code": ..., "message": ..., "hint": ..., "actions": [...]}} so the frontend can show what went wrong,
the likely reason, and what to do next.
"""


class AppError(Exception):
    status_code = 400

    def __init__(
        self,
        message: str,
        *,
        code: str = "bad_request",
        hint: str = "",
        actions: list[str] | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.actions = actions or []
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint, "actions": self.actions}


class NotFound(AppError):
    status_code = 404

    def __init__(self, what: str = "Item"):
        super().__init__(f"{what} not found.", code="not_found")


class Unauthorized(AppError):
    status_code = 401

    def __init__(self, message: str = "Please sign in to continue."):
        super().__init__(message, code="unauthorized")


class Forbidden(AppError):
    status_code = 403

    def __init__(self, message: str = "You do not have access to this."):
        super().__init__(message, code="forbidden")


class Conflict(AppError):
    status_code = 409

    def __init__(self, message: str, **kw):
        super().__init__(message, code=kw.pop("code", "conflict"), **kw)


class ExternalFetchError(AppError):
    """A page or API outside CareerPilot could not be read."""

    status_code = 422

    def __init__(self, message: str, hint: str = "", actions: list[str] | None = None):
        super().__init__(message, code="fetch_failed", hint=hint, actions=actions or ["retry", "paste_description"])
