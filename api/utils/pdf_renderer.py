"""
api/utils/pdf_renderer.py
~~~~~~~~~~~~~~~~~~~~~~~~~
A LaTeX-free resume PDF renderer, built on PyMuPDF's Story layout engine.

Why this exists
---------------
``pdflatex`` produces the nicest output, but a TeX Live install able to
compile Jake's Resume Template needs ``texlive-latex-extra`` (for
``fullpage``, ``titlesec`` and ``enumitem``) on top of the base and font
packages — well over a gigabyte in the image, which is more than a free build
tier will carry. Dropping TeX outright would have taken PDF export with it:
three of the four call sites already degrade to "LaTeX source only", and the
fourth returns 422.

So the LaTeX stays as the *source of truth* — it is what the editor shows,
what the LLM emits, and what ``pdflatex`` compiles when a host does have TeX —
and this module renders that same source to a PDF using PyMuPDF, which is
already a dependency (it is what parses uploaded resumes). Fidelity is close
but not identical: the layout is reproduced, the exact TeX vertical rhythm is
not.

Scope of the parser
-------------------
This is deliberately *not* a LaTeX implementation. It understands exactly the
macro vocabulary of ``agents/latex_templates.py`` — the template the
deterministic renderer emits and the one the tailoring prompt instructs the
model to use — plus the handful of inline commands that appear inside it.
Anything it does not recognise degrades to its plain text rather than
appearing as raw markup.
"""

from __future__ import annotations

import html
import io
import logging
import re
from typing import TYPE_CHECKING

try:
    import pymupdf  # PyMuPDF >= 1.24 — `fitz` is the deprecated alias
except ImportError:  # pragma: no cover — older PyMuPDF
    import fitz as pymupdf

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """Raised when the resume could not be turned into a PDF."""


# ---------------------------------------------------------------------------
# Inline LaTeX → HTML
# ---------------------------------------------------------------------------

#: Commands whose *content* is kept but whose formatting is dropped: they are
#: size/shape switches that Story handles through CSS instead.
_DROP_KEEPING_ARG = ("small", "large", "Large", "Huge", "huge", "normalsize", "underline")

#: Bare control words that carry no content at all.
_BARE_COMMANDS = re.compile(
    r"\\(?:scshape|raggedright|raggedbottom|vspace\*?|hspace\*?|newpage|noindent"
    r"|centering|bfseries|itshape|par|smallskip|medskip|bigskip|hfill|extracolsep"
    r"|titlerule|fill|item)\b"
)

#: ``\vspace{-4pt}``-style commands: the whole call, argument included, goes.
_SPACING_WITH_ARG = re.compile(r"\\(?:vspace\*?|hspace\*?|setlength|addtolength)\s*\{[^{}]*\}(?:\s*\{[^{}]*\})?")

#: Placeholders for characters that have to survive a later stripping pass:
#: unescaped ``$`` delimits math mode and is removed, but ``\$`` is a literal
#: dollar sign — and resumes are full of them.
_DOLLAR = "\x00USD\x00"
_PIPE = "\x00PIPE\x00"
_BREAK = "\x00BR\x00"

_ESCAPED_CHARS = {
    r"\&": "&", r"\%": "%", r"\$": _DOLLAR, r"\#": "#", r"\_": "_",
    r"\{": "{", r"\}": "}",
    r"\textbackslash{}": "\\", r"\textasciitilde{}": "~", r"\textasciicircum{}": "^",
}


def _find_group(text: str, start: int) -> tuple[str, int]:
    """Read a brace group beginning at ``start``, returning its body and end.

    Brace-aware rather than regex-based: resume bullets routinely contain
    nested groups (``\\textbf{Go}`` inside an item), and a non-greedy regex
    stops at the first closing brace, truncating the line.
    """
    if start >= len(text) or text[start] != "{":
        return "", start
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    return text[start + 1 :], len(text)


