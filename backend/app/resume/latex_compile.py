"""Compiles LaTeX to PDF in an isolated temporary directory.

Templates are uploaded files and are treated as untrusted: constructs that read or write files outside the
build directory or run shell commands are rejected before compilation, shell escape is never enabled, and
Tectonic runs with --untrusted.
"""

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from pypdf import PdfReader

from app.core.config import get_settings
from app.resume.latex_syntax import strip_comments

log = logging.getLogger(__name__)

_DANGEROUS = [
    (re.compile(r"\\write18|\\immediate\s*\\write|\\openout|\\openin|\\read\s*\d|\\directlua|\\luaexec|"
                r"\\ShellEscape|\\pdfshellescape"),
     "runs shell commands or writes files"),
    (re.compile(r"\\(?:input|include|InputIfFileExists|IfFileExists|lstinputlisting|verbatiminput|import|subimport|"
                r"includegraphics|includepdf|addbibresource|bibliography)\s*(?:\[[^\]]*\])?\s*\{\s*(?:/|~|[A-Za-z]:\\|"
                r"[^}]*\.\.)"),
     "reads files outside the template"),
    (re.compile(r"\\(?:include|includeonly)\s*\{"), "includes other .tex files, which are not uploaded"),
]
# Local file inputs a single uploaded file cannot satisfy (distribution files like glyphtounicode are fine).
_LOCAL_INPUT = re.compile(r"\\input\s*\{\s*([^}]+)\}")
_ALLOWED_INPUTS = {"glyphtounicode", "glyphtounicode.tex"}


def check_template_safety(src: str) -> list[str]:
    body = strip_comments(src)
    problems = []
    for rx, why in _DANGEROUS:
        m = rx.search(body)
        if m:
            problems.append(f"The template uses `{m.group().strip()[:40]}`, which {why}. Remove it and upload again.")
    for m in _LOCAL_INPUT.finditer(body):
        name = m.group(1).strip()
        if name not in _ALLOWED_INPUTS:
            problems.append(f"The template inputs `{name}`. Only a single self-contained .tex file can be used; "
                            "paste that file's contents into the template instead.")
    return problems


# pdfTeX-only primitives that XeTeX (used by Tectonic) lacks. Defining them as no-ops does not change layout.
_COMPAT_SHIM = (
    "\\ifdefined\\pdfglyphtounicode\\else\\def\\pdfglyphtounicode#1#2{}\\fi\n"
    "\\ifdefined\\pdfgentounicode\\else\\newcount\\pdfgentounicode\\fi\n"
)


@dataclass
class CompileResult:
    pdf: bytes | None
    page_count: int | None
    error: str
    engine: str


def resolve_engine() -> str | None:
    pref = get_settings().latex_engine
    if pref in ("tectonic", "pdflatex"):
        return pref if shutil.which(pref) else None
    for eng in ("tectonic", "pdflatex"):
        if shutil.which(eng):
            return eng
    return None


def compile_latex(src: str) -> CompileResult:
    engine = resolve_engine()
    if engine is None:
        return CompileResult(None, None, "No LaTeX engine is installed on the server (install Tectonic).", "")
    problems = check_template_safety(src)
    if problems:
        return CompileResult(None, None, " ".join(problems), engine)
    timeout = get_settings().latex_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="cp-latex-") as tmp:
        tex_path = os.path.join(tmp, "resume.tex")
        text = _COMPAT_SHIM + src if engine == "tectonic" else src
        with open(tex_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", tmp),
            "LANG": "C.UTF-8",
        }
        for k in ("XDG_CACHE_HOME", "TECTONIC_CACHE_DIR"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        if engine == "tectonic":
            cmd = ["tectonic", "-X", "compile", "--untrusted", "--keep-logs", "resume.tex"]
        else:
            env.update({"openin_any": "p", "openout_any": "p", "shell_escape": "f"})
            cmd = ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                cmd, cwd=tmp, env=env, capture_output=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return CompileResult(None, None, "LaTeX compilation took too long and was stopped.", engine)
        pdf_path = os.path.join(tmp, "resume.pdf")
        if proc.returncode != 0 or not os.path.exists(pdf_path):
            log_text = ""
            log_path = os.path.join(tmp, "resume.log")
            if os.path.exists(log_path):
                with open(log_path, encoding="utf-8", errors="replace") as fh:
                    log_text = fh.read()
            return CompileResult(None, None, _summarize_error(log_text, proc.stderr.decode(errors="replace")), engine)
        with open(pdf_path, "rb") as fh:
            pdf = fh.read()
    return CompileResult(pdf, count_pages(pdf), "", engine)


def count_pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _summarize_error(log_text: str, stderr: str) -> str:
    lines = []
    for block in re.finditer(r"^! (.+?)$(?:\n(l\.\d+.*))?", log_text, re.M):
        msg = block.group(1).strip()
        where = (block.group(2) or "").strip()
        lines.append(f"{msg} {where}".strip())
    if not lines:
        for ln in stderr.splitlines():
            if ln.startswith("error:") and "downloading" not in ln:
                lines.append(ln[6:].strip())
    detail = "; ".join(dict.fromkeys(lines))[:600] or "unknown error"
    return f"LaTeX could not compile the resume: {detail}"
