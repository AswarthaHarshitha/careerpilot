"""Seeds the career profile from the user's own resume template.

Only fills what is empty and never duplicates: a second import of the same template changes nothing. Every
imported value is the user's own content, stored as USER-PROVIDED.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.github.client import REPO_URL_RX
from app.matching.taxonomy import canonicalize
from app.models import (
    Achievement,
    Certification,
    Education,
    Experience,
    ExperienceBullet,
    Project,
    ProjectBullet,
    ResumeTemplate,
    Skill,
    UserProfile,
    UserSettings,
)
from app.services.github_scan import slugify

CERT_RX = re.compile(r"certif|certificate|licen[cs]e", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def split_dates(text: str) -> tuple[str, str, bool]:
    parts = [p.strip() for p in re.split(r"\s*(?:–|—|--|-|\bto\b)\s*", text or "") if p.strip()]
    if not parts:
        return "", "", False
    start = parts[0]
    end = parts[1] if len(parts) > 1 else ""
    current = bool(re.search(r"present|current|now|ongoing", end, re.I))
    return start, end, current


def get_or_create_profile(db: Session, user_id: int) -> UserProfile:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        db.flush()
    return profile


def import_from_template(db: Session, user_id: int, tpl: ResumeTemplate) -> dict:
    s = tpl.structure
    counts = {"profile_fields": 0, "experiences": 0, "projects_linked": 0, "projects_created": 0, "skills": 0,
              "education": 0, "certifications": 0, "achievements": 0}
    profile = get_or_create_profile(db, user_id)
    header = s.get("header", {})

    def fill(attr: str, value: str):
        if value and not getattr(profile, attr):
            setattr(profile, attr, value[:300])
            counts["profile_fields"] += 1

    name = header.get("name", "").strip()
    if name and len(name.split()) <= 6:
        fill("full_name", name)
        parts = name.split()
        fill("first_name", parts[0])
        if len(parts) > 1:
            fill("last_name", " ".join(parts[1:]))
    fill("email", header.get("email", ""))
    fill("phone", header.get("phone", ""))
    for link in header.get("links", []):
        url = link.get("url", "")
        if link.get("kind") == "linkedin":
            fill("linkedin_url", url)
        elif link.get("kind") == "github":
            fill("github_url", url)
        elif link.get("kind") == "website" and url.startswith("http"):
            fill("portfolio_url", url)
    settings = db.get(UserSettings, user_id)
    gh = re.match(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/?$", profile.github_url or "")
    if settings and gh and not settings.github_username:
        settings.github_username = gh.group(1)

    existing_exp = {_norm(e.organization): e for e in db.scalars(select(Experience).where(Experience.user_id == user_id))}
    existing_edu = {_norm(e.institution) for e in db.scalars(select(Education).where(Education.user_id == user_id))}
    existing_skills = {sk.canonical for sk in db.scalars(select(Skill).where(Skill.user_id == user_id))}
    existing_certs = {_norm(c.name) for c in db.scalars(select(Certification).where(Certification.user_id == user_id))}
    existing_ach = {_norm(a.text) for a in db.scalars(select(Achievement).where(Achievement.user_id == user_id))}
    projects = db.scalars(select(Project).where(Project.user_id == user_id)).all()

    for sec in s.get("sections", []):
        kind, layout = sec["kind"], sec["layout"]
        if kind == "summary" and layout == "paragraph":
            fill("summary", sec["paragraph"]["text"])
        elif kind == "experience" and layout == "entries":
            for pos, e in enumerate(sec["entries"]):
                f = e["fields"]
                org = f.get("primary", "")
                if not org or _norm(org) in existing_exp:
                    continue
                start, end, current = split_dates(f.get("dates", ""))
                exp = Experience(user_id=user_id, organization=org[:200], title=f.get("secondary", "")[:200],
                                 location=f.get("location", "")[:200], start_date=start[:40], end_date=end[:40],
                                 is_current=current, sort_order=pos)
                exp.bullets = [ExperienceBullet(text=it["text"], sort_order=i) for i, it in enumerate(e["items"])]
                db.add(exp)
                existing_exp[_norm(org)] = exp
                counts["experiences"] += 1
        elif kind == "projects" and layout == "entries":
            for e in sec["entries"]:
                f = e["fields"]
                bullets = [it["text"] for it in e["items"]]
                project = _match_project(projects, f)
                if project is None:
                    title = f.get("primary", "").strip()
                    if not title:
                        continue
                    url = f.get("url", "")
                    project = Project(user_id=user_id, name=f.get("short_name") or title, display_name=title[:200],
                                      slug=_unique_slug(db, user_id, slugify(f.get("short_name") or title)),
                                      origin="manual", repo_url=url if "github.com" in url else "",
                                      homepage_url=url if url and "github.com" not in url else "",
                                      description=f.get("tech", ""))
                    db.add(project)
                    db.flush()
                    projects.append(project)
                    counts["projects_created"] += 1
                else:
                    counts["projects_linked"] += 1
                    if not project.display_name or project.display_name == project.name:
                        project.display_name = f.get("primary", project.display_name)[:200]
                if not project.bullets and bullets:
                    project.bullets = [ProjectBullet(text=b, source="template_import", sort_order=i)
                                       for i, b in enumerate(bullets)]
        elif layout == "skills":
            for c in sec["skills"]:
                for pos, item in enumerate(c["items"]):
                    key = canonicalize(item)
                    if not key or key in existing_skills:
                        continue
                    db.add(Skill(user_id=user_id, name=item[:100], canonical=key[:100], category=c["name"][:80],
                                 sort_order=pos))
                    existing_skills.add(key)
                    counts["skills"] += 1
        elif kind == "education" and layout == "entries":
            for pos, e in enumerate(sec["entries"]):
                f = e["fields"]
                inst = f.get("primary", "")
                if not inst or _norm(inst) in existing_edu:
                    continue
                start, end, _ = split_dates(f.get("dates", ""))
                years = re.findall(r"(?:19|20)\d{2}", f.get("dates", ""))
                db.add(Education(user_id=user_id, institution=inst[:200], degree=f.get("secondary", "")[:200],
                                 start_date=start[:40], end_date=end[:40],
                                 graduation_year=int(years[-1]) if years else None, grade=f.get("grade", "")[:80],
                                 location=f.get("location", "")[:200], sort_order=pos))
                existing_edu.add(_norm(inst))
                counts["education"] += 1
        elif kind in ("certifications", "achievements") and layout in ("items", "entries"):
            texts = ([it["text"] for it in sec.get("items", [])] if layout == "items"
                     else [e["fields"].get("primary", "") for e in sec["entries"]])
            for pos, t in enumerate(texts):
                t = t.strip()
                if not t:
                    continue
                if kind == "certifications" or (CERT_RX.search(t) and len(t) < 160):
                    if _norm(t) not in existing_certs:
                        db.add(Certification(user_id=user_id, name=t.rstrip(".")[:300], sort_order=pos))
                        existing_certs.add(_norm(t))
                        counts["certifications"] += 1
                elif _norm(t) not in existing_ach:
                    db.add(Achievement(user_id=user_id, text=t, sort_order=pos))
                    existing_ach.add(_norm(t))
                    counts["achievements"] += 1
    db.commit()
    return counts


def _match_project(projects: list[Project], fields: dict) -> Project | None:
    url = fields.get("url", "")
    m = REPO_URL_RX.match(url) if url else None
    if m:
        full = f"{m.group(1)}/{m.group(2)}".lower()
        for p in projects:
            if p.repo_full_name.lower() == full:
                return p
    for key in (fields.get("short_name", ""), fields.get("primary", "")):
        k = _norm(key)
        if not k:
            continue
        for p in projects:
            names = {_norm(p.name), _norm(p.display_name), _norm(p.repo_full_name.split("/")[-1])}
            if k in names or any(len(n) >= 5 and (n.startswith(k) or k.startswith(n)) for n in names if n):
                return p
    return None


def _unique_slug(db: Session, user_id: int, base: str) -> str:
    slug, n = base, 2
    while db.scalar(select(Project.id).where(Project.user_id == user_id, Project.slug == slug)):
        slug = f"{base}-{n}"
        n += 1
    return slug
