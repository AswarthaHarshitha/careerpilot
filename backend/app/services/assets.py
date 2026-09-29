"""Classifies repositories and entries as projects or personal profile assets.

A portfolio website, a `<user>.github.io` site and the `<user>/<user>` profile README describe the person, not
a piece of engineering work. They are locked as PERSONAL PROFILE: never project evidence, never counted in
matching, never listed in a resume's Projects section. The portfolio may only appear as a header link.
"""

import re

PROFILE_WORDS = {
    "portfolio",
    "personal",
    "website",
    "site",
    "web",
    "page",
    "homepage",
    "my",
    "me",
    "resume",
    "cv",
    "dev",
    "v1",
    "v2",
    "v3",
    "new",
    "old",
    "official",
    "profile",
    "app",
    "react",
    "next",
    "nextjs",
}
ANCHOR_WORDS = {"portfolio", "website", "homepage", "profile"}


def _looks_like_portfolio(name: str, owner: str) -> bool:
    """True when the repository name consists only of portfolio/profile words (plus years or the owner's name),
    e.g. "Portfolio", "My-Portfolio", "portfolio-v2", "harshitha-portfolio" - but not "portfolio-optimizer-ml"."""
    tokens = [t for t in re.split(r"[-_.\s]+|(?<=[a-z])(?=[A-Z])", name) if t]
    owner_parts = {p.lower() for p in re.split(r"[-_.\s]+|(?<=[a-z])(?=[A-Z])", owner or "") if p}
    low = [t.lower() for t in tokens]
    if not any(t in ANCHOR_WORDS for t in low):
        return False
    return all(t in PROFILE_WORDS or t in owner_parts or re.fullmatch(r"(19|20)\d{2}", t) for t in low)


def is_personal_profile(
    repo_name: str, full_name: str = "", homepage: str = "", github_username: str = "", portfolio_url: str = ""
) -> bool:
    name = (repo_name or "").strip()
    full_name, homepage = full_name or "", homepage or ""
    owner = (full_name.split("/")[0] if "/" in full_name else github_username) or ""
    if _looks_like_portfolio(name, owner):
        return True
    if owner and name.lower() in (owner.lower(), f"{owner.lower()}.github.io"):
        return True
    if name.lower().endswith(".github.io"):
        return True
    return bool(portfolio_url and homepage and _same_site(portfolio_url, homepage))


def _same_site(a: str, b: str) -> bool:
    norm = lambda u: re.sub(r"^https?://(www\.)?", "", u.strip().lower()).rstrip("/")  # noqa: E731
    return bool(a and b) and norm(a) == norm(b)


def apply_classification(project, *, github_username: str = "", portfolio_url: str = "") -> bool:
    """Locks the project as a personal profile asset when the rule matches. Returns True if it did."""
    if is_personal_profile(
        project.name or "", project.repo_full_name or "", project.homepage_url or "", github_username, portfolio_url
    ):
        project.asset_type = "personal_profile"
        project.asset_type_locked = True
        project.resume_eligible = False
        return True
    return False
