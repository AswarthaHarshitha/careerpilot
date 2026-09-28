"""Loads the user's profile and portfolio into the plain snapshot the matching engine works on."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.matching.engine import ExperienceSnapshot, ProfileSnapshot, ProjectSnapshot
from app.models import Certification, Education, Experience, Project, Skill, UserProfile


def load_snapshot(db: Session, user_id: int) -> ProfileSnapshot:
    snap = ProfileSnapshot()
    snap.skills = {s.canonical: s.id for s in db.scalars(select(Skill).where(Skill.user_id == user_id))}
    for e in db.scalars(select(Experience).where(Experience.user_id == user_id)
                        .options(selectinload(Experience.bullets))):
        text = " ".join([e.title] + [b.text for b in e.bullets])
        snap.experiences.append(ExperienceSnapshot(e.id, f"{e.title} at {e.organization}".strip(), text))
    projects = db.scalars(select(Project).where(Project.user_id == user_id)
                          .options(selectinload(Project.evidence), selectinload(Project.bullets))).all()
    for p in projects:
        if not p.evidence_eligible:  # portfolio/profile assets, excluded repos and unfinished projects
            continue
        snap.projects.append(ProjectSnapshot(
            id=p.id, title=p.title, description=p.description,
            evidence=[(e.canonical, e.status, e.label, e.source_detail) for e in p.evidence
                      if e.status != "unsupported"],
            bullets=[b.text for b in p.bullets], readme=p.readme_excerpt, pushed_at=p.pushed_at,
        ))
    snap.certifications = [(c.id, c.name) for c in db.scalars(select(Certification)
                                                               .where(Certification.user_id == user_id))]
    snap.educations = [(e.id, " ".join(x for x in (e.degree, e.field_of_study, e.institution) if x))
                       for e in db.scalars(select(Education).where(Education.user_id == user_id))]
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile and profile.years_of_experience is not None:
        snap.years_of_experience = float(profile.years_of_experience)
    return snap
