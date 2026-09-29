"""Runs the resume agent against a stored resume version: context from the database, one transaction per request."""

import copy
import re
import statistics

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import resume_agent as ra
from app.llm.provider import LLMProvider
from app.llm.router import describe
from app.matching.taxonomy import canonicalize, find_skills
from app.models import Experience, Job, Project, ResumeVersion, Skill, UserProfile, UserSettings
from app.resume.document import apply_ops, index
from app.services import editor

MAX_UNPREVIEWED = 8  # larger changes are shown as a preview first


def chars_per_line(pdf: bytes | None) -> int:
    """Width of a full text line in the rendered resume, measured from the PDF itself."""
    if not pdf:
        return 110
    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf, filetype="pdf")
        lengths = []
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                t = "".join(s["text"] for s in line["spans"]).strip()
                if len(t) > 30:
                    lengths.append(len(t))
        if len(lengths) < 5:
            return 110
        lengths.sort()
        return int(statistics.median(lengths[int(len(lengths) * 0.75) :]))
    except Exception:  # noqa: BLE001 - measurement is best effort
        return 110


def build_context(db: Session, v: ResumeVersion, selection: list[str]) -> ra.Context:
    user_id = v.user_id
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    job = db.get(Job, v.job_id) if v.job_id else None
    focus: list[str] = []
    if job:
        reqs = sorted(job.requirements, key=lambda r: {"required": 0, "preferred": 1}.get(r.importance, 2))
        focus = list(dict.fromkeys(r.canonical for r in reqs if r.kind == "skill" and r.canonical))
    facts: list[str] = []
    if profile and profile.summary:
        facts.append(profile.summary)
    for exp in db.scalars(select(Experience).where(Experience.user_id == user_id)):
        facts.append(f"{exp.title} at {exp.organization}")
        facts += [b.text for b in exp.bullets]
    skills = db.scalars(select(Skill).where(Skill.user_id == user_id)).all()
    keys = {s.canonical or canonicalize(s.name) for s in skills}
    if skills:
        facts.append("Skills: " + ", ".join(s.name for s in skills))
    on_resume = {
        re.sub(r"[^a-z0-9]", "", (e["fields"].get("primary") or "").lower())
        for s in v.content["sections"]
        for e in s.get("entries") or []
    }
    for p in db.scalars(select(Project).where(Project.user_id == user_id)):
        if not p.evidence_eligible:
            continue
        name = re.sub(r"[^a-z0-9]", "", (p.title or "").lower())
        verified = [e for e in p.evidence if e.status in ("verified", "user_provided")]
        keys |= {e.canonical for e in verified if e.canonical}
        if name and any(name in n or n in name for n in on_resume if n):
            facts.append(f"{p.title}: {p.description}".strip(": "))
            facts += [f"{p.title} uses {e.label}" for e in verified if e.kind in ("technology", "testing")][:12]
            facts += [e.label for e in verified if e.kind in ("feature", "architecture")][:8]
    for s in v.content["sections"]:
        for e in s.get("entries") or []:
            facts += [b["text"] for b in e.get("bullets") or [] if b.get("origin") != "user"]
        for cat in s.get("categories") or []:  # the skills section the user wrote in their own template
            facts.append(f"{cat['name']}: {', '.join(cat['items'])}")
    style = [
        b["text"]
        for s in v.content["sections"]
        for e in s.get("entries") or []
        for b in e.get("bullets") or []
        if b.get("origin") in ("template", "profile", "user") and not b.get("tech_line")
    ]
    if profile and profile.summary:
        style.insert(0, profile.summary)
    ctx_mem = v.assistant_context or {}
    settings = db.get(UserSettings, user_id)
    return ra.Context(
        content=v.content,
        selection=[i for i in selection if i],
        last_targets=list(ctx_mem.get("last_targets") or []),
        page_target=int(
            v.content.get("page_target")
            or (v.config or {}).get("length")
            or (settings.resume_max_pages if settings else 1)
        ),
        page_count=v.page_count,
        focus=focus,
        facts=[f for f in dict.fromkeys(facts) if f],
        evidence_keys={k for k in keys if k} | {m.key for f in facts for m in find_skills(f)},
        style_samples=style[:8],
        chars_per_line=chars_per_line(v.pdf),
    )


