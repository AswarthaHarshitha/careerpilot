"""Resume agent: intent resolution, planning and targeted execution on a synthetic document."""

import pytest

from app.agent import resume_agent as ra
from app.resume.document import ensure_ids

SUMMARY = (
    "Computer Science graduate with internship experience building REST APIs and full-stack applications. "
    "Comfortable with Python, SQL and React."
)


def doc() -> dict:
    content = {
        "header": {
            "name": "Test Person",
            "links": [
                {
                    "id": "header.link.portfolio",
                    "kind": "portfolio",
                    "url": "https://p.example.dev",
                    "text": "Portfolio",
                    "mode": "include",
                },
                {
                    "id": "header.link.github",
                    "kind": "github",
                    "url": "https://github.com/x",
                    "text": "GitHub",
                    "mode": "exclude",
                },
                {
                    "id": "header.link.linkedin",
                    "kind": "linkedin",
                    "url": "https://linkedin.com/in/x",
                    "text": "LinkedIn",
                    "mode": "include",
                },
            ],
        },
        "sections": [
            {"index": 0, "kind": "summary", "title": "Summary", "layout": "paragraph", "text": SUMMARY},
            {
                "index": 1,
                "kind": "experience",
                "title": "Work Experience",
                "layout": "entries",
                "entries": [
                    {
                        "fields": {"primary": "Acme Labs", "secondary": "Backend Intern"},
                        "bullets": [
                            {
                                "text": "Built REST APIs with FastAPI and PostgreSQL for issue intake.",
                                "origin": "profile",
                            },
                            {"text": "Wrote pytest suites for the billing service.", "origin": "profile"},
                        ],
                    },
                ],
            },
            {
                "index": 2,
                "kind": "projects",
                "title": "Projects",
                "layout": "entries",
                "entries": [
                    {
                        "fields": {"primary": "Commitments", "short_name": "Commitments"},
                        "bullets": [
                            {"text": "Built a commitment tracker with FastAPI and React.", "origin": "evidence"}
                        ],
                    },
                    {
                        "fields": {"primary": "EventSphere", "short_name": "EventSphere"},
                        "bullets": [
                            {"text": "Built an event platform with Node.js and MongoDB.", "origin": "evidence"}
                        ],
                    },
                    {
                        "fields": {"primary": "Resolve Issue Platform", "short_name": "Resolve"},
                        "bullets": [
                            {
                                "text": "Built an issue-resolution platform with Python, FastAPI and PostgreSQL.",
                                "origin": "evidence",
                            }
                        ],
                    },
                ],
            },
            {
                "index": 3,
                "kind": "other",
                "title": "Certifications & Achievements",
                "layout": "items",
                "items": ["SAP Certified", "AWS Certified Solutions Architect"],
            },
        ],
    }
    return ensure_ids(content)


def ctx(content=None, selection=None, last=None, facts=None) -> ra.Context:
    return ra.Context(
        content=content or doc(),
        selection=selection or [],
        last_targets=last or [],
        page_target=1,
        page_count=1,
        focus=["python", "fastapi", "postgresql", "rest_api"],
        facts=facts or [SUMMARY],
        evidence_keys={"python", "fastapi", "postgresql", "react", "sql", "pytest"},
        style_samples=[],
        chars_per_line=100,
    )


def ops_of(c: ra.Context, text: str) -> list[tuple[str, str]]:
    u = ra.understand(c, text, None)
    return [(s.op, s.target) for s in u.steps]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Increase the summary some more.", [("EXPAND_TEXT", "section:summary")]),
        ("Make it slightly shorter", None),  # pronoun without context: asks
        (
            "Put Resolve first and EventSphere second",
            [("REORDER_ENTRY", "projects.resolve"), ("REORDER_ENTRY", "projects.eventsphere")],
        ),
        ("Keep my portfolio out.", [("REMOVE_LINK", "portfolio")]),
        ("Add my GitHub but remove LinkedIn", [("ADD_LINK", "github"), ("REMOVE_LINK", "linkedin")]),
        ("Make the whole resume fit on one page", [("SET_PAGE_TARGET", "")]),
        ("Remove the certifications", [("REMOVE_SECTION", "section.3")]),
        ("Remove anything that isn't helping this application", [("REDUCE_CONTENT", "")]),
        ("Use the backend experience more", [("CHANGE_EMPHASIS", "section:section.1")]),
        ("undo", [("UNDO", "")]),
    ],
)
def test_rules_resolve_everyday_requests(text, expected):
    c = ctx()
    u = ra.understand(c, text, None)
    if expected is None:
        assert not u.steps and u.question and u.options
    else:
        assert [(s.op, s.target) for s in u.steps] == expected, u


def test_multi_step_request_is_split_and_planned():
    c = ctx()
    steps = ops_of(
        c,
        "Make the summary longer, put Resolve first, remove my portfolio, shorten the projects, "
        "and keep it to one page",
    )
    assert [op for op, _ in steps] == ["EXPAND_TEXT", "REORDER_ENTRY", "REMOVE_LINK", "SHORTEN_TEXT", "SET_PAGE_TARGET"]
    ordered = sorted([ra.Step(op, t) for op, t in steps], key=lambda s: ra.ORDER.get(s.op, 1))
    assert ordered[-1].op == "SET_PAGE_TARGET" and ordered[0].op in ra.STRUCT_OPS


