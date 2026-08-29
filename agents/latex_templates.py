"""
agents/latex_templates.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Jake's Resume Template: the one shared LaTeX preamble used both by the LLM
resume tailor (agents/resume_tailor.py, which asks an LLM to emit a full
document in one shot) and by the deterministic structured-document renderer
below (render_resume_latex, which builds the document straight from
Resume.structured_content with no LLM call — that's what makes it cheap
enough to run on every debounced edit in the Resume Studio editor).

escape_latex() is the other shared primitive: every piece of user-typed text
must go through it before being interpolated into LaTeX source, or a bullet
containing "50% growth" or "C++ & Go" would break compilation.
"""

from __future__ import annotations

import re
from typing import Any

JAKES_TEMPLATE_PREAMBLE = r"""\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\pagestyle{fancy}
\fancyhf{} % clear all header and footer fields
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}
\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]
\newcommand{\resumeItem}[1]{\item\small{{#1 \vspace{-2pt}}}}
\newcommand{\resumeSubheading}[4]{\vspace{-2pt}\item\begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}\textbf{#1} & #2 \\\textit{\small#3} & \textit{\small #4} \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubSubheading}[2]{\item\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}\textit{\small#1} & \textit{\small #2} \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeProjectHeading}[2]{\item\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}\small#1 & #2 \\\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}"""

_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "%": r"\%",
}
_ESCAPE_RE = re.compile("|".join(re.escape(c) for c in _ESCAPE_MAP))


def escape_latex(text: str | None) -> str:
    """Escape LaTeX special characters in arbitrary user-typed text.

    Matches are found against the original input only — the replacement
    strings (which themselves contain backslashes/braces) are never
    rescanned, so this is a single safe pass rather than a cascade.
    """
    if not text:
        return ""
    return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group()], text)


def _href(url: str, label: str) -> str:
    return r"\href{" + url + r"}{\underline{" + label + r"}}"


def _section_header(title: str) -> str:
    return r"\section{" + escape_latex(title) + "}"


def _bullets_block(bullets: list[str] | None) -> str:
    items = [b for b in (bullets or []) if b and str(b).strip()]
    if not items:
        return ""
    lines = [r"\resumeItemListStart"]
    lines.extend(r"\resumeItem{" + escape_latex(b) + "}" for b in items)
    lines.append(r"\resumeItemListEnd")
    return "\n".join(lines)


def _personal_block(personal: dict[str, Any]) -> str:
    name = escape_latex(personal.get("full_name") or "Your Name")
    headline = escape_latex(personal.get("headline") or "")
    contact_parts: list[str] = []

    phone = (personal.get("phone") or "").strip()
    if phone:
        contact_parts.append(escape_latex(phone))

    email = (personal.get("email") or "").strip()
    if email:
        contact_parts.append(_href("mailto:" + email, escape_latex(email)))

    location = (personal.get("location") or "").strip()
    if location:
        contact_parts.append(escape_latex(location))

    for link in personal.get("links") or []:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        label = escape_latex(link.get("label") or url)
        display_url = url if url.startswith("http") else "https://" + url
        contact_parts.append(_href(display_url, label))

    contact_line = " $|$ ".join(contact_parts)

    lines = [r"\begin{center}", r"    \textbf{\Huge \scshape " + name + r"} \\ \vspace{1pt}"]
    if headline:
        lines.append(r"    \small \textit{" + headline + r"} \\")
    if contact_line:
        lines.append(r"    \small " + contact_line)
    lines.append(r"\end{center}")
    return "\n".join(lines)


