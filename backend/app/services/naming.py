"""Professional file names for exported resumes: First_Last_Resume_Company_Role.pdf"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, ResumeVersion, UserProfile

_BANNED = re.compile(r"\b(ai|generated|draft|final|copy|version|v\d+)\b", re.I)


def _part(s: str) -> str:
    s = _BANNED.sub(" ", s or "")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:40]


def resume_filename(db: Session, v: ResumeVersion, ext: str) -> str:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == v.user_id))
    name = ""
    if profile:
        name = "_".join(x for x in (_part(profile.first_name), _part(profile.last_name)) if x) or _part(profile.full_name)
    job = db.get(Job, v.job_id) if v.job_id else None
    parts = [name or "Resume", "Resume" if name else ""]
    if job:
        parts += [_part(job.company), _part(job.role_title)]
    return "_".join(p for p in parts if p) + f".{ext}"