def run(
    db: Session,
    v: ResumeVersion,
    instruction: str,
    selection: list[str],
    provider: LLMProvider | None,
    base_revision: int | None,
    *,
    confirm: bool = False,
) -> dict:
    editor.ensure_history(db, v)
    ctx = build_context(db, v, selection)
    u = ra.understand(ctx, instruction, provider)
    ai = describe(provider) if provider is not None and (provider.used or provider.notices) else ""
    if not u.steps:
        report = "\n".join(f"• Not changed — {n}" for n in u.notes)
        return {
            "applied": False,
            "question": "" if report else u.question,
            "options": [] if report else u.options,
            "steps": [],
            "report": report,
            "ai": ai,
        }

    # history operations run on their own
    history = [s for s in u.steps if s.op in ("UNDO", "REDO", "RESTORE_VERSION")]
    if history:
        s = history[0]
        if s.op == "UNDO":
            v = editor.undo(db, v, base_revision)
            report = "Undid the last change."
        elif s.op == "REDO":
            v = editor.redo(db, v, base_revision)
            report = "Redid the change."
        else:
            v = editor.restore(db, v, s.params["number"], base_revision)
            report = f"Restored revision {s.params['number']} as a new change."
        return {
            "applied": True,
            "state": editor.payload(db, v),
            "changed": ["all"],
            "report": report,
            "steps": [{"label": report, "status": "done"}],
            "ai": ai,
        }

    steps = sorted(u.steps, key=lambda s: ra.ORDER.get(s.op, 1))
    results: list[dict] = [{"label": n, "status": "skipped", "detail": "not on this resume"} for n in u.notes]
    ops: list[dict] = []
    notes: list[str] = []
    before = {k: el.get("text", "") for k, el in index(v.content).items()}
    work = copy.deepcopy(v.content)
    for step in steps:
        if step.op in ra.TEXT_OPS:
            wctx = ra.Context(**{**ctx.__dict__, "content": work})
            new_ops, step_notes, failure = ra.plan_text_edits(wctx, step, provider)
            notes += step_notes
            if new_ops:
                work, _ = apply_ops(work, new_ops, "assistant")
                ops += new_ops
                results.append(
                    {
                        "label": ra.describe_text_change(ctx, step, before, new_ops),
                        "status": "done",
                        "op": step.op,
                        "ids": [o["id"] for o in new_ops],
                    }
                )
            else:
                results.append(
                    {
                        "label": step.label or step.clause,
                        "status": "skipped",
                        "detail": failure or (step_notes[0] if step_notes else "no change was needed"),
                        "op": step.op,
                    }
                )
        else:
            new_ops, failure = ra.structural_ops(ra.Context(**{**ctx.__dict__, "content": work}), step)
            if new_ops:
                try:
                    work, _ = apply_ops(work, new_ops, "assistant")
                except Exception as exc:  # noqa: BLE001 - an invalid step is reported, not fatal
                    results.append(
                        {"label": step.label or step.clause, "status": "skipped", "detail": str(exc), "op": step.op}
                    )
                    continue
                ops += new_ops
                label = step.label or step.op.replace("_", " ").capitalize()
                if step.op == "REDUCE_CONTENT":
                    n = len(new_ops)
                    label = f"Removed {n} item{'s' if n != 1 else ''} that support none of the job's requirements"
                results.append(
                    {
                        "label": label,
                        "status": "done",
                        "op": step.op,
                        "ids": [o.get("id") or o.get("kind") for o in new_ops],
                    }
                )
            else:
                results.append(
                    {"label": step.label or step.clause, "status": "skipped", "detail": failure, "op": step.op}
                )

    if not ops:
        return {
            "applied": False,
            "steps": results,
            "report": _report(results, notes, None, None),
            "ai": ai,
            "question": "",
            "options": [],
        }

    broad = sum(1 for o in ops if o["op"] in ("update_text", "remove_bullet", "remove_entry")) > MAX_UNPREVIEWED
    if broad and not confirm:
        from app.resume.assistant import preview

        return {
            "applied": False,
            "preview": preview(v.content, ops),
            "ops": ops,
            "steps": results,
            "broad": True,
            "report": "This changes a lot of the resume. Review the changes, then apply.",
            "ai": ai,
        }

    pages_before = v.page_count
    v, changed = editor.apply(db, v, ops, source="assistant", label=instruction[:200], base_revision=base_revision)
    fitted: list[str] = []
    target = int(v.content.get("page_target") or ctx.page_target or 1)
    if v.page_count and v.page_count > target and not any(o["op"] == "set_page_target" for o in ops):
        v, more = editor.apply(
            db,
            v,
            [{"op": "set_page_target", "pages": target}],
            source="assistant",
            label=f"Kept to {target} page{'s' if target > 1 else ''}",
            base_revision=v.revision,
        )
        changed += more
        fitted.append(f"Tightened content to keep the resume to {target} page{'s' if target > 1 else ''}")
    remember(v, results, ctx)
    db.commit()
    return {
        "applied": True,
        "state": editor.payload(db, v),
        "changed": changed,
        "steps": results + [{"label": f, "status": "done", "op": "SET_PAGE_TARGET"} for f in fitted],
        "report": _report(results + [{"label": f, "status": "done"} for f in fitted], notes, v, pages_before),
        "ai": ai,
        "question": "",
        "options": [],
    }


