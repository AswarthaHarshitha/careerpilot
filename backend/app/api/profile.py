from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import AppError, NotFound
from app.matching.taxonomy import canonicalize, display_name
from app.models import (
    Achievement,
    Certification,
    Education,
    Experience,
    ExperienceBullet,
    ProfileAnswer,
    Skill,
    User,
    UserProfile,
)
from app.services.profile_import import get_or_create_profile

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _url(v: str) -> str:
    v = (v or "").strip()
    if v and not v.startswith(("http://", "https://")):
        v = "https://" + v
    if v and (" " in v or len(v) > 300):
        raise ValueError("must be a valid URL")
    return v


class ProfileIn(BaseModel):
    full_name: str = Field(default="", max_length=200)
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=200)
    street: str = Field(default="", max_length=200)
    address_line2: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="", max_length=100)
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    headline: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=3000)
    work_authorization: str = Field(default="", max_length=300)
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    notice_period: str = Field(default="", max_length=100)
    salary_expectation: str = Field(default="", max_length=100)
    years_of_experience: float | None = Field(default=None, ge=0, le=60)
    earliest_start_date: str = Field(default="", max_length=40)
    preferred_locations: list[str] = Field(default_factory=list, max_length=20)
    preferred_job_types: list[str] = Field(default_factory=list, max_length=10)

    _urls = field_validator("linkedin_url", "github_url", "portfolio_url")(_url)


def profile_out(p: UserProfile) -> dict:
    return {
        "full_name": p.full_name,
        "first_name": p.first_name,
        "last_name": p.last_name,
        "email": p.email,
        "phone": p.phone,
        "location": p.location,
        "street": p.street,
        "address_line2": p.address_line2,
        "city": p.city,
        "state": p.state,
        "postal_code": p.postal_code,
        "country": p.country,
        "linkedin_url": p.linkedin_url,
        "github_url": p.github_url,
        "portfolio_url": p.portfolio_url,
        "headline": p.headline,
        "summary": p.summary,
        "work_authorization": p.work_authorization,
        "requires_sponsorship": p.requires_sponsorship,
        "willing_to_relocate": p.willing_to_relocate,
        "notice_period": p.notice_period,
        "salary_expectation": p.salary_expectation,
        "years_of_experience": float(p.years_of_experience) if p.years_of_experience is not None else None,
        "earliest_start_date": p.earliest_start_date,
        "preferred_locations": p.preferred_locations or [],
        "preferred_job_types": p.preferred_job_types or [],
    }


@router.get("")
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_or_create_profile(db, user.id)
    db.commit()
    exps = db.scalars(
        select(Experience)
        .where(Experience.user_id == user.id)
        .options(selectinload(Experience.bullets))
        .order_by(Experience.sort_order, Experience.id)
    ).all()
    return {
        "personal": profile_out(p),
        "experiences": [exp_out(e) for e in exps],
        "educations": [
            edu_out(e)
            for e in db.scalars(
                select(Education).where(Education.user_id == user.id).order_by(Education.sort_order, Education.id)
            )
        ],
        "skills": [
            skill_out(s)
            for s in db.scalars(
                select(Skill).where(Skill.user_id == user.id).order_by(Skill.category, Skill.sort_order, Skill.id)
            )
        ],
        "certifications": [
            cert_out(c)
            for c in db.scalars(
                select(Certification)
                .where(Certification.user_id == user.id)
                .order_by(Certification.sort_order, Certification.id)
            )
        ],
        "achievements": [
            {"id": a.id, "text": a.text}
            for a in db.scalars(
                select(Achievement)
                .where(Achievement.user_id == user.id)
                .order_by(Achievement.sort_order, Achievement.id)
            )
        ],
        "answers": [
            answer_out(a)
            for a in db.scalars(
                select(ProfileAnswer).where(ProfileAnswer.user_id == user.id).order_by(ProfileAnswer.question_key)
            )
        ],
        "completeness": completeness(db, user.id, p),
    }


