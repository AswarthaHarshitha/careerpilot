"""Moving a career profile between CareerPilot instances (for example from a laptop to the hosted site).

The export's "portable" section holds what a person would otherwise retype: profile, experience, education,
skills, certifications, achievements, saved answers, preferences, manually added projects and the resume
template files. Importing merges it into the signed-in account: empty fields are filled, existing entries are
kept, nothing is duplicated. GitHub projects are not copied - they are re-read from GitHub, so their evidence is
verified again on the new instance. Jobs, applications and generated resumes stay where they were made.
"""

import base64
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.matching.taxonomy import canonicalize
from app.models import (
    Achievement,
    Certification,
    Education,
    Experience,
    ExperienceBullet,
    ProfileAnswer,
    Project,
    ProjectBullet,
    ResumeTemplate,
    Skill,
    UserProfile,
    UserSettings,
)

VERSION = 1
PROFILE_FIELDS = (
    "full_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "location",
    "street",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "headline",
    "summary",
    "work_authorization",
    "requires_sponsorship",
    "willing_to_relocate",
    "notice_period",
    "salary_expectation",
    "years_of_experience",
    "earliest_start_date",
    "preferred_locations",
    "preferred_job_types",
)
SETTINGS_FIELDS = (
    "resume_max_pages",
    "max_projects",
    "max_bullets_per_project",
    "timezone",
    "submit_confirmation_mode",
    "deadline_reminder_days",
    "follow_up_after_days",
    "notify_in_app",
    "header_portfolio",
    "header_github",
    "header_linkedin",
    "github_username",
    "include_forks",
    "ai_provider",
    "ai_fallback_enabled",
    "ai_privacy_mode",
)


def _num(v):
    return float(v) if v is not None else None


def export_portable(db: Session, user_id: int) -> dict:
    p = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    s = db.get(UserSettings, user_id)
    profile = {f: getattr(p, f) for f in PROFILE_FIELDS} if p else {}
    if profile.get("years_of_experience") is not None:
        profile["years_of_experience"] = _num(profile["years_of_experience"])
    exps = db.scalars(select(Experience).where(Experience.user_id == user_id).order_by(Experience.sort_order)).all()
    templates = []
    for t in db.scalars(select(ResumeTemplate).where(ResumeTemplate.user_id == user_id)):
        raw = t.source_text.encode() if t.source_text and t.format in ("latex", "text") else t.source_bytes
        if not raw:
            continue
        filename = t.original_filename or f"{t.name}.{'tex' if t.format == 'latex' else t.format}"
        if t.format == "latex" and not filename.lower().endswith(".tex"):
            filename = re.sub(r"\.\w+$", "", filename) + ".tex"  # zipped projects were inlined into one file
        templates.append(
            {
                "name": t.name,
                "filename": filename,
                "format": t.format,
                "is_default": bool(s and s.default_template_id == t.id),
                "data_b64": base64.b64encode(raw).decode(),
            }
        )
    manual = []
    for pr in db.scalars(select(Project).where(Project.user_id == user_id, Project.origin == "manual")):
        manual.append(
            {
                "name": pr.name,
                "display_name": pr.display_name,
                "description": pr.description,
                "repo_url": pr.repo_url,
                "homepage_url": pr.homepage_url,
                "status": pr.status,
                "bullets": [b.text for b in pr.bullets],
            }
        )
    return {
        "version": VERSION,
        "profile": profile,
        "experiences": [
            {
                "organization": e.organization,
                "title": e.title,
                "location": e.location,
                "employment_type": e.employment_type,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "is_current": e.is_current,
                "bullets": [b.text for b in e.bullets],
            }
            for e in exps
        ],
        "educations": [
            {
                "institution": e.institution,
                "degree": e.degree,
                "field_of_study": e.field_of_study,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "graduation_year": e.graduation_year,
                "grade": e.grade,
                "location": e.location,
            }
            for e in db.scalars(select(Education).where(Education.user_id == user_id).order_by(Education.sort_order))
        ],
        "skills": [
            {"name": k.name, "category": k.category}
            for k in db.scalars(select(Skill).where(Skill.user_id == user_id).order_by(Skill.sort_order))
        ],
        "certifications": [
            {"name": c.name, "issuer": c.issuer, "date": c.date, "url": c.url}
            for c in db.scalars(select(Certification).where(Certification.user_id == user_id))
        ],
        "achievements": [a.text for a in db.scalars(select(Achievement).where(Achievement.user_id == user_id))],
        "answers": [
            {"question_key": a.question_key, "label": a.label, "answer": a.answer, "sensitive": a.sensitive}
            for a in db.scalars(select(ProfileAnswer).where(ProfileAnswer.user_id == user_id))
        ],
        "settings": {f: getattr(s, f) for f in SETTINGS_FIELDS} if s else {},
        "manual_projects": manual,
        "templates": templates,
    }


