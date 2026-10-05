"""
tests/test_pdf_renderer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Covers the LaTeX-free resume renderer that let TeX Live out of the image.

The renderer is only worth having if it produces the *same resume*, so these
tests read the text back out of the generated PDF and assert the content
survived — headings, entries, bullets and links — rather than just that some
bytes came out. They also pin the escaping rules: this parses LLM output, so
"renders as text rather than as markup" is a correctness property.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.latex_templates import render_resume_latex
from api.utils.latex_compiler import LatexCompileError, compile_latex_to_pdf
from api.utils.pdf_renderer import (
    PdfRenderError,
    inline_to_html,
    latex_to_html,
    render_latex_pdf,
)

pymupdf = pytest.importorskip("pymupdf")


SAMPLE_DOCUMENT = {
    "personal": {
        "full_name": "Ada Lovelace",
        "headline": "Backend Engineer",
        "email": "ada@example.com",
        "phone": "+91 98765 43210",
        "location": "Bengaluru, India",
        "links": [{"label": "linkedin.com/in/ada", "url": "https://linkedin.com/in/ada"}],
    },
    "sections": [
        {
            "type": "summary", "title": "Professional Summary", "order": 0,
            "items": [{"text": "Payments infrastructure engineer. 50% throughput growth; C++ & Go."}],
        },
        {
            "type": "experience", "title": "Experience", "order": 1,
            "items": [{
                "title": "Senior Engineer", "dates": "2022 - Present",
                "company": "Stripe", "location": "Bengaluru",
                "bullets": ["Cut p99 latency 38% by batching ledger writes"],
            }],
        },
        {
            "type": "skills", "title": "Technical Skills", "order": 2,
            "items": [{"category": "Languages", "items": ["Python", "Go", "SQL"]}],
        },
    ],
}


@pytest.fixture(scope="module")
def rendered_text() -> str:
    latex = render_resume_latex(SAMPLE_DOCUMENT)
    document = pymupdf.open("pdf", render_latex_pdf(latex))
    return "\n".join(page.get_text() for page in document)


# ─────────────────────────────────────────────────────────────────────────────
# The resume survives the round trip
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:
    @pytest.mark.parametrize(
        "expected",
        [
            "ADA LOVELACE",                 # header, in small capitals
            "Backend Engineer",             # headline
            "ada@example.com",              # linked contact
            "PROFESSIONAL SUMMARY",         # section heading, in small capitals
            "50% throughput growth",        # escaped percent survives
            "C++ & Go",                     # escaped ampersand
            "Senior Engineer",              # entry title
            "Stripe",                       # entry subtitle
            "2022 - Present",               # right-hand column
            "Cut p99 latency 38%",          # bullet
            "TECHNICAL SKILLS",
            "Python, Go, SQL",              # skills row
        ],
    )
    def test_content_reaches_the_pdf(self, rendered_text, expected):
        assert expected in rendered_text

    def test_no_latex_markup_leaks_into_the_output(self, rendered_text):
        for artefact in ("\\resumeItem", "\\section", "\\textbf", "\\begin", "\\href"):
            assert artefact not in rendered_text

    def test_dates_are_right_aligned(self):
        """The two-column entry rows are the one thing a naive port loses."""
        latex = render_resume_latex(SAMPLE_DOCUMENT)
        page = pymupdf.open("pdf", render_latex_pdf(latex))[0]
        spans = [
            span
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
        ]
        title = next(s for s in spans if "Senior Engineer" in s["text"])
        dates = next(s for s in spans if "2022 - Present" in s["text"])
        assert dates["bbox"][0] > title["bbox"][2], "dates should sit right of the title"
        assert dates["bbox"][2] > page.rect.width * 0.75, "dates should be flush right"


# ─────────────────────────────────────────────────────────────────────────────
# It is the template, and an ATS can read it
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DOCUMENT = {
    "personal": {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "links": [{"label": "GitHub", "url": "https://github.com/ada"}],
    },
    "sections": [{
        "type": "projects", "title": "Projects", "order": 0,
        "items": [{
            "name": "Ledger Search — Hybrid Retrieval Engine",
            "tech": "Python · FastAPI · OpenSearch · Apache Airflow · PostgreSQL · Docker",
            "dates": "Jul 2026",
            "link": "https://github.com/ada/ledger",
            "bullets": ["Built an efficient workflow engine with offline profiling"],
        }],
    }],
}


def _spans(page: object) -> list[dict]:
    return [
        span
        for block in page.get_text("dict")["blocks"]  # type: ignore[attr-defined]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]


class TestTemplateFidelity:
    @pytest.fixture(scope="class")
    def page(self):
        return pymupdf.open("pdf", render_latex_pdf(render_resume_latex(PROJECT_DOCUMENT)))[0]

    def test_it_is_set_in_a_serif_face(self, page):
        fonts = {span["font"] for span in _spans(page)}
        assert not any("Sans" in font or "Helv" in font for font in fonts), fonts

    def test_section_headings_are_small_capitals(self, page):
        """A full-size initial followed by smaller capitals, as \\scshape sets it."""
        spans = _spans(page)
        initial = next(s for s in spans if s["text"].strip() == "P")
        rest = next(s for s in spans if s["text"].strip() == "ROJECTS")
        assert rest["size"] < initial["size"]

    def test_links_are_clickable(self, page):
        """Drawing the Story without links left LinkedIn/GitHub as dead text."""
        targets = {link.get("uri") for link in page.get_links()}
        assert {"mailto:ada@example.com", "https://github.com/ada", "https://github.com/ada/ledger"} <= targets

    def test_links_are_black_like_the_template(self, page):
        link_text = next(s for s in _spans(page) if "GitHub" in s["text"])
        assert link_text["color"] == 0

    def test_no_ligature_code_points_reach_the_text_layer(self, page):
        """ "workﬂow" does not match a search for "workflow"."""
        text = page.get_text()
        assert not [ch for ch in text if "ﬀ" <= ch <= "ﬆ"]
        for word in ("efficient", "workflow", "offline", "profiling"):
            assert word in text

    def test_a_short_date_is_not_wrapped_by_a_long_title(self, page):
        assert "Jul 2026" in page.get_text()

    def test_a_project_tech_stack_is_not_bold(self, page):
        tech = next(s for s in _spans(page) if "OpenSearch" in s["text"])
        assert "Bold" not in tech["font"]


# ─────────────────────────────────────────────────────────────────────────────
# Inline conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestInlineConversion:
    @pytest.mark.parametrize(
        "latex,expected",
        [
            (r"\textbf{Go}", "<b>Go</b>"),
            (r"\textit{lead}", "<i>lead</i>"),
            (r"\emph{lead}", "<i>lead</i>"),
            (r"\underline{x}", "x"),
            (r"50\% growth", "50% growth"),
            (r"C++ \& Go", "C++ &amp; Go"),
            (r"a \$5M portfolio", "a $5M portfolio"),
            (r"\vspace{-4pt}Text", "Text"),
            ("", ""),
        ],
    )
    def test_fragments(self, latex, expected):
        assert inline_to_html(latex) == expected

    def test_nested_groups_are_not_truncated(self):
        """A non-greedy regex stops at the first brace and eats half the line."""
        out = inline_to_html(r"\textbf{Built \textit{fast} pipelines} and shipped")
        assert "pipelines" in out and "shipped" in out

    def test_links_become_anchors(self):
        out = inline_to_html(r"\href{https://example.com}{\underline{example.com}}")
        assert out == '<a href="https://example.com">example.com</a>'

    def test_non_http_schemes_render_as_text_not_links(self):
        """This parses LLM output; a javascript: href must never survive."""
        out = inline_to_html(r"\href{javascript:alert(1)}{click me}")
        assert "href" not in out
        assert "click me" in out

    def test_html_in_the_source_is_escaped(self):
        out = inline_to_html("5 < 10 and <script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out


# ─────────────────────────────────────────────────────────────────────────────
# Block conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockConversion:
    def test_bullets_become_one_list(self):
        latex = (
            r"\section{Experience}"
            r"\resumeItemListStart"
            r"\resumeItem{First}\resumeItem{Second}"
            r"\resumeItemListEnd"
        )
        html = latex_to_html(latex)
        assert html.count("<ul>") == 1
        assert html.count("<li>") == 2

    def test_a_subheading_closes_the_previous_bullet_list(self):
        latex = (
            r"\resumeItem{First}"
            r"\resumeSubheading{Role}{Dates}{Company}{Place}"
        )
        html = latex_to_html(latex)
        assert html.index("</ul>") < html.index("Role")

    def test_unknown_macros_do_not_leak(self):
        html = latex_to_html(r"\begin{document}\somethingNew{x} plain text\end{document}")
        assert "somethingNew" not in html
        assert "plain text" in html


# ─────────────────────────────────────────────────────────────────────────────
# Failure modes
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureModes:
    def test_empty_source_is_refused(self):
        with pytest.raises(PdfRenderError):
            render_latex_pdf("   ")

    def test_a_document_with_no_readable_content_is_refused(self):
        with pytest.raises(PdfRenderError):
            render_latex_pdf(r"\documentclass{article}\begin{document}\end{document}")

    def test_renderer_failures_arrive_as_LatexCompileError(self):
        """Callers only catch LatexCompileError; anything else is a 500."""
        with (
            patch("api.utils.pdf_renderer.render_latex_pdf", side_effect=PdfRenderError("boom")),
            patch("api.utils.latex_compiler.shutil.which", return_value=None),
            pytest.raises(LatexCompileError, match="boom"),
        ):
            compile_latex_to_pdf(r"\begin{document}Hello\end{document}")


# ─────────────────────────────────────────────────────────────────────────────
# Engine selection
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineSelection:
    def _settings(self, engine: str):
        from config.settings import get_settings

        settings = get_settings().model_copy(update={"pdf_engine": engine})
        return patch("api.utils.latex_compiler.get_settings", return_value=settings)

    def test_auto_falls_back_when_pdflatex_is_absent(self):
        latex = render_resume_latex(SAMPLE_DOCUMENT)
        with self._settings("auto"), \
             patch("api.utils.latex_compiler.shutil.which", return_value=None):
            pdf = compile_latex_to_pdf(latex)
        assert pdf.startswith(b"%PDF")

    def test_builtin_is_used_even_where_pdflatex_exists(self):
        latex = render_resume_latex(SAMPLE_DOCUMENT)
        with self._settings("builtin"), \
             patch("api.utils.latex_compiler.shutil.which", return_value="/usr/bin/pdflatex"), \
             patch("api.utils.latex_compiler.subprocess.run") as run:
            pdf = compile_latex_to_pdf(latex)
        run.assert_not_called()
        assert pdf.startswith(b"%PDF")

    def test_latex_engine_refuses_to_silently_switch(self):
        with (
            self._settings("latex"),
            patch("api.utils.latex_compiler.shutil.which", return_value=None),
            pytest.raises(LatexCompileError, match="pdflatex is not installed"),
        ):
            compile_latex_to_pdf(r"\begin{document}Hello\end{document}")
