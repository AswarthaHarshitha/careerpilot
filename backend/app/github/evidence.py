"""Turns repository contents into evidence records with an explicit trust level.

VERIFIED      - stated by the repository itself: a dependency in a manifest, a file that exists (Dockerfile,
                CI workflow, tests), measured language share, or an explicit statement in the README.
INFERRED      - a reasonable guess (e.g. a module named ``permissions.py`` suggests access control, or a
                model's reading of the README). Requires the user's review before it is used as a claim.
USER_PROVIDED - entered or confirmed by the user (created elsewhere).
"""

import json
import re
import tomllib
from dataclasses import dataclass, field

from app.matching.taxonomy import (
    CONCEPT_FILE_HINTS,
    FILE_MARKERS,
    GITHUB_LANGUAGES,
    PACKAGE_SKILLS,
    SKILLS,
    category_of,
    display_name,
    find_skills,
)

MANIFEST_NAMES = {
    "package.json", "requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "pyproject.toml", "Pipfile",
    "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml", "Gemfile", "composer.json",
    "environment.yml", "setup.py",
}
MAX_MANIFESTS = 8
SKIP_DIRS = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.venv|venv|site-packages|\.next|coverage|target)/")

EXTENSION_LANGUAGES = {
    ".py": "Python", ".ipynb": "Jupyter Notebook", ".java": "Java", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".cpp": "C++",
    ".cc": "C++", ".hpp": "C++", ".c": "C", ".h": "C", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".kt": "Kotlin",
    ".swift": "Swift", ".rb": "Ruby", ".php": "PHP", ".scala": "Scala", ".r": "R", ".m": "MATLAB", ".sh": "Shell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".dart": "Dart", ".sql": "TSQL", ".vue": "Vue",
    ".svelte": "Svelte", ".tf": "HCL", ".cds": "CAP CDS", ".abap": "ABAP", ".sol": "Solidity",
}
SOURCE_RANK = {"dependency": 0, "file_tree": 1, "language": 2, "readme": 3, "metadata": 4, "user": 0, "llm": 9}
DEPLOY_HOSTS = re.compile(r"https?://[\w.-]+\.(?:vercel\.app|onrender\.com|netlify\.app|herokuapp\.com|github\.io|"
                          r"fly\.dev|railway\.app|pages\.dev|streamlit\.app|azurewebsites\.net|web\.app|"
                          r"firebaseapp\.com|hf\.space)[^\s)\"'>\]]*", re.I)


@dataclass
class EvidenceItem:
    kind: str
    label: str
    canonical: str
    category: str
    status: str
    source_type: str
    source_detail: str
    snippet: str = ""


@dataclass
class RepoAnalysis:
    languages: dict[str, int] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    readme_excerpt: str = ""
    file_count: int = 0


def select_manifests(paths: list[str]) -> list[str]:
    candidates = [p for p in paths if p.rsplit("/", 1)[-1] in MANIFEST_NAMES and not SKIP_DIRS.search(p + "/")]
    candidates.sort(key=lambda p: (p.count("/"), p))
    return [p for p in candidates if p.count("/") <= 2][:MAX_MANIFESTS]


def select_readme(paths: list[str]) -> str | None:
    root = [p for p in paths if "/" not in p and re.fullmatch(r"readme(\.(md|markdown|rst|txt))?", p, re.I)]
    return sorted(root, key=lambda p: (not p.lower().endswith(".md"), p))[0] if root else None