def _split_args(text: str, start: int, count: int) -> tuple[list[str], int]:
    """Read ``count`` consecutive brace groups, skipping whitespace between."""
    args: list[str] = []
    pos = start
    for _ in range(count):
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        arg, pos = _find_group(text, pos)
        args.append(arg)
    return args, pos


def _wrap_command(source: str, name: str, open_tag: str, close_tag: str) -> str:
    """Replace every ``\\name{...}`` with ``open_tag``…``close_tag``."""
    needle = "\\" + name
    out: list[str] = []
    pos = 0
    while True:
        idx = source.find(needle, pos)
        if idx == -1:
            out.append(source[pos:])
            return "".join(out)
        after = idx + len(needle)
        # Guard against matching a longer command that merely starts the same
        # way (\\text vs \\textbf).
        if after < len(source) and source[after].isalpha():
            out.append(source[pos : after])
            pos = after
            continue
        body, end = _find_group(source, after)
        if end == after:  # not a group — leave the text alone
            out.append(source[pos:after])
            pos = after
            continue
        out.append(source[pos:idx])
        out.append(open_tag + inline_to_html(body) + close_tag)
        pos = end


def inline_to_html(fragment: str) -> str:
    """Convert one LaTeX fragment to inline HTML, escaping everything else."""
    if not fragment:
        return ""

    text = fragment

    # Links first: \href{url}{label}, whose label often nests \underline{}.
    out: list[str] = []
    pos = 0
    while True:
        idx = text.find(r"\href", pos)
        if idx == -1:
            out.append(text[pos:])
            break
        args, end = _split_args(text, idx + len(r"\href"), 2)
        out.append(text[pos:idx])
        url, label = [*args, "", ""][:2]
        url = re.sub(r"\\[a-zA-Z]+\s*", "", url).strip()
        safe_url = html.escape(url, quote=True)
        # Only http(s) and mailto reach an href; anything else renders as text.
        if not re.match(r"^(https?://|mailto:)", url, re.IGNORECASE):
            out.append(inline_to_html(label))
        else:
            out.append(f'<a href="{safe_url}">{inline_to_html(label)}</a>')
        pos = end
    text = "".join(out)

    text = _SPACING_WITH_ARG.sub("", text)
    text = _wrap_command(text, "textbf", "<b>", "</b>")
    text = _wrap_command(text, "textit", "<i>", "</i>")
    text = _wrap_command(text, "emph", "<i>", "</i>")
    text = _wrap_command(text, "texttt", "<code>", "</code>")
    for name in _DROP_KEEPING_ARG:
        text = _wrap_command(text, name, "", "")

    # TeX ligatures for dashes, which resumes use in date ranges.
    text = text.replace("---", "—").replace("--", "–")
    text = text.replace("$|$", _PIPE).replace("|", _PIPE)
    text = text.replace(r"\\", _BREAK)
    text = _BARE_COMMANDS.sub("", text)

    for latex, plain in _ESCAPED_CHARS.items():
        text = text.replace(latex, plain)

    # Anything left that looks like a command is dropped rather than shown.
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    text = text.replace("{", "").replace("}", "").replace("~", " ").replace("$", "")

    # Escape, then restore the placeholders and the inline tags we generated.
    text = html.escape(text, quote=False)
    text = text.replace(_PIPE, "|").replace(_BREAK, "<br/>").replace(_DOLLAR, "$")
    for tag in ("b", "i", "code", "u"):
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    text = re.sub(r'&lt;a href="(.*?)"&gt;', r'<a href="\1">', text)
    text = text.replace("&lt;/a&gt;", "</a>")
    text = text.replace("&lt;br/&gt;", "<br/>")

    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# Block-level LaTeX → HTML
# ---------------------------------------------------------------------------

_BODY_RE = re.compile(r"\\begin\{document\}(.*)\\end\{document\}", re.DOTALL)
_CENTER_RE = re.compile(r"\\begin\{center\}(.*?)\\end\{center\}", re.DOTALL)