def _blank(v) -> bool:
    return v is None or v == "" or v == []


def import_portable(db: Session, user_id: int, data: dict) -> dict:
    """Merges a portable export into this account. Returns counts and the background tasks it started."""
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise AppError(
            "This file is not a CareerPilot export (or comes from an incompatible version).",
            code="invalid_import",
            hint="On the other CareerPilot, open Settings → Data and download the export again.",
        )
    counts = {
        k: 0
        for k in (
            "profile_fields",
            "experiences",
            "educations",
            "skills",
            "certifications",
            "achievements",
            "answers",
            "projects",
            "templates",
        )
    }
    p = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if p is None:
        p = UserProfile(user_id=user_id)
        db.add(p)
    for f, v in (data.get("profile") or {}).items():
        if f in PROFILE_FIELDS and not _blank(v) and _blank(getattr(p, f)):
            setattr(p, f, v)
            counts["profile_fields"] += 1

    have = {
        (e.organization.lower(), e.title.lower())
        for e in db.scalars(select(Experience).where(Experience.user_id == user_id))
    }
    for i, e in enumerate(data.get("experiences") or []):
        key = (str(e.get("organization", "")).lower(), str(e.get("title", "")).lower())
        if not key[0] or key in have:
            continue
        row = Experience(
            user_id=user_id,
            organization=e["organization"][:200],
            title=str(e.get("title", ""))[:200],
            location=str(e.get("location", ""))[:200],
            employment_type=str(e.get("employment_type", ""))[:60],
            start_date=str(e.get("start_date", ""))[:40],
            end_date=str(e.get("end_date", ""))[:40],
            is_current=bool(e.get("is_current")),
            sort_order=i,
        )
        row.bullets = [ExperienceBullet(text=str(t)[:2000], sort_order=j) for j, t in enumerate(e.get("bullets") or [])]
        db.add(row)
        counts["experiences"] += 1

    have = {
        (e.institution.lower(), e.degree.lower())
        for e in db.scalars(select(Education).where(Education.user_id == user_id))
    }
    for i, e in enumerate(data.get("educations") or []):
        key = (str(e.get("institution", "")).lower(), str(e.get("degree", "")).lower())
        if not key[0] or key in have:
            continue
        gy = e.get("graduation_year")
        db.add(
            Education(
                user_id=user_id,
                institution=e["institution"][:200],
                degree=str(e.get("degree", ""))[:200],
                field_of_study=str(e.get("field_of_study", ""))[:200],
                start_date=str(e.get("start_date", ""))[:40],
                end_date=str(e.get("end_date", ""))[:40],
                graduation_year=gy if isinstance(gy, int) else None,
                grade=str(e.get("grade", ""))[:80],
                location=str(e.get("location", ""))[:200],
                sort_order=i,
            )
        )
        counts["educations"] += 1

    have = {k.canonical for k in db.scalars(select(Skill).where(Skill.user_id == user_id))}
    for i, k in enumerate(data.get("skills") or []):
        name = str(k.get("name", "")).strip()[:100]
        canon = canonicalize(name) if name else ""
        if not name or canon in have:
            continue
        have.add(canon)
        db.add(
            Skill(user_id=user_id, name=name, canonical=canon, category=str(k.get("category", ""))[:80], sort_order=i)
        )
        counts["skills"] += 1

    have = {c.name.lower() for c in db.scalars(select(Certification).where(Certification.user_id == user_id))}
    for c in data.get("certifications") or []:
        name = str(c.get("name", "")).strip()
        if name and name.lower() not in have:
            db.add(
                Certification(
                    user_id=user_id,
                    name=name[:300],
                    issuer=str(c.get("issuer", ""))[:200],
                    date=str(c.get("date", ""))[:40],
                    url=str(c.get("url", ""))[:300],
                )
            )
            counts["certifications"] += 1

    have = {a.text.strip().lower() for a in db.scalars(select(Achievement).where(Achievement.user_id == user_id))}
    for t in data.get("achievements") or []:
        if str(t).strip() and str(t).strip().lower() not in have:
            db.add(Achievement(user_id=user_id, text=str(t)[:2000]))
            counts["achievements"] += 1

    have = {a.question_key for a in db.scalars(select(ProfileAnswer).where(ProfileAnswer.user_id == user_id))}
    for a in data.get("answers") or []:
        key = str(a.get("question_key", ""))[:80]
        if key and key not in have and str(a.get("answer", "")).strip():
            db.add(
                ProfileAnswer(
                    user_id=user_id,
                    question_key=key,
                    label=str(a.get("label", ""))[:300],
                    answer=str(a["answer"])[:3000],
                    sensitive=bool(a.get("sensitive")),
                )
            )
            counts["answers"] += 1

    s = db.get(UserSettings, user_id)
    for f, v in (data.get("settings") or {}).items():
        if f in SETTINGS_FIELDS and v is not None and (f != "github_username" or not s.github_username):
            setattr(s, f, v)

    from app.services.github_scan import slugify

    have = {pr.name.lower() for pr in db.scalars(select(Project).where(Project.user_id == user_id))}
    for pr in data.get("manual_projects") or []:
        name = str(pr.get("name", "")).strip()
        if not name or name.lower() in have:
            continue
        slug, n = slugify(name), 2
        while db.scalar(select(Project.id).where(Project.user_id == user_id, Project.slug == slug)):
            slug, n = f"{slugify(name)}-{n}", n + 1
        row = Project(
            user_id=user_id,
            name=name[:200],
            display_name=str(pr.get("display_name") or name)[:200],
            slug=slug,
            origin="manual",
            description=str(pr.get("description", "")),
            repo_url=str(pr.get("repo_url", ""))[:300],
            homepage_url=str(pr.get("homepage_url", ""))[:300],
            status=str(pr.get("status") or "built")[:20],
        )
        row.bullets = [ProjectBullet(text=str(t)[:2000], sort_order=j) for j, t in enumerate(pr.get("bullets") or [])]
        db.add(row)
        counts["projects"] += 1
    db.commit()

    from app.services.resumes import create_template

    tasks: list[int] = []
    existing = {t.sha256 for t in db.scalars(select(ResumeTemplate).where(ResumeTemplate.user_id == user_id))}
    import hashlib

    for t in data.get("templates") or []:
        try:
            raw = base64.b64decode(t.get("data_b64", ""), validate=True)
        except ValueError:
            continue
        if not raw or hashlib.sha256(raw).hexdigest() in existing:
            continue
        tpl, task_id = create_template(
            db, user_id, str(t.get("filename") or "resume.tex")[:200], raw, str(t.get("name", ""))[:200]
        )
        tasks.append(task_id)
        counts["templates"] += 1
        if t.get("is_default"):
            s.default_template_id = tpl.id
    db.commit()

    if s.github_username:
        from app.tasks.queue import enqueue

        tasks.append(
            enqueue(
                db,
                user_id,
                "github_scan",
                {"force": False},
                ["Listing repositories", "Inspecting repositories", "Checking suggested projects"],
            ).id
        )
    return {"imported": counts, "task_ids": tasks}