def _render_summary(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    text = (items[0].get("text") or "").strip() if items else ""
    if not text:
        return ""
    escaped = escape_latex(" ".join(text.split()))
    return _section_header(section.get("title") or "Professional Summary") + "\n" + escaped


def _render_education(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    if not items:
        return ""
    lines = [_section_header(section.get("title") or "Education"), r"\resumeSubHeadingListStart"]
    for item in items:
        school = escape_latex(item.get("school") or "")
        location = escape_latex(item.get("location") or "")
        degree = escape_latex(item.get("degree") or "")
        dates = escape_latex(item.get("dates") or "")
        lines.append(
            r"\resumeSubheading{" + school + "}{" + location + "}{" + degree + "}{" + dates + "}"
        )
        bullets = _bullets_block(item.get("bullets"))
        if bullets:
            lines.append(bullets)
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _render_experience(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    if not items:
        return ""
    lines = [_section_header(section.get("title") or "Experience"), r"\resumeSubHeadingListStart"]
    for item in items:
        title = escape_latex(item.get("title") or "")
        dates = escape_latex(item.get("dates") or "")
        company = escape_latex(item.get("company") or "")
        location = escape_latex(item.get("location") or "")
        lines.append(
            r"\resumeSubheading{" + title + "}{" + dates + "}{" + company + "}{" + location + "}"
        )
        bullets = _bullets_block(item.get("bullets"))
        if bullets:
            lines.append(bullets)
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _render_projects(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    if not items:
        return ""
    lines = [_section_header(section.get("title") or "Projects"), r"\resumeSubHeadingListStart"]
    for item in items:
        name = escape_latex(item.get("name") or "")
        tech = escape_latex(item.get("tech") or "")
        dates = escape_latex(item.get("dates") or "")
        link = (item.get("link") or "").strip()

        title_tex = r"\textbf{" + name + "}"
        if link:
            display_url = link if link.startswith("http") else "https://" + link
            title_tex = _href(display_url, title_tex)
        heading = title_tex
        if tech:
            heading += r" $|$ \emph{" + tech + "}"

        lines.append(r"\resumeProjectHeading{" + heading + "}{" + dates + "}")
        bullets = _bullets_block(item.get("bullets"))
        if bullets:
            lines.append(bullets)
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _render_skills(section: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in section.get("items") or []:
        category = escape_latex(item.get("category") or "")
        values = [str(v) for v in (item.get("items") or []) if v and str(v).strip()]
        if not category or not values:
            continue
        rows.append(r"\textbf{" + category + "}{: " + escape_latex(", ".join(values)) + r"} \\")
    if not rows:
        return ""
    lines = [
        _section_header(section.get("title") or "Technical Skills"),
        r"\begin{itemize}[leftmargin=0.15in, label={}]",
        r"\small{\item{",
    ]
    lines.extend(rows)
    lines.append("}}")
    lines.append(r"\end{itemize}")
    return "\n".join(lines)


def _render_certifications(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    if not items:
        return ""
    lines = [_section_header(section.get("title") or "Certifications"), r"\resumeSubHeadingListStart"]
    for item in items:
        name = escape_latex(item.get("name") or "")
        issuer = escape_latex(item.get("issuer") or "")
        date = escape_latex(item.get("date") or "")
        left = name + (" -- " + issuer if issuer else "")
        lines.append(r"\resumeSubSubheading{" + left + "}{" + date + "}")
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


def _render_custom(section: dict[str, Any]) -> str:
    items = section.get("items") or []
    if not items:
        return ""
    lines = [_section_header(section.get("title") or "Additional"), r"\resumeSubHeadingListStart"]
    for item in items:
        heading = escape_latex(item.get("heading") or "")
        if heading:
            lines.append(r"\item \textbf{" + heading + "}")
        bullets = _bullets_block(item.get("bullets"))
        if bullets:
            lines.append(bullets)
    lines.append(r"\resumeSubHeadingListEnd")
    return "\n".join(lines)


_SECTION_RENDERERS = {
    "summary": _render_summary,
    "education": _render_education,
    "experience": _render_experience,
    "projects": _render_projects,
    "skills": _render_skills,
    "certifications": _render_certifications,
    "custom": _render_custom,
}


def render_resume_latex(doc: dict[str, Any]) -> str:
    """Deterministically render a structured resume document to LaTeX.

    No LLM call — this is what makes it cheap enough to run on every
    debounced edit in the Resume Studio editor. ``visible: false`` sections
    are dropped and the rest are emitted in ``order``.
    """
    personal = doc.get("personal") or {}
    sections = sorted(
        (s for s in (doc.get("sections") or []) if s.get("visible", True)),
        key=lambda s: s.get("order", 0),
    )

    body_parts = [_personal_block(personal)]
    for section in sections:
        renderer = _SECTION_RENDERERS.get(section.get("type", ""))
        if renderer is None:
            continue
        rendered = renderer(section)
        if rendered:
            body_parts.append(rendered)

    body = "\n\n".join(body_parts)
    return JAKES_TEMPLATE_PREAMBLE + "\n\\begin{document}\n\n" + body + "\n\n\\end{document}\n"
