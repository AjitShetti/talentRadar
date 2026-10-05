"""
api/utils/file_parser.py
~~~~~~~~~~~~~~~~~~~~~~~
Utility to extract text from uploaded PDF and DOCX files.

The text returned here is the only thing the rest of Resume Studio ever sees
of an upload, so it has to carry what a reader of the file would see — not
just the body paragraphs. Three things a naive pass loses, and that this one
keeps: text laid out in tables (a very common resume layout), the targets of
hyperlinks (a resume says "LinkedIn", the URL lives in the link), and the
fact that a paragraph is a bullet.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any

try:
    # `pymupdf` is the current module name; `fitz` is the deprecated alias and
    # emits a warning on every import under PyMuPDF >= 1.24.
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    try:
        import fitz  # PyMuPDF < 1.24
    except ImportError:
        fitz = None

try:
    import docx
except ImportError:
    docx = None

logger = logging.getLogger(__name__)


def _bare_url(url: str) -> str:
    """A URL reduced to what a resume would print: no scheme, no ``www.``."""
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip().lower()).rstrip("/")


def _is_web_link(url: str | None) -> bool:
    return bool(url) and str(url).lower().startswith(("http://", "https://"))


def _docx_paragraph_text(paragraph: Any) -> str:
    """One paragraph as text, with link targets and a bullet marker restored."""
    parts: list[str] = []
    for item in paragraph.iter_inner_content():
        text = item.text
        parts.append(text)
        url = getattr(item, "url", None)
        # "LinkedIn" alone is useless downstream; "github.com/x" already is the link.
        if _is_web_link(url) and _bare_url(url) not in text.lower():
            parts.append(f" ({url})")
    text = "".join(parts)
    if not text.strip():
        return ""

    properties = paragraph._p.pPr
    numbered = properties is not None and properties.numPr is not None
    style = (paragraph.style.name or "") if paragraph.style is not None else ""
    if numbered or style.startswith(("List Bullet", "List Number")):
        return "- " + text.strip()
    return text


def _docx_block_lines(container: Any) -> list[str]:
    """Paragraphs and tables of ``container`` as lines, in document order.

    ``Document.paragraphs`` skips tables entirely; walking the inner content
    reads a table where it sits, and recurses into cells that nest another.
    """
    lines: list[str] = []
    for block in container.iter_inner_content():
        if hasattr(block, "rows"):  # a table
            for row in block.rows:
                seen: set[int] = set()
                cells: list[list[str]] = []
                for cell in row.cells:
                    if id(cell._tc) in seen:  # a merged cell repeats per column
                        continue
                    seen.add(id(cell._tc))
                    cell_lines = [ln for ln in _docx_block_lines(cell) if ln.strip()]
                    if cell_lines:
                        cells.append(cell_lines)
                if cells and all(len(cell_lines) == 1 for cell_lines in cells):
                    # "Title | dates" rows stay on one line, as on the page.
                    lines.append("\t".join(cell_lines[0] for cell_lines in cells))
                else:
                    lines.extend(ln for cell_lines in cells for ln in cell_lines)
        else:
            lines.append(_docx_paragraph_text(block))
    return lines


def _extract_docx(file_bytes: bytes) -> str:
    document = docx.Document(io.BytesIO(file_bytes))
    lines: list[str] = []
    # Contact details are often typed into the page header, not the body.
    for section in document.sections[:1]:
        if not section.header.is_linked_to_previous:
            lines.extend(ln for ln in _docx_block_lines(section.header) if ln.strip())
    lines.extend(_docx_block_lines(document))
    return "\n".join(lines)


def _pdf_link_lines(page: Any, page_text: str) -> list[str]:
    """``Label: url`` for each link on ``page`` whose target is not printed."""
    printed = page_text.lower()
    # One link is often several rectangles (it wraps, or changes font midway),
    # so the label is every word sitting in any of them. Matching on a word's
    # centre rather than the rectangle's text keeps the lines above and below,
    # which a link rectangle routinely overlaps, out of the label.
    areas: dict[str, list[Any]] = {}
    for link in page.get_links():
        url = link.get("uri")
        if _is_web_link(url) and _bare_url(url) not in printed:
            areas.setdefault(url, []).append(link["from"])
    if not areas:
        return []

    words = page.get_text("words")
    lines: list[str] = []
    for url, rects in areas.items():
        label = " ".join(
            word[4]
            for word in words
            if any(
                rect.x0 <= (word[0] + word[2]) / 2 <= rect.x1
                and rect.y0 <= (word[1] + word[3]) / 2 <= rect.y1
                for rect in rects
            )
        )
        lines.append(f"{label}: {url}" if label else url)
    return lines


def _extract_pdf(file_bytes: bytes) -> str:
    text_blocks: list[str] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            page_text = page.get_text()
            text_blocks.append(page_text)
            text_blocks.extend(_pdf_link_lines(page, page_text))
    return "\n".join(text_blocks)


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    """Extract text from a PDF or DOCX file represented as bytes."""
    filename_lower = filename.lower()

    if filename_lower.endswith(".pdf"):
        if fitz is None:
            raise RuntimeError("PyMuPDF (fitz) is not installed. Cannot parse PDF.")
        try:
            return _extract_pdf(file_bytes)
        except Exception as e:
            logger.error("Failed to parse PDF", exc_info=True)
            raise ValueError(f"Could not parse PDF file: {e}") from e

    elif filename_lower.endswith(".docx"):
        if docx is None:
            raise RuntimeError("python-docx is not installed. Cannot parse DOCX.")
        try:
            return _extract_docx(file_bytes)
        except Exception as e:
            logger.error("Failed to parse DOCX", exc_info=True)
            raise ValueError(f"Could not parse DOCX file: {e}") from e

    else:
        # Fallback to plain text decoding
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                # Fallback encoding if utf-8 fails
                return file_bytes.decode("latin-1")
            except Exception as e:
                raise ValueError("Unsupported file format and could not decode as text.") from e
