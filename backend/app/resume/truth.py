"""Truthfulness checks for generated resume text.

A rewritten line is accepted only if it adds nothing the source material does not support:

* every number, percentage and year must already appear in the source text for that entry;
* every technology it names must be one the entry has claimable (verified or user-provided) evidence for;
* words that assert scale or impact (users, customers, revenue, production, led a team, award...) must already
  appear in the source text.

Rejected rewrites fall back to the user's original wording.
"""

import re
from dataclasses import dataclass

from app.matching.taxonomy import display_name, find_skills, implied_keys

NUMBER_RX = re.compile(r"(?<![A-Za-z])\d[\d,.]*\s*(?:%|\+|x|k|m|million|billion|thousand|lakh|crore)?", re.I)
IMPACT_TERMS = [
    "users",
    "user base",
    "customers",
    "clients",
    "revenue",
    "profit",
    "sales",
    "production",
    "in production",
    "enterprise-scale",
    "at scale",
    "million",
    "billion",
    "thousands",
    "hundreds",
    "led",
    "lead",
    "leading",
    "managed",
    "mentored",
    "team of",
    "award",
    "awarded",
    "won",
    "winner",
    "patent",
    "published",
    "adopted by",
    "deployed to",
    "saved",
    "increased",
    "reduced",
    "improved",
    "boosted",
    "grew",
    "cut",
    "accelerated",
    "downloads",
    "traffic",
    "sla",
    "uptime",
    "99.",
    "latency",
    "throughput",
    "cost",
]


@dataclass
class Verdict:
    ok: bool
    problems: list[str]


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group()).lower().rstrip(".") for m in NUMBER_RX.finditer(text)}


def check_rewrite(new: str, source: str, allowed_skills: set[str], max_len: int = 400) -> Verdict:
    problems: list[str] = []
    src_low = source.lower()
    new_numbers = _numbers(new)
    src_numbers = _numbers(source)
    for n in sorted(new_numbers - src_numbers):
        problems.append(f"introduces the number '{n}'")
    allowed = set(allowed_skills)
    for k in list(allowed):
        allowed |= implied_keys(k)
    for m in find_skills(new, include_weak=False):
        if m.key not in allowed and m.alias.lower() not in src_low:
            problems.append(f"names {display_name(m.key)}, which this entry has no evidence for")
    for term in IMPACT_TERMS:
        rx = re.compile(rf"(?<![a-z]){re.escape(term)}(?![a-z])", re.I)
        if rx.search(new) and not rx.search(source):
            problems.append(f"adds the claim '{term}'")
    if len(new) > max_len:
        problems.append("is too long")
    return Verdict(not problems, sorted(set(problems)))


def allowed_skills_for(texts: list[str], evidence_keys: set[str]) -> set[str]:
    keys = set(evidence_keys)
    for t in texts:
        keys |= {m.key for m in find_skills(t)}
    return keys