def language_breakdown(tree: list[tuple[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for path, size in tree:
        if SKIP_DIRS.search(path + "/") or re.search(r"(\.min\.js|\.lock|lock\.json)$", path):
            continue
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        lang = EXTENSION_LANGUAGES.get(ext)
        if lang:
            out[lang] = out.get(lang, 0) + size
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def parse_dependencies(path: str, content: str) -> list[str]:
    """Package names declared in a manifest (best effort, never executes anything)."""
    name = path.rsplit("/", 1)[-1]
    names: list[str] = []
    try:
        if name in ("package.json", "composer.json"):
            data = json.loads(content)
            for key in ("dependencies", "devDependencies", "peerDependencies", "require", "require-dev"):
                names += list((data.get(key) or {}).keys())
        elif name.startswith("requirements") or name == "environment.yml":
            for line in content.splitlines():
                line = line.split("#", 1)[0].strip().lstrip("- ")
                if not line or line.startswith(("-", "git+", "http")):
                    continue
                m = re.match(r"([A-Za-z0-9_.\-\[\]]+)", line)
                if m:
                    names.append(re.sub(r"\[.*\]", "", m.group(1)))
        elif name == "pyproject.toml":
            data = tomllib.loads(content)
            deps = list(data.get("project", {}).get("dependencies", []))
            for group in (data.get("project", {}).get("optional-dependencies") or {}).values():
                deps += group
            for group in (data.get("dependency-groups") or {}).values():
                deps += [d for d in group if isinstance(d, str)]
            poetry = data.get("tool", {}).get("poetry", {})
            deps += list((poetry.get("dependencies") or {}).keys()) + list((poetry.get("dev-dependencies") or {}).keys())
            for d in deps:
                m = re.match(r"([A-Za-z0-9_.\-]+)", d)
                if m:
                    names.append(m.group(1))
        elif name == "Pipfile":
            data = tomllib.loads(content)
            names += list((data.get("packages") or {}).keys()) + list((data.get("dev-packages") or {}).keys())
        elif name == "setup.py":
            m = re.search(r"install_requires\s*=\s*\[(.*?)\]", content, re.S)
            if m:
                names += re.findall(r"['\"]([A-Za-z0-9_.\-]+)", m.group(1))
        elif name == "pom.xml":
            # Regex rather than an XML parser: repository files are untrusted input.
            names += re.findall(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", content)
        elif name.startswith("build.gradle"):
            for m in re.finditer(r"['\"]([\w.\-]+):([\w.\-]+)(?::[^'\"]*)?['\"]", content):
                names.append(m.group(2))
        elif name == "go.mod":
            names += re.findall(r"^\s*(?:require\s+)?([\w./\-]+)\s+v[\d.]+", content, re.M)
        elif name == "Cargo.toml":
            data = tomllib.loads(content)
            names += list((data.get("dependencies") or {}).keys())
        elif name == "Gemfile":
            names += re.findall(r"^\s*gem\s+['\"]([\w\-]+)['\"]", content, re.M)
    except (ValueError, tomllib.TOMLDecodeError, AttributeError):
        return []
    return [n.lower() for n in names if n]


def clean_readme(text: str) -> str:
    t = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)  # images/badges
    t = re.sub(r"<img[^>]*>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\((?:[^)]*)\)", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start) + 1)
    right_candidates = [i for i in (text.find("\n", end), text.find(". ", end)) if i >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 if left >= 0 else 0 : right + 1].strip(" -*#\n")[:240]


# README sections that describe what the project does NOT do or might do later.
_NON_CLAIM_SECTIONS = re.compile(
    r"^(#+)\s*(?:future|roadmap|limitations?|known (?:issues|limitations)|not (?:included|implemented|in scope)|"
    r"out of scope|next steps|todo|to-do|planned|trade-?offs?|what'?s not|non-goals|alternatives considered)[^\n]*\n"
    r".*?(?=^#{1,6}\s|\Z)",
    re.I | re.M | re.S,
)
_INLINE_IDENTIFIER = re.compile(r"`[^`\n]*[/_.\\=()][^`\n]*`")
_NEGATION = re.compile(
    r"\b(?:no|not|without|instead of|rather than|never|unlike|avoid(?:ed|s)?|replac(?:e|ed|es|ing)|isn'?t|aren'?t|"
    r"doesn'?t|don'?t|won'?t|lacks?|except|dropped|removed|deliberately not|in place of|vs\.?|versus)\b",
    re.I,
)
_HYPOTHETICAL = re.compile(r"\b(?:if|would|could|might|may|future|later|planned|plan to|consider(?:ed|ing)?|"
                           r"eventually|someday|roadmap|todo|should)\b", re.I)


def statements_only(text: str) -> str:
    """Removes README parts that are not statements about the project as built: roadmap/limitation sections and
    inline code spans that hold identifiers or paths (``bootstrap_admin.py`` is not Bootstrap)."""
    t = _NON_CLAIM_SECTIONS.sub(" ", text)
    return _INLINE_IDENTIFIER.sub(" ", t)


def is_negated_or_hypothetical(text: str, start: int, end: int) -> bool:
    """True when the mention sits in a clause such as "no microservices" or "if load demands a queue"."""
    clause_start = max(text.rfind(ch, 0, start) for ch in ".;:\n—(!?")
    clause_end_candidates = [i for i in (text.find(ch, end) for ch in ".;\n—)!?") if i >= 0]
    clause_end = min(clause_end_candidates) if clause_end_candidates else len(text)
    before = text[clause_start + 1 : start]
    clause = text[clause_start + 1 : clause_end]
    if _NEGATION.search(" ".join(before.split()[-6:])):
        return True
    return bool(_HYPOTHETICAL.search(clause))


def _section_lines(readme: str, heading_rx: str, limit: int = 8) -> list[str]:
    m = re.search(rf"^#+\s*(?:{heading_rx})[^\n]*\n(.*?)(?=^#+\s|\Z)", readme, re.I | re.M | re.S)
    if not m:
        return []
    lines = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if re.match(r"^[-*+]\s+|^\d+\.\s+", ln):
            item = re.sub(r"^[-*+]\s+|^\d+\.\s+", "", ln)
            item = re.sub(r"\*\*|__|`", "", item).strip()
            if 12 <= len(item) <= 240:
                lines.append(item)
        if len(lines) >= limit:
            break
    return lines


def analyze_repository(
    *,
    description: str,
    homepage: str,
    topics: list[str],
    tree: list[tuple[str, int]],
    readme: str | None,
    manifests: dict[str, str],
) -> RepoAnalysis:
    out = RepoAnalysis()
    paths = [p for p, _ in tree]
    out.file_count = len(paths)
    items: list[EvidenceItem] = []

    # Languages measured from the file tree.
    langs = language_breakdown(tree)
    out.languages = langs
    total = sum(langs.values()) or 1
    for lang, size in langs.items():
        share = size / total
        key = GITHUB_LANGUAGES.get(lang)
        if key and (share >= 0.03 or size >= 20_000):
            items.append(EvidenceItem("technology", display_name(key), key, category_of(key), "verified", "language",
                                      f"{round(share * 100)}% of source by size"))

    # Declared dependencies.
    for path, content in manifests.items():
        for pkg in parse_dependencies(path, content):
            for key in PACKAGE_SKILLS.get(pkg, ()):
                items.append(EvidenceItem("technology", display_name(key), key, category_of(key), "verified",
                                          "dependency", f"{path}: {pkg}"))

    # Files that exist in the repository.
    for rx, keys, reason in FILE_MARKERS:
        hit = next((p for p in paths if rx.search(p) and not SKIP_DIRS.search(p + "/")), None)
        if hit:
            for key in keys:
                kind = "testing" if category_of(key) == "testing" else "technology"
                items.append(EvidenceItem(kind, display_name(key), key, category_of(key), "verified", "file_tree",
                                          f"{reason} ({hit})"))
    test_files = [p for p in paths if re.search(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.\w+$|(^|/)test_\w+\.py$",
                                                 p) and not SKIP_DIRS.search(p + "/")]
    if test_files:
        items.append(EvidenceItem("testing", "Automated tests", "testing", "testing", "verified", "file_tree",
                                  f"{len(test_files)} test files"))

    # Concepts suggested by module names: inferred only.
    for rx, key, reason in CONCEPT_FILE_HINTS:
        hit = next((p for p in paths if rx.search(p) and not SKIP_DIRS.search(p + "/")), None)
        if hit:
            items.append(EvidenceItem("concept", display_name(key), key, category_of(key), "inferred", "file_tree",
                                      f"{reason} ({hit})"))

    # Explicit statements in the README and repository description.
    text = clean_readme(readme or "")
    out.readme_excerpt = text[:2000]
    combined = (description + "\n" + " ".join(topics) + "\n" + text)[:60_000]
    statement_text = statements_only(combined)
    seen_readme: set[str] = set()
    for m in find_skills(statement_text, include_weak=False):
        if m.key in seen_readme or is_negated_or_hypothetical(statement_text, m.start, m.end):
            continue
        seen_readme.add(m.key)
        kind = "concept" if category_of(m.key) in ("concept", "security") else "technology"
        items.append(EvidenceItem(kind, display_name(m.key), m.key, category_of(m.key), "verified", "readme",
                                  "stated in README/description", _sentence_around(statement_text, m.start, m.end)))

    for feat in _section_lines(text, r"features?|what it does|highlights|capabilities|key features"):
        items.append(EvidenceItem("feature", feat[:200], "", "", "verified", "readme", "README features section"))
    for arch in _section_lines(text, r"architecture|design|how it works|system design", limit=5):
        items.append(EvidenceItem("architecture", arch[:200], "", "", "verified", "readme", "README architecture section"))

    deploy_urls = []
    if homepage and homepage.startswith("http"):
        deploy_urls.append((homepage, "metadata", "repository homepage"))
    for m in DEPLOY_HOSTS.finditer(readme or ""):
        deploy_urls.append((m.group().rstrip(".,"), "readme", "deployment link in README"))
    seen_urls = set()
    for url, src, detail in deploy_urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append(EvidenceItem("deployment", url, "", "", "verified", src, detail))

    out.evidence = dedupe(items)
    return out


def dedupe(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """One row per (canonical or label, source type); the strongest source first."""
    seen: set[tuple[str, str]] = set()
    out = []
    for it in sorted(items, key=lambda i: (SOURCE_RANK.get(i.source_type, 5), i.kind)):
        k = (it.canonical or it.label.lower(), it.source_type)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def known_skill(key: str) -> bool:
    return key in SKILLS
