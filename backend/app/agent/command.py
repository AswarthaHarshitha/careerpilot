"""The command agent behind ⌘K: natural language -> tool -> result.

    understand (rules, then the model against the tool registry)  ->  resolve context from the current page
    ->  check the tool's permission  ->  run it  ->  report what happened and where to look

High-impact tools are never executed here: the user is taken to the confirmation screen.
"""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent import tools as T
from app.core.errors import AppError
from app.llm.provider import LLMError, LLMProvider, wrap_untrusted

RESUME_WORDS = re.compile(
    r"\b(summary|bullet|projects?|experience|skills?|section|portfolio|linkedin|github link|one page|1 page|two pages|"
    r"page|shorter|longer|concise|rewrite|reword|natural|generic|stronger|emphasi[sz]e|focus|put .+ first|resume|"
    r"move|remove|add my|keep my|sound)\b",
    re.I,
)


@dataclass
class Intent:
    tool: str = ""  # a registry tool, or "navigate"
    href: str = ""
    label: str = ""
    confidence: float = 1.0


def rules(text: str, ctx: T.Ctx) -> Intent | None:
    t = text.lower().strip()
    if re.search(r"\b(submit|send)\b.*\b(application|it|now)\b|\bconfirm (and|&) submit\b", t):
        return Intent("submit_application")
    if re.search(
        r"\b(what'?s|what is|why)\b.*\b(stopp|block|holding|stuck|waiting|going on)\w*|\bwhy (isn'?t|hasn'?t)\b"
        r"|\bstatus of (my |the )?application\b",
        t,
    ):
        return Intent("application_blockers")
    if re.search(r"\b(continue|resume|carry on|keep going)\b.*\bapplication\b|^(continue|resume|keep going)\b", t):
        return Intent("continue_application")
    if re.search(r"\b(apply|start (the |my )?application|apply with careerpilot)\b", t) and not re.search(
        r"\bapply (the |these |those )?(changes?|edits?)\b", t
    ):
        return Intent("start_application")
    if re.search(r"\b(generate|create|tailor|build|make)\b.*\bresume\b", t) and not re.search(r"\bone page\b", t):
        return Intent("generate_resume")
    if re.search(r"^undo\b|\bundo (the )?last (resume )?change\b", t):
        return Intent("undo_resume_change")
    if re.search(r"\b(ats|run (the )?checks?|check (the |my )?resume|quality check)\b", t):
        return Intent("run_ats_check")
    if re.search(r"\b(skill )?gaps?\b|\bmissing skills\b|\bwhat am i missing\b", t):
        return Intent("get_skill_gaps")
    if re.search(r"\b(re)?scan\b.*\bgithub\b|\bgithub\b.*\b(re)?scan\b|\brefresh (my )?repos", t):
        return Intent("scan_github")
    if re.search(r"\b(career )?ideas?\b", t) and re.search(r"\b(add|save|note|new|idea:)", t):
        return Intent("add_career_idea")
    if re.search(r"^(open|show|go to)\b.*\bnotes?\b", t):
        return Intent("navigate", label="job notes")
    if re.search(r"\b(add|save|write)\b.*\bnote\b|^note\b", t):
        return Intent("add_job_note")
    if re.search(r"^(open|edit|show|go to)\b.*\bresume\b", t) and not RESUME_WORDS.search(re.sub(r"\bresume\b", "", t)):
        return Intent("navigate", label="resume")
    if re.search(r"^(open|show|go to)\b.*\bapplication\b", t):
        return Intent("navigate", label="application")
    if re.search(r"^(open|show|go to)\b.*\b(job|analysis|requirements)\b", t):
        return Intent("navigate", label="job")
    if ctx.resume_id or RESUME_WORDS.search(t):
        return Intent("edit_resume", confidence=0.7 if not ctx.resume_id else 1.0)
    return None


CLASSIFY_SYSTEM = (
    "Pick the single tool that fulfils the user's request in a job-application workspace. Tools:\n{tools}\n"
    'Return {{"tool": "<name>" | null, "question": "one short question if no tool fits"}}.'
)


def model_intent(text: str, provider: LLMProvider) -> tuple[Intent | None, str]:
    listing = "\n".join(f"- {t.name}: {t.description}" for t in T.REGISTRY.values())
    try:
        data = provider.complete_json(
            CLASSIFY_SYSTEM.format(tools=listing),
            wrap_untrusted("REQUEST", text, 1000),
            max_tokens=200,
            temperature=0.0,
        )
    except LLMError:
        return None, ""
    name = data.get("tool")
    if isinstance(name, str) and name in T.REGISTRY:
        return Intent(name, confidence=0.8), ""
    q = data.get("question")
    return None, (str(q)[:200] if q else "")


def run(db: Session, user_id: int, text: str, context: dict, provider: LLMProvider | None) -> dict:
    ctx = T.Ctx(
        db=db,
        user_id=user_id,
        job_id=_int(context.get("job_id")),
        application_id=_int(context.get("application_id")),
        resume_id=_int(context.get("resume_id")),
        selection=[str(x) for x in (context.get("selection") or [])][:20],
        text=text.strip(),
    )
    intent = rules(text, ctx)
    question = ""
    if intent is None and provider is not None:
        intent, question = model_intent(text, provider)
    if intent is None:
        return {
            "ok": False,
            "report": question or "Is this about your resume, an application, or a job?",
            "options": ["Edit current resume", "What is stopping the application?", "Show skill gaps"],
            "tool": None,
        }
    try:
        if intent.tool == "navigate":
            return _navigate(ctx, intent)
        tool = T.REGISTRY[intent.tool]
        T.resolve(ctx, tool.needs)
        result = tool.run(ctx)
    except AppError as exc:
        return {"ok": False, "report": exc.message, "tool": intent.tool, "options": [], "code": exc.code}
    report = result.report
    if ctx.notes:
        report = " ".join(ctx.notes) + ". " + report
    return {
        "ok": result.ok,
        "report": report,
        "navigate": result.navigate,
        "actions": result.actions,
        "tool": tool.name,
        "permission": tool.permission,
        "options": result.data.get("options", []),
        "data": {k: v for k, v in result.data.items() if k != "options"},
    }


def _navigate(ctx: T.Ctx, intent: Intent) -> dict:
    needs = {"resume": ("resume",), "application": ("application",), "job notes": ("job",), "job": ("job",)}
    T.resolve(ctx, needs[intent.label])
    href = {
        "resume": f"/resumes/{ctx.resume_id}",
        "application": f"/applications/{ctx.application_id}",
        "job notes": f"/jobs/{ctx.job_id}#notes",
        "job": f"/jobs/{ctx.job_id}",
    }[intent.label]
    note = (" ".join(ctx.notes) + ". ") if ctx.notes else ""
    return {
        "ok": True,
        "report": f"{note}Opening the {intent.label}.",
        "navigate": href,
        "tool": "navigate",
        "permission": T.READ,
        "options": [],
        "actions": [],
    }


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
