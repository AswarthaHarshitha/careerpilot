"""CareerPilot's standard resume layout, generated from the career profile.

The layout follows the user's preferred Overleaf template: centred name with icon links separated by bars,
small-caps section titles over a rule, one table per experience/project with dates or links on the right,
bullets with a closing "Technologies:" line, and tables for skills and education. It is used when a resume
is created from the profile or from a plain-text/Markdown upload. Every value comes from the user's data.
"""

import re

from app.resume.latex_syntax import escape, escape_url

PREAMBLE = r"""%-----------------------------------------------------------------------------------------------------------------------------------------------%
%   Standard CareerPilot layout (based on an MIT-licensed resume template)
%-----------------------------------------------------------------------------------------------------------------------------------------------%
\documentclass[a4paper,10pt]{article}
\usepackage{url}
\usepackage{parskip}
\RequirePackage{color}
\RequirePackage{graphicx}
\usepackage[usenames,dvipsnames]{xcolor}
\usepackage[top=0.5in,bottom=0.5in,left=0.55in,right=0.55in]{geometry}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage{lmodern}
\newcolumntype{C}{>{\centering\arraybackslash}X}
\usepackage{titlesec}
\usepackage{multicol}
\usepackage{multirow}
\titleformat{\section}{\Large\scshape\raggedright}{}{0em}{}[\titlerule]
\titlespacing{\section}{0pt}{8pt}{5pt}
\usepackage[unicode, draft=false]{hyperref}
\definecolor{linkcolour}{rgb}{0,0.2,0.6}
\hypersetup{colorlinks,breaklinks,urlcolor=linkcolour,linkcolor=linkcolour}
\usepackage{fontawesome5}

\begin{document}
\pagestyle{empty}
"""

ICONS = {"github": r"\faGithub", "linkedin": r"\faLinkedin", "email": r"\faEnvelope", "phone": r"\faMobile",
         "portfolio": r"\faGlobe"}


def _link(kind: str, url: str, text: str) -> str:
    return rf"\href{{{escape_url(url)}}}{{\raisebox{{-0.05\height}}{ICONS[kind]}\ {escape(text)}}}"


def _entry(title: str, right: str, secondary: str, location: str, bullets: list[str], tech: list[str],
           link: str = "") -> str:
    right_cell = rf"\hfill \href{{{escape_url(link)}}}{{\textit{{GitHub}}}}" if link else rf"\hfill {escape(right)}"
    rows = [r"\begin{tabularx}{\linewidth}{ @{}l r@{} }", rf"\textbf{{{escape(title)}}} & {right_cell} \\[-2pt]"]
    if secondary or location:
        rows.insert(2, rf"\underline{{\small {escape(secondary)}}} & \hfill {{\small {escape(location)}}} \\[-3pt]")
        rows[1] = rows[1].replace(r"\\[-2pt]", r"\\")
    items = [rf"    \item {escape(b)}" for b in bullets if b.strip()]
    if tech:
        items.append(rf"    \item \textbf{{Technologies:}} {escape(', '.join(tech))}.")
    if not items:
        items = [rf"    \item {escape(title)}"]
    rows += [r"\multicolumn{2}{@{}X@{}}{",
             r"\begin{itemize}[leftmargin=1em,itemsep=1pt,topsep=1pt,parsep=0pt]", *items, r"\end{itemize}", "}",
             r"\end{tabularx}"]
    return "\n".join(rows)


def build(profile: dict, experiences: list[dict], projects: list[dict], skills: dict[str, list[str]],
          educations: list[dict], certifications: list[str], achievements: list[str]) -> str:
    out = [PREAMBLE]
    links = []
    for kind, key, label in (("github", "github_url", None), ("linkedin", "linkedin_url", None),
                             ("email", "email", None), ("phone", "phone", None), ("portfolio", "portfolio_url",
                                                                                   "Portfolio")):
        val = (profile.get(key) or "").strip()
        if not val:
            continue
        if kind == "email":
            links.append(_link(kind, "mailto:" + val, val))
        elif kind == "phone":
            links.append(_link(kind, "tel:" + re.sub(r"[^\d+]", "", val), val))
        else:
            text = label or re.sub(r"^https?://(www\.)?(github\.com/|linkedin\.com/in/)?", "", val).strip("/")
            links.append(_link(kind, val, text))
    out.append(r"\begin{tabularx}{\linewidth}{@{} C @{}}")
    out.append(rf"\Huge{{{escape(profile.get('full_name') or '')}}} \\[7.5pt]")
    out.append(" \\ $|$ \n".join(links) + r" \\")
    out.append(r"\end{tabularx}")
    if profile.get("summary"):
        out += [r"\section{Summary}", escape(profile["summary"]), r"\vspace{-5pt}"]
    if experiences:
        out.append(r"\section{Work Experience}")
        blocks = [_entry(e["organization"], e.get("dates", ""), e.get("title", ""), e.get("location", ""),
                         e.get("bullets", []), e.get("tech", [])) for e in experiences]
        out.append("\n\n\\vspace{-16pt}\n\n".join(blocks))
        out.append(r"\vspace{-15pt}")
    if projects:
        out.append(r"\section{Projects}")
        blocks = [_entry(p["title"], "", "", "", p.get("bullets", []), p.get("tech", []), p.get("url", ""))
                  for p in projects]
        out.append("\n\\vspace{-12pt}\n\n".join(blocks))
        out.append(r"\vspace{-12pt}")
    if skills:
        out.append(r"\section{Technical Skills}")
        out.append(r"\begin{tabularx}{\linewidth}{@{}l X@{}}")
        out += [rf"{escape(cat)} & {escape(', '.join(items))} \\" for cat, items in skills.items() if items]
        out.append(r"\end{tabularx}")
        out.append(r"\vspace{-9pt}")
    if educations:
        out.append(r"\section{Education}")
        out.append(r"\begin{tabularx}{\linewidth}{@{}l X r@{}}")
        for e in educations:
            years = "--".join(x for x in (e.get("start", ""), e.get("end", "")) if x) or str(e.get("year") or "")
            degree = rf"\textbf{{{escape(e['degree'])}}}, " if e.get("degree") else ""
            out.append(rf"{escape(years)} & {degree}{escape(e['institution'])} & {escape(e.get('grade', ''))} \\")
        out.append(r"\end{tabularx}")
    extra = certifications + achievements
    if extra:
        title = "Certifications \\& Achievements" if certifications and achievements else (
            "Certifications" if certifications else "Achievements")
        out.append(rf"\section{{{title}}}")
        out.append(r"\begin{itemize}[nosep,leftmargin=1em,itemsep=0pt,topsep=0pt]")
        out += [rf"    \item {escape(x)}" for x in extra]
        out.append(r"\end{itemize}")
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"