_MARKUP_RE = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;)")
_LOWER_RUN_RE = re.compile(r"[^\W\d_A-Z]+")


def _small_caps(markup: str) -> str:
    """Typeset inline HTML the way ``\\scshape`` does: caps, small where lowercase.

    The template sets the name and every section heading in small capitals,
    which is most of what makes it recognisable. CSS ``font-variant`` is not
    an option here: MuPDF draws it from the font's private-use glyphs, so the
    heading *looks* right and extracts as garbage — fatal for a document whose
    whole point is to be read by an ATS. Uppercasing the lowercase runs at a
    reduced size draws the same shapes and leaves real letters in the PDF.
    """
    out: list[str] = []
    for chunk in _MARKUP_RE.split(markup):
        if _MARKUP_RE.fullmatch(chunk):
            out.append(chunk)
        else:
            out.append(
                _LOWER_RUN_RE.sub(lambda m: f'<span class="sc">{m.group().upper()}</span>', chunk)
            )
    return "".join(out)


_LIGATURE_LEAD_RE = re.compile(r"f(?=[fil])")


def _unligate(markup: str) -> str:
    """Stop the layout engine fusing ``fi``/``fl``/``ff`` into ligature glyphs.

    The engine shapes those pairs into single glyphs and writes the ligature
    code point into the PDF, so "workflow" is stored as "workﬂow" — and a
    keyword search for "workflow", which is how an ATS reads a resume, misses
    it. There is no CSS switch for this in MuPDF; putting the ``f`` in its own
    span ends the shaping run, which draws the letters separately and stores
    them as themselves.
    """
    return "".join(
        chunk if _MARKUP_RE.fullmatch(chunk) else _LIGATURE_LEAD_RE.sub("<span>f</span>", chunk)
        for chunk in _MARKUP_RE.split(markup)
    )


def _header_html(body: str) -> tuple[str, str]:
    """Pull the centred name/contact header out, returning (html, remainder)."""
    match = _CENTER_RE.search(body)
    if not match:
        return "", body

    block = match.group(1)
    lines = [ln.strip() for ln in re.split(r"\\\\", block) if ln.strip()]
    parts: list[str] = []
    if lines:
        parts.append(f'<div class="name">{_small_caps(inline_to_html(lines[0]))}</div>')
    for line in lines[1:]:
        rendered = inline_to_html(line)
        if rendered:
            parts.append(f'<div class="contact">{rendered}</div>')
    return "".join(parts), body[: match.start()] + body[match.end() :]


#: Point size of each row's right-hand cell, used to size that column.
_RIGHT_CELL_PT = {"entry": 11.0, "subentry": 10.0, "project": 10.0}


def _two_column(left: str, right: str, *, cls: str) -> str:
    """One entry row: title on the left, dates flush right.

    A one-row table, because that is what the Story engine actually honours —
    it has no flexbox, and ``text-align`` on an inline span does nothing.

    Left alone, the engine sizes the columns without regard to their content,
    which wrapped "Jul 2026" onto two lines beside a long project title. TeX
    gives the right-hand column its natural width and the left the rest
    (``l@{\\extracolsep{\\fill}}r``); the width is estimated from the text to
    do the same.
    """
    right_html = inline_to_html(right)
    plain = html.unescape(re.sub(r"<[^>]+>", "", right_html))
    width = min(round(len(plain) * _RIGHT_CELL_PT.get(cls, 11.0) * 0.56) + 6, 300) if plain else 1
    return (
        f'<table class="row {cls}"><tr>'
        f'<td class="l">{inline_to_html(left)}</td>'
        f'<td class="r" style="width: {width}px">{right_html}</td>'
        "</tr></table>"
    )


#: Macros that start a new block, and therefore end any plain-text run.
_BLOCK_MACROS = frozenset({
    "section", "resumeSubheading", "resumeProjectHeading", "resumeSubSubheading",
    "resumeItem", "resumeSubItem", "resumeItemListEnd", "item",
})