def completeness(db: Session, user_id: int, p: UserProfile) -> dict:
    missing = []
    for attr, label in (
        ("full_name", "Full name"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("location", "Location"),
        ("linkedin_url", "LinkedIn"),
        ("github_url", "GitHub"),
    ):
        if not getattr(p, attr):
            missing.append(label)
    if p.years_of_experience is None:
        missing.append("Years of experience")
    if not p.work_authorization:
        missing.append("Work authorization")
    if p.requires_sponsorship is None:
        missing.append("Sponsorship requirement")
    if not db.scalar(select(Education.id).where(Education.user_id == user_id)):
        missing.append("Education")
    if not db.scalar(select(Skill.id).where(Skill.user_id == user_id)):
        missing.append("Skills")
    return {"missing": missing}


@router.put("")
def update_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_or_create_profile(db, user.id)
    for k, v in body.model_dump().items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    if p.full_name and not (p.first_name or p.last_name):
        parts = p.full_name.split()
        p.first_name, p.last_name = parts[0], " ".join(parts[1:])
    if p.full_name and not user.display_name:
        user.display_name = p.full_name
    db.commit()
    return profile_out(p)


# --- experience -----------------------------------------------------------------------------------


class ExperienceIn(BaseModel):
    organization: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    employment_type: str = Field(default="", max_length=60)
    start_date: str = Field(default="", max_length=40)
    end_date: str = Field(default="", max_length=40)
    is_current: bool = False
    bullets: list[str] = Field(default_factory=list, max_length=15)
    sort_order: int = 0


def exp_out(e: Experience) -> dict:
    return {
        "id": e.id,
        "organization": e.organization,
        "title": e.title,
        "location": e.location,
        "employment_type": e.employment_type,
        "start_date": e.start_date,
        "end_date": e.end_date,
        "is_current": e.is_current,
        "sort_order": e.sort_order,
        "bullets": [b.text for b in e.bullets],
    }


def _owned(db: Session, model, user_id: int, obj_id: int, what: str):
    obj = db.get(model, obj_id)
    if obj is None or obj.user_id != user_id:
        raise NotFound(what)
    return obj


@router.post("/experiences")
def add_experience(body: ExperienceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = Experience(user_id=user.id, **body.model_dump(exclude={"bullets"}))
    e.bullets = [ExperienceBullet(text=b.strip()[:600], sort_order=i) for i, b in enumerate(body.bullets) if b.strip()]
    db.add(e)
    db.commit()
    return exp_out(e)


@router.put("/experiences/{exp_id}")
def update_experience(
    exp_id: int, body: ExperienceIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    e = _owned(db, Experience, user.id, exp_id, "Experience")
    for k, v in body.model_dump(exclude={"bullets"}).items():
        setattr(e, k, v)
    e.bullets.clear()
    db.flush()
    e.bullets.extend(
        ExperienceBullet(text=b.strip()[:600], sort_order=i) for i, b in enumerate(body.bullets) if b.strip()
    )
    db.commit()
    return exp_out(e)


@router.delete("/experiences/{exp_id}")
def delete_experience(exp_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, Experience, user.id, exp_id, "Experience"))
    db.commit()
    return {"ok": True}


# --- education ------------------------------------------------------------------------------------


class EducationIn(BaseModel):
    institution: str = Field(min_length=1, max_length=200)
    degree: str = Field(default="", max_length=200)
    field_of_study: str = Field(default="", max_length=200)
    start_date: str = Field(default="", max_length=40)
    end_date: str = Field(default="", max_length=40)
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    grade: str = Field(default="", max_length=80)
    location: str = Field(default="", max_length=200)
    sort_order: int = 0


def edu_out(e: Education) -> dict:
    return {
        "id": e.id,
        "institution": e.institution,
        "degree": e.degree,
        "field_of_study": e.field_of_study,
        "start_date": e.start_date,
        "end_date": e.end_date,
        "graduation_year": e.graduation_year,
        "grade": e.grade,
        "location": e.location,
        "sort_order": e.sort_order,
    }


@router.post("/educations")
def add_education(body: EducationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = Education(user_id=user.id, **body.model_dump())
    db.add(e)
    db.commit()
    return edu_out(e)


@router.put("/educations/{edu_id}")
def update_education(edu_id: int, body: EducationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = _owned(db, Education, user.id, edu_id, "Education")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    db.commit()
    return edu_out(e)


@router.delete("/educations/{edu_id}")
def delete_education(edu_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, Education, user.id, edu_id, "Education"))
    db.commit()
    return {"ok": True}


# --- skills ---------------------------------------------------------------------------------------


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=80)


def skill_out(s: Skill) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "canonical": s.canonical,
        "category": s.category,
        "known": not s.canonical.startswith("x:"),
        "taxonomy_name": display_name(s.canonical),
    }


@router.post("/skills")
def add_skill(body: SkillIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    key = canonicalize(body.name)
    if db.scalar(select(Skill).where(Skill.user_id == user.id, Skill.canonical == key)):
        raise AppError(f"{body.name} is already in your skills.", code="duplicate_skill", status_code=409)
    s = Skill(user_id=user.id, name=body.name.strip(), canonical=key, category=body.category.strip())
    db.add(s)
    db.commit()
    return skill_out(s)


@router.put("/skills/{skill_id}")
def update_skill(skill_id: int, body: SkillIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _owned(db, Skill, user.id, skill_id, "Skill")
    key = canonicalize(body.name)
    clash = db.scalar(select(Skill).where(Skill.user_id == user.id, Skill.canonical == key, Skill.id != s.id))
    if clash:
        raise AppError(f"{body.name} is already in your skills.", code="duplicate_skill", status_code=409)
    s.name, s.canonical, s.category = body.name.strip(), key, body.category.strip()
    db.commit()
    return skill_out(s)


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, Skill, user.id, skill_id, "Skill"))
    db.commit()
    return {"ok": True}


# --- certifications & achievements ----------------------------------------------------------------


class CertificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    issuer: str = Field(default="", max_length=200)
    date: str = Field(default="", max_length=40)
    url: str = ""

    _u = field_validator("url")(_url)


def cert_out(c: Certification) -> dict:
    return {"id": c.id, "name": c.name, "issuer": c.issuer, "date": c.date, "url": c.url}


@router.post("/certifications")
def add_cert(body: CertificationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = Certification(user_id=user.id, **body.model_dump())
    db.add(c)
    db.commit()
    return cert_out(c)


@router.put("/certifications/{cid}")
def update_cert(cid: int, body: CertificationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _owned(db, Certification, user.id, cid, "Certification")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.commit()
    return cert_out(c)


@router.delete("/certifications/{cid}")
def delete_cert(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, Certification, user.id, cid, "Certification"))
    db.commit()
    return {"ok": True}


class AchievementIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/achievements")
def add_achievement(body: AchievementIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = Achievement(user_id=user.id, text=body.text.strip())
    db.add(a)
    db.commit()
    return {"id": a.id, "text": a.text}


@router.put("/achievements/{aid}")
def update_achievement(
    aid: int, body: AchievementIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    a = _owned(db, Achievement, user.id, aid, "Achievement")
    a.text = body.text.strip()
    db.commit()
    return {"id": a.id, "text": a.text}


@router.delete("/achievements/{aid}")
def delete_achievement(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, Achievement, user.id, aid, "Achievement"))
    db.commit()
    return {"ok": True}


# --- saved application answers --------------------------------------------------------------------

ANSWER_KEYS = Literal[
    "how_heard",
    "referral",
    "why_company",
    "additional_info",
    "gender",
    "race",
    "veteran",
    "disability",
    "sexual_orientation",
    "country",
    "custom",
]
SENSITIVE_KEYS = {"gender", "race", "veteran", "disability", "sexual_orientation"}


class AnswerIn(BaseModel):
    question_key: ANSWER_KEYS
    label: str = Field(default="", max_length=300)
    answer: str = Field(default="", max_length=3000)


def answer_out(a: ProfileAnswer) -> dict:
    return {"id": a.id, "question_key": a.question_key, "label": a.label, "answer": a.answer, "sensitive": a.sensitive}


@router.put("/answers")
def upsert_answer(body: AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.scalar(
        select(ProfileAnswer).where(ProfileAnswer.user_id == user.id, ProfileAnswer.question_key == body.question_key)
    )
    if a is None:
        a = ProfileAnswer(user_id=user.id, question_key=body.question_key)
        db.add(a)
    a.label, a.answer = body.label, body.answer.strip()
    a.sensitive = body.question_key in SENSITIVE_KEYS
    db.commit()
    return answer_out(a)


@router.delete("/answers/{aid}")
def delete_answer(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned(db, ProfileAnswer, user.id, aid, "Answer"))
    db.commit()
    return {"ok": True}