def remember(v: ResumeVersion, results: list[dict], ctx: ra.Context) -> None:
    """The most recent targets, so "it" and "some more" in the next request mean the same thing."""
    ids = [i for r in results if r["status"] == "done" and r.get("op") in ra.TEXT_OPS for i in r.get("ids", [])]
    if ids:
        v.assistant_context = {**(v.assistant_context or {}), "last_targets": ids[:8]}


def remember_manual(v: ResumeVersion, changed: list[str]) -> None:
    els = index(v.content)
    ids = [c for c in changed if els.get(c, {}).get("type") in ("paragraph", "bullet", "item")]
    if ids:
        v.assistant_context = {**(v.assistant_context or {}), "last_targets": ids[:8]}


def _report(results: list[dict], notes: list[str], v: ResumeVersion | None, pages_before: int | None) -> str:
    done = [r["label"] for r in results if r["status"] == "done" and r["label"]]
    skipped = [f"{r['label']}: {r['detail']}" for r in results if r["status"] == "skipped"]
    lines: list[str] = []
    if done:
        lines.append("Done." if len(done) > 1 else done[0] + ".")
        if len(done) > 1:
            lines += [f"• {d}" for d in done]
    if v is not None:
        if v.page_count:
            same = pages_before == v.page_count
            lines.append(
                f"• Resume {'remains' if same else 'is now'} {v.page_count} page{'s' if v.page_count != 1 else ''}"
            )
        q = v.quality_report or {}
        counts = q.get("counts") or {}
        if counts:
            if counts.get("fail"):
                lines.append(f"• {counts['fail']} check(s) failed — see Checks before exporting")
            elif counts.get("review"):
                lines.append(f"• Evidence checks passed; {counts['review']} item(s) worth a look in Checks")
            else:
                lines.append("• Evidence and quality checks passed")
        if len(done) == 1 and not skipped:
            lines.append("Nothing else was changed.")
    lines += [f"• Not changed — {s}" for s in skipped]
    lines += [f"• {n}" for n in notes[:3] if not any(n in s for s in skipped)]
    return "\n".join(lines)