#: List and environment scaffolding: structural in LaTeX, meaningless here.
_STRUCTURAL = re.compile(
    r"\\(?:resumeSubHeadingListStart|resumeSubHeadingListEnd|resumeItemListStart"
    r"|begin|end)\s*(?:\{[^{}]*\})?(?:\[[^\]]*\])?"
)


def _iter_blocks(body: str) -> Iterator[str]:
    """Walk the document body, yielding one HTML block per recognised macro.

    Text that sits between macros — the summary section renders as a bare
    escaped paragraph, and the skills block as one ``\\item`` of ``\\\\``
    separated rows — is emitted as a paragraph rather than dropped.
    """
    body = _STRUCTURAL.sub(" ", body)
    pos = 0
    bullets_open = False
    pending_start = 0

    def close_bullets() -> str:
        return "</ul>" if bullets_open else ""

    def flush_text(upto: int) -> str:
        """Render any plain text between the last macro and ``upto``."""
        raw = body[pending_start:upto]
        if not raw.strip():
            return ""
        rendered = inline_to_html(raw)
        return f'<div class="para">{rendered}</div>' if rendered else ""

    while pos < len(body):
        idx = body.find("\\", pos)
        if idx == -1:
            break

        match = re.match(r"\\([a-zA-Z]+)", body[idx:])
        if not match:
            pos = idx + 1
            continue

        name = match.group(1)
        after = idx + 1 + len(name)

        if name in _BLOCK_MACROS:
            text_block = flush_text(idx)
            if text_block:
                if bullets_open:
                    yield close_bullets()
                    bullets_open = False
                yield text_block

        if name == "section":
            arg, pos = _split_args(body, after, 1)
            if bullets_open:
                yield close_bullets()
                bullets_open = False
            heading = _small_caps(inline_to_html(arg[0]))
            yield f'<div class="section">{heading}</div><div class="rule"></div>'
        elif name == "resumeSubheading":
            args, pos = _split_args(body, after, 4)
            if bullets_open:
                yield close_bullets()
                bullets_open = False
            yield _two_column(args[0], args[1], cls="entry")
            yield _two_column(args[2], args[3], cls="subentry")
        elif name in ("resumeProjectHeading", "resumeSubSubheading"):
            args, pos = _split_args(body, after, 2)
            if bullets_open:
                yield close_bullets()
                bullets_open = False
            cls = "project" if name == "resumeProjectHeading" else "subentry"
            yield _two_column(args[0], args[1], cls=cls)
        elif name in ("resumeItem", "resumeSubItem"):
            args, pos = _split_args(body, after, 1)
            rendered = inline_to_html(args[0])
            if rendered:
                if not bullets_open:
                    yield "<ul>"
                    bullets_open = True
                yield f"<li>{rendered}</li>"
        elif name == "resumeItemListEnd":
            pos = after
            if bullets_open:
                yield close_bullets()
                bullets_open = False
        elif name == "item":
            # A bare \item inside an itemize — the Technical Skills block.
            arg, pos = _split_args(body, after, 1)
            rendered = inline_to_html(arg[0]) if arg[0] else ""
            if not rendered:
                # No braces: take the rest of the line instead.
                line_end = body.find("\n", after)
                line_end = len(body) if line_end == -1 else line_end
                rendered = inline_to_html(body[after:line_end])
                pos = line_end
            if rendered:
                if bullets_open:
                    yield close_bullets()
                    bullets_open = False
                yield f'<div class="para item">{rendered}</div>'
        else:
            pos = after
            continue  # not a block macro: the text run keeps growing

        pending_start = pos

    trailing = flush_text(len(body))
    if trailing:
        if bullets_open:
            yield close_bullets()
            bullets_open = False
        yield trailing

    if bullets_open:
        yield "</ul>"


