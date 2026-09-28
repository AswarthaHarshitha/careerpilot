"""Low-level LaTeX scanning helpers: comments, brace groups, macro arguments, escaping, plain-text conversion."""

import re
from dataclasses import dataclass

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESCAPE_RX = re.compile(r"[\\&%$#_{}~^]")


def escape(text: str) -> str:
    """Escapes plain text for insertion into LaTeX. Generated content always passes through here."""
    text = (text or "").replace("\r", "")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    out = _ESCAPE_RX.sub(lambda m: _ESCAPES[m.group()], text)
    # LaTeX's own dashes render in every font encoding; the Unicode characters may not
    return out.replace("\u2014", "---").replace("\u2013", "--")


def escape_url(url: str) -> str:
    """Escapes a URL for \\href{...}{}; only characters that break the argument are escaped."""
    url = re.sub(r"[\s{}\\]", "", url or "")
    return url.replace("%", r"\%").replace("#", r"\#")


def comment_mask(src: str) -> list[bool]:
    """True for every character that is inside a % comment."""
    mask = [False] * len(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                mask[k] = True
            i = j
            continue
        i += 1
    return mask


def strip_comments(src: str) -> str:
    mask = comment_mask(src)
    return "".join(ch for ch, m in zip(src, mask, strict=True) if not m)


def match_brace(src: str, open_idx: int, mask: list[bool] | None = None) -> int:
    """Index of the '}' matching the '{' at open_idx, or -1."""
    depth = 0
    i, n = open_idx, len(src)
    while i < n:
        if mask is not None and mask[i]:
            i += 1
            continue
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


@dataclass
class Arg:
    start: int  # index of '{'
    end: int  # index of matching '}'
    optional: bool = False

    def inner(self, src: str) -> str:
        return src[self.start + 1 : self.end]


def read_args(src: str, pos: int, mask: list[bool] | None = None, max_args: int = 9) -> tuple[list[Arg], int]:
    """Reads consecutive {..} (and [..]) groups after a macro name. Whitespace and newlines may separate them,
    but a blank line ends the argument list."""
    args: list[Arg] = []
    i, n = pos, len(src)
    while i < n and len([a for a in args if not a.optional]) < max_args:
        j = i
        newlines = 0
        while j < n and (src[j] in " \t\r\n" or (mask is not None and mask[j])):
            if src[j] == "\n":
                newlines += 1
            j += 1
        if newlines > 1 or j >= n:
            break
        if src[j] == "{":
            end = match_brace(src, j, mask)
            if end < 0:
                break
            args.append(Arg(j, end))
            i = end + 1
        elif src[j] == "[" and not args:
            end = src.find("]", j)
            if end < 0:
                break
            args.append(Arg(j, end, optional=True))
            i = end + 1
        else:
            break
    return [a for a in args if not a.optional], i


MACRO_RX = re.compile(r"\\([A-Za-z@]+\*?)")


@dataclass
class MacroCall:
    name: str
    start: int
    name_end: int
    args: list[Arg]
    end: int  # exclusive


def iter_macros(src: str, start: int = 0, end: int | None = None, mask: list[bool] | None = None):
    end = len(src) if end is None else end
    mask = mask if mask is not None else comment_mask(src)
    for m in MACRO_RX.finditer(src, start, end):
        if mask[m.start()]:
            continue
        # skip escaped backslash sequences like "\\section"
        k, bs = m.start() - 1, 0
        while k >= 0 and src[k] == "\\":
            bs += 1
            k -= 1
        if bs % 2 == 1:
            continue
        args, after = read_args(src, m.end(), mask)
        yield MacroCall(m.group(1), m.start(), m.end(), args, after)


# Commands whose arguments are layout, not text (dropped with their argument).
_TEXT_DROP = re.compile(
    r"\\(?:vspace|hspace|setlength|addtolength|fontsize|color|rule|includegraphics|label|ref|cite|pagestyle|"
    r"thispagestyle|raisebox|definecolor|hypersetup)\*?(?:\[[^\]]*\])?(?:\{(?:[^{}]|\{[^{}]*\})*\})?(?:\{[^{}]*\})?"
)
# Switches that only change appearance; the text that follows (or their braced argument) is kept.
_SWITCHES = re.compile(
    r"\\(?:newline|linebreak|hfill|vfill|small|large|Large|LARGE|huge|Huge|scriptsize|footnotesize|normalsize|tiny|"
    r"scshape|bfseries|itshape|centering|raggedright|raggedleft|noindent|par|quad|qquad|selectfont)\b\*?"
)
_ICON_RX = re.compile(r"\\fa[A-Za-z]+\*?|\\(?:faicon|icon)\{[^{}]*\}|\\(?:Mobilefone|Letter|Email|Telefon)\b")


def to_plain(tex: str) -> str:
    """Best-effort conversion of a LaTeX fragment to readable plain text (for analysis and display)."""
    s = strip_comments(tex)
    s = _ICON_RX.sub(" ", s)
    s = re.sub(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?(?:\{(?:[^{}]|\{[^{}]*\})*\})*", " ", s)
    s = re.sub(r"\\multicolumn\{[^{}]*\}\{(?:[^{}]|\{[^{}]*\})*\}", " ", s)
    s = re.sub(r"\\href\{[^{}]*\}\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)
    s = re.sub(r"\\url\{([^{}]*)\}", r"\1", s)
    s = _TEXT_DROP.sub(" ", s)
    s = _SWITCHES.sub(" ", s)
    s = s.replace("\\\\", "\n")
    s = s.replace("\\ ", " ")
    s = re.sub(r"\$\|\$|\$\\vert\$|\\textbar\{?\}?", "|", s)
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = s.replace("---", "\u2014").replace("--", "\u2013").replace("``", '"').replace("''", '"')
    s = re.sub(r"\\([&%$#_{}])", r"\1", s)
    s = s.replace("\\textasciitilde{}", "~").replace("~", " ")
    s = re.sub(r"\\[A-Za-z@]+\*?", " ", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def find_hrefs(tex: str) -> list[tuple[str, str]]:
    """(url, plain link text) for every \\href in the fragment."""
    out = []
    mask = comment_mask(tex)
    for call in iter_macros(tex, mask=mask):
        if call.name == "href" and len(call.args) >= 2:
            out.append((call.args[0].inner(tex).strip(), to_plain(call.args[1].inner(tex))))
        elif call.name == "url" and call.args:
            u = call.args[0].inner(tex).strip()
            out.append((u, u))
    return out
