"""A small database-backed task queue.

Long-running work (GitHub scans, job analysis, resume generation) is recorded as a BackgroundTask row and
executed by worker threads inside the API process. The UI polls the task for per-step progress. Using the
database avoids a separate broker while still surviving restarts: tasks left "running" by a crashed process
are re-queued on startup.
"""

import logging
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.errors import AppError
from app.llm.providers import prefer_light
from app.models import BackgroundTask

log = logging.getLogger(__name__)

Handler = Callable[["TaskContext"], dict | None]
HANDLERS: dict[str, Handler] = {}
# Work nobody is waiting on. It runs after anything the user asked for, so a job analysis never sits behind a
# GitHub scan of many repositories.
BACKGROUND_KINDS = ("github_scan_repo", "interview_prep", "company_research")
MAX_ATTEMPTS = 3


def task_handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


class RetryLater(Exception):
    """Raised by a handler to re-queue the task (e.g. an external rate limit)."""

    def __init__(self, message: str, not_before: datetime | None = None):
        super().__init__(message)
        self.not_before = not_before


def enqueue(db: Session, user_id: int, kind: str, payload: dict, steps: list[str]) -> BackgroundTask:
    task = BackgroundTask(
        user_id=user_id,
        kind=kind,
        payload=payload,
        steps=[{"label": s, "state": "pending"} for s in steps],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _wake.set()
    return task


class TaskContext:
    def __init__(self, db: Session, task: BackgroundTask):
        self.db = db
        self.task = task

    @property
    def payload(self) -> dict:
        return self.task.payload or {}

    def _set_step(self, label: str, state: str, detail: str = "") -> None:
        steps = [dict(s) for s in (self.task.steps or [])]
        for s in steps:
            if s["label"] == label:
                s["state"] = state
                if detail:
                    s["detail"] = detail
                break
        else:
            steps.append({"label": label, "state": state, **({"detail": detail} if detail else {})})
        self.task.steps = steps
        self.db.commit()

    @contextmanager
    def step(self, label: str):
        self._set_step(label, "running")
        started = time.monotonic()
        try:
            yield
        except Exception:
            self.db.rollback()
            self._set_step(label, "failed")
            raise
        finally:
            log.info("task %s %s: %s %.1fs", self.task.id, self.task.kind, label, time.monotonic() - started)
        self._set_step(label, "done")

    def note(self, label: str, detail: str) -> None:
        self._set_step(label, "done", detail)

    def skip(self, label: str, detail: str = "") -> None:
        self._set_step(label, "skipped", detail)


def _claim_next(db: Session) -> BackgroundTask | None:
    now = datetime.now(UTC)
    stmt = (
        select(BackgroundTask)
        .where(BackgroundTask.status == "queued", BackgroundTask.created_at <= now)
        .order_by(case((BackgroundTask.kind.in_(BACKGROUND_KINDS), 1), else_=0), BackgroundTask.created_at)
        .limit(1)
    )
    if not get_settings().is_sqlite:
        stmt = stmt.with_for_update(skip_locked=True)
    task = db.scalar(stmt)
    if task is None:
        db.rollback()
        return None
    res = db.execute(
        update(BackgroundTask)
        .where(BackgroundTask.id == task.id, BackgroundTask.status == "queued")
        .values(status="running", started_at=now, attempts=BackgroundTask.attempts + 1)
    )
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(task)
    return task


def run_task(db: Session, task: BackgroundTask) -> None:
    handler = HANDLERS.get(task.kind)
    ctx = TaskContext(db, task)
    try:
        if handler is None:
            raise AppError(f"Unknown task type {task.kind}.")
        light = prefer_light.set(task.kind in BACKGROUND_KINDS)
        try:
            result = handler(ctx) or {}
        finally:
            prefer_light.reset(light)
        task.status = "succeeded"
        task.result = result
        task.error = ""
    except RetryLater as exc:
        db.rollback()
        task.status = "queued"
        task.error = str(exc)
        # created_at doubles as "not before" for re-queued tasks
        task.created_at = exc.not_before or (datetime.now(UTC) + timedelta(minutes=5))
    except AppError as exc:
        db.rollback()
        task.status = "failed"
        task.error = exc.message
        task.result = {"error": exc.to_dict()}
    except Exception as exc:  # noqa: BLE001 - worker must never die
        db.rollback()
        log.error("task %s (%s) crashed: %s", task.id, task.kind, "".join(traceback.format_exception_only(exc)).strip())
        task.status = "failed"
        task.error = "Something went wrong while processing this. Please retry."
        task.result = {"error": {"code": "internal_error", "message": task.error, "hint": "", "actions": ["retry"]}}
    task.finished_at = datetime.now(UTC) if task.status != "queued" else None
    db.commit()
    for cb in list(_after_hooks):
        try:
            cb(db, task)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("post-task hook failed")


_after_hooks: list[Callable[[Session, BackgroundTask], None]] = []


def after_task(fn):
    _after_hooks.append(fn)
    return fn


def run_pending_tasks(max_tasks: int = 100) -> int:
    """Processes queued tasks synchronously (used by tests and one-off scripts)."""
    done = 0
    while done < max_tasks:
        db = session_factory()()
        try:
            task = _claim_next(db)
            if task is None:
                return done
            run_task(db, task)
            done += 1
        finally:
            db.close()
    return done


def recover_stuck_tasks() -> None:
    db = session_factory()()
    try:
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        stuck = db.scalars(
            select(BackgroundTask).where(BackgroundTask.status == "running", BackgroundTask.started_at < cutoff)
        ).all()
        for t in stuck:
            if t.attempts >= MAX_ATTEMPTS:
                t.status = "failed"
                t.error = "This task was interrupted repeatedly. Please retry."
            else:
                t.status = "queued"
        db.commit()
    finally:
        db.close()


_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []


def _worker_loop(n: int) -> None:
    poll = get_settings().worker_poll_seconds
    while not _stop.is_set():
        db = session_factory()()
        try:
            task = _claim_next(db)
            if task is not None:
                run_task(db, task)
                continue
        except Exception:  # noqa: BLE001
            log.exception("worker %s loop error", n)
            time.sleep(poll)
        finally:
            db.close()
        _wake.wait(poll)
        _wake.clear()


def start_workers() -> None:
    settings = get_settings()
    if not settings.worker_enabled or _threads:
        return
    recover_stuck_tasks()
    _stop.clear()
    for n in range(settings.worker_threads):
        t = threading.Thread(target=_worker_loop, args=(n,), name=f"cp-worker-{n}", daemon=True)
        t.start()
        _threads.append(t)


def stop_workers() -> None:
    _stop.set()
    _wake.set()
    for t in _threads:
        t.join(timeout=5)
    _threads.clear()
