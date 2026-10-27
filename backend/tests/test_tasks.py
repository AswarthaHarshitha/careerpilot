"""Task queue ordering."""

from sqlalchemy import select

from app.core.db import session_factory
from app.models import User
from app.tasks.queue import _claim_next, enqueue


def test_user_requested_work_runs_before_background_scans(auth_client):
    db = session_factory()()
    try:
        user_id = db.scalar(select(User.id))
        for n in range(3):
            enqueue(db, user_id, "github_scan_repo", {"project_id": n}, ["Reading repository"])
        enqueue(db, user_id, "job_analyze", {"job_id": 1}, ["Analysing"])
        assert _claim_next(db).kind == "job_analyze"
        assert _claim_next(db).kind == "github_scan_repo"
    finally:
        db.close()
