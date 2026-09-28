"""Overleaf project archives (.zip): find the main document, inline its \\input/\\include files and keep the
class/style files it needs. Everything is read as text; nothing in the archive is executed."""

import io
import posixpath
import re
import zipfile

from app.core.errors import AppError

MAX_TOTAL = 10_000_000
AUX_EXT = (".cls", ".sty", ".bib", ".bst", ".def", ".cfg", ".clo")


def from_zip(data: bytes) -> tuple[str, dict[str, str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AppError("That .zip file could not be opened.", code="invalid_template") from exc
    infos = [i for i in z.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
    if sum(i.file_size for i in infos) > MAX_TOTAL or len(infos) > 300:
        raise AppError("The project archive is too large.", code="invalid_template")
    texts: dict[str, str] = {}
    for i in infos:
        name = posixpath.normpath(i.filename)
        if name.startswith("..") or name.startswith("/"):
            continue
        if name.endswith((".tex",) + AUX_EXT):
            texts[name] = z.read(i).decode("utf-8", errors="replace")
    mains = [n for n, t in texts.items() if n.endswith(".tex") and "\\documentclass" in t and "\\begin{document}" in t]
    if not mains:
        raise AppError("No main .tex file (with \\documentclass and \\begin{document}) was found in the archive.",
                       code="invalid_template")
    main = sorted(mains, key=lambda n: (posixpath.basename(n) != "main.tex", n.count("/"), n))[0]
    base = posixpath.dirname(main)

    def inline(src: str, depth: int = 0) -> str:
        if depth > 5:
            return src

        def repl(m: re.Match) -> str:
            ref = m.group(2).strip()
            cand = posixpath.normpath(posixpath.join(base, ref))
            for c in (cand, cand + ".tex"):
                if c in texts:
                    return "\n" + inline(texts[c], depth + 1) + "\n"
            return m.group(0)

        return re.sub(r"\\(input|include)\s*\{([^}]+)\}", repl, src)

    src = inline(texts[main])
    aux = {posixpath.basename(n): t for n, t in texts.items() if n.endswith(AUX_EXT)}
    return src, aux
