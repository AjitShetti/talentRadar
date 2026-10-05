"""
services/resume_coverage.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
A deterministic check that a structured resume document still contains the
resume it was built from.

Why this exists
---------------
``extract_resume_structure()`` asks a model to turn resume text into the
editor's document. The model drops content without saying so — a whole section
when its token budget runs out mid-answer, a bullet or a skills row even when
it does not — and what comes back is still valid JSON, so nothing downstream
can tell. The user sees half a resume and no error.

The source text is the one thing known to be complete, so the check is made
against it: every substantial line of the upload should be recognisable
somewhere in the document. Pure functions, no LLM — cheap enough to run on
every attempt, and what the retry uses to say exactly what went missing.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Lines shorter than this are headings, separators and stray labels; they are
#: legitimately renamed ("TECHNICAL SKILLS" → "Skills") or dropped.
_MIN_LINE_TOKENS = 3

#: Share of a line's words that must appear in the document. Below 1.0 because
#: a faithful extraction still loses the odd word to a label or a separator;
#: well above what a dropped line scores from words that happen to recur
#: elsewhere in the resume.
_LINE_COVERAGE = 0.6


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a document, in no particular order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def missing_resume_lines(resume_text: str, document: dict[str, Any]) -> list[str]:
    """Lines of ``resume_text`` that ``document`` does not account for.

    Returned in source order and verbatim (stripped), so they can be handed
    straight back to the model. An empty list means the document is complete.
    """
    present = set(_tokens(" ".join(_strings(document))))
    missing: list[str] = []
    for raw_line in resume_text.splitlines():
        line = raw_line.strip()
        tokens = _tokens(line)
        if len(tokens) < _MIN_LINE_TOKENS:
            continue
        covered = sum(1 for token in tokens if token in present)
        if covered / len(tokens) < _LINE_COVERAGE:
            missing.append(line)
    return missing


def _anchor(section: dict[str, Any]) -> str:
    """A short run of the section's own content to locate it by."""
    for text in _strings(section.get("items")):
        if len(text.strip()) >= 4:
            return text.strip()[:40]
    return ""


def order_sections_by_source(resume_text: str, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put ``sections`` back in the order the resume itself uses.

    The model reorders freely — Skills drifts to the end, Education to the
    top — and a resume's section order is the author's decision, not ours. A
    section is located by its heading on a line of its own, or failing that by
    its first piece of content; one that cannot be located keeps its place
    after whichever section preceded it.
    """
    haystack = resume_text.lower()
    keyed: list[tuple[int, int, dict[str, Any]]] = []
    last = -1
    for index, section in enumerate(sections):
        position = -1
        title = str(section.get("title") or "").strip().lower()
        if title:
            heading = re.search(rf"^[ \t]*{re.escape(title)}[ \t]*:?[ \t]*$", haystack, re.MULTILINE)
            if heading:
                position = heading.start()
        if position == -1:
            anchor = _anchor(section).lower()
            position = haystack.find(anchor) if anchor else -1
        if position == -1:
            position = last
        last = position
        keyed.append((position, index, section))

    ordered = [section for _, _, section in sorted(keyed, key=lambda entry: entry[:2])]
    for order, section in enumerate(ordered):
        section["order"] = order
    return ordered