def latex_to_html(latex_content: str) -> str:
    """Render Jake's-template LaTeX as the HTML the Story engine lays out."""
    match = _BODY_RE.search(latex_content)
    body = match.group(1) if match else latex_content

    header, body = _header_html(body)
    blocks = "".join(_iter_blocks(body))
    return f'<div class="resume">{_unligate(header + blocks)}</div>'


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

#: The template's own measurements, restated for the Story engine: an 11pt
#: serif article on Letter paper with half-inch margins; a ``\\Huge`` bold
#: small-caps name; ``\\large`` small-caps section headings over a rule;
#: ``\\small`` (10pt) bullets, sub-rows and skills; entries indented 0.15in.
#: One CSS px is one PDF point here.
_CSS = """
body { font-family: serif; font-size: 11px; color: #000; line-height: 1.1; }
.sc { font-size: 0.8em; }
.name { font-size: 25px; font-weight: bold; text-align: center; margin-bottom: 1px; }
.contact { font-size: 10px; text-align: center; margin-bottom: 1px; }
.section { font-size: 12.5px; margin-top: 6px; margin-bottom: 0px; }
.rule { border-bottom: 0.5px solid #000; margin-bottom: 3px; }
table.row { width: 100%; margin-top: 1px; }
table.subentry { margin-top: 0px; }
table.row td { padding: 0px; vertical-align: top; }
td.l { text-align: left; padding-left: 11px; }
td.r { text-align: right; white-space: nowrap; }
.entry .l { font-weight: bold; }
.subentry td { font-style: italic; font-size: 10px; }
.project td { font-size: 10px; }
.para { margin-top: 2px; }
.item { font-size: 10px; margin-left: 11px; }
ul { font-size: 10px; margin-top: 1px; margin-bottom: 2px; margin-left: 12px; }
li { margin-bottom: 0px; }
/* The template underlines its links and leaves them black
   (\\usepackage[hidelinks]{hyperref} with \\underline). ``a:link``, because
   the engine's own blue is set on that selector and a bare ``a`` loses to it. */
a:link { color: #000000; text-decoration: underline; }
"""


#: A resume longer than this is a runaway, not a resume.
_MAX_PAGES = 12


def render_html_to_pdf(body_html: str) -> bytes:
    """Lay ``body_html`` out on Letter pages and return the PDF bytes."""
    page_rect = pymupdf.paper_rect("letter")
    content_rect = page_rect + (36, 36, -36, -36)  # noqa: RUF005 - Rect arithmetic, not a list

    def next_page(page_number: int, _filled: object) -> tuple[object, object, None]:
        if page_number >= _MAX_PAGES:
            raise PdfRenderError(f"the resume runs past {_MAX_PAGES} pages")
        return page_rect, content_rect, None

    try:
        story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=_CSS)
        # write_with_links, not a bare DocumentWriter: drawing a Story page by
        # page lays the text out but leaves every <a> as plain ink, so the
        # LinkedIn and GitHub links a recruiter clicks went nowhere.
        document = story.write_with_links(next_page)
        buffer = io.BytesIO()
        document.save(buffer, garbage=3, deflate=True)
    except Exception as exc:  # broad by design: surfaced as a clean 422/warning
        raise PdfRenderError(f"The resume could not be rendered as a PDF: {exc}") from exc

    data = buffer.getvalue()
    if not data:
        raise PdfRenderError("The resume could not be rendered as a PDF.")
    return data


def render_latex_pdf(latex_content: str) -> bytes:
    """Render Jake's-template LaTeX to PDF bytes without invoking TeX."""
    if not latex_content or not latex_content.strip():
        raise PdfRenderError("No content to render.")
    body = latex_to_html(latex_content)
    if not re.search(r"[A-Za-z0-9]", re.sub(r"<[^>]+>", "", body)):
        raise PdfRenderError("The resume source contained no readable content.")
    return render_html_to_pdf(body)