def test_follow_up_uses_the_last_target_and_selection_wins():
    c = ctx(last=["summary.text"])
    assert ops_of(c, "some more") == [("EXPAND_TEXT", "last:summary.text")]
    assert ops_of(c, "make it slightly shorter") == [("SHORTEN_TEXT", "last:summary.text")]
    c = ctx(selection=["experience.acme-labs.b2"], last=["summary.text"])
    assert ops_of(c, "make this more natural") == [("REWRITE_TEXT", "selection:experience.acme-labs.b2")]


def test_lines_are_understood():
    c = ctx()
    u = ra.understand(c, "Make the summary about 4 lines and focus more on backend development", None)
    s = u.steps[0]
    assert s.op in ("EXPAND_TEXT", "CHANGE_EMPHASIS") and s.params["lines"] == 4 and "backend" in s.params["focus"]


def test_text_targets_exclude_tech_lines_and_user_edits_unless_selected():
    content = doc()
    content["sections"][1]["entries"][0]["bullets"][1]["origin"] = "user"
    content["sections"][1]["entries"][0]["bullets"].append(
        {"id": "experience.acme-labs.b9", "text": "Technologies: Python", "tech_line": True}
    )
    c = ctx(content)
    ids = [i for i, _ in ra.resolve_text_targets(c, "section:section.1", False)]
    assert ids == ["experience.acme-labs.b1"]
    ids = [i for i, _ in ra.resolve_text_targets(c, "selection:experience.acme-labs.b2", False)]
    assert ids == ["experience.acme-labs.b2"]


def test_deterministic_shorten_and_expand_only_use_verified_facts():
    c = ctx()
    step = ra.Step("SHORTEN_TEXT", "section:summary", {"lines": 1, "request": "shorter"})
    ops, _, _ = ra.plan_text_edits(c, step, None)
    assert ops and len(ops[0]["text"]) < len(SUMMARY)
    step = ra.Step("EXPAND_TEXT", "section:summary", {"lines": None, "request": "longer"})
    ops, notes, _ = ra.plan_text_edits(c, step, None)
    assert ops and ops[0]["text"].startswith(SUMMARY) and "FastAPI" in ops[0]["text"]
    assert notes  # says it was built from verified facts


def test_model_rewrite_is_truth_checked():
    from tests.conftest import FakeLLM

    c = ctx()
    fake = FakeLLM([{"items": {"summary.text": SUMMARY + " Led a team of 12 engineers using Kubernetes."}}])
    step = ra.Step("EXPAND_TEXT", "section:summary", {"lines": 3, "request": "longer"})
    ops, notes, _ = ra.plan_text_edits(c, step, fake)
    assert not ops and "unchanged" in notes[0]
    fake = FakeLLM([{"items": {"summary.text": SUMMARY + " Built REST APIs with FastAPI and PostgreSQL."}}])
    ops, notes, _ = ra.plan_text_edits(c, step, fake)
    assert ops and "FACTS" in fake.prompts[0][1]


def test_model_understanding_is_validated_against_the_document():
    from tests.conftest import FakeLLM

    c = ctx()
    fake = FakeLLM(
        [
            {
                "steps": [
                    {"op": "REWRITE_TEXT", "target": "element:summary.text", "params": {"request": "x"}},
                    {"op": "REMOVE_SECTION", "target": "section.99"},  # does not exist: dropped
                    {"op": "DELETE_EVERYTHING", "target": ""},  # not an operation: dropped
                    {"op": "REMOVE_LINK", "target": "github"},
                ]
            }
        ]
    )
    u = ra.understand(c, "Tidy this up the way we discussed in the other tab", fake)
    assert u.used_model and [(s.op, s.target) for s in u.steps] == [
        ("REWRITE_TEXT", "element:summary.text"),
        ("REMOVE_LINK", "github"),
    ]


def test_names_are_matched_by_distinctive_word_and_model_cannot_substitute_entries():
    from tests.conftest import FakeLLM

    content = doc()
    content["sections"][2]["entries"][2]["fields"]["short_name"] = ""  # only "Resolve Issue Platform"
    c = ctx(content)
    assert ops_of(c, "put Resolve first") == [("REORDER_ENTRY", "projects.resolve")]
    # a project that is not on the resume must not be swapped for a different one
    fake = FakeLLM([{"steps": [{"op": "REORDER_ENTRY", "target": "projects.commitments", "params": {"position": 1}}]}])
    u = ra.understand(c, "put Zephyr second", fake)
    assert not u.steps and "Zephyr" in u.notes[0]


def test_nouns_decide_the_scope():
    c = ctx(last=["summary.text"])
    # "this bullet" with nothing selected never silently edits the summary
    u = ra.understand(c, "Make this bullet more natural", None)
    assert not u.steps and "Which bullet" in u.question
    c = ctx(selection=["experience.acme-labs.b1"])
    assert ops_of(c, "Make this section shorter") == [("SHORTEN_TEXT", "section:section.1")]
    assert ops_of(c, "Add more detail to this bullet") == [("EXPAND_TEXT", "selection:experience.acme-labs.b1")]


def test_removing_a_named_project_removes_only_that_project():
    c = ctx()
    assert ops_of(c, "Drop the EventSphere project") == [("REMOVE_ENTRY", "projects.eventsphere")]
    assert ops_of(c, "Remove the projects section") == [("REMOVE_SECTION", "section.2")]


def test_use_only_n_projects_keeps_the_first_n():
    c = ctx()
    u = ra.understand(c, "Use only two projects", None)
    assert [(s.op, s.target, s.params) for s in u.steps] == [("LIMIT_ENTRIES", "section.2", {"n": 2})]
    ops, _ = ra.structural_ops(c, u.steps[0])
    assert ops == [{"op": "remove_entry", "id": "projects.resolve"}]
