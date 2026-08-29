"""Tests for agents/latex_templates.py — the deterministic structured-document
LaTeX renderer behind the Resume Studio editor."""

from typing import Any

from agents.latex_templates import escape_latex, render_resume_latex


def test_escape_latex_handles_each_special_character() -> None:
    raw = r"50% growth in C++ & Go, cost ~$5 #1 team_lead ^ backslash\end"
    escaped = escape_latex(raw)

    assert r"\%" in escaped
    assert r"\&" in escaped
    assert r"\$" in escaped
    assert r"\#" in escaped
    assert r"\_" in escaped
    assert r"\textasciicircum{}" in escaped
    assert r"\textasciitilde{}" in escaped
    assert r"\textbackslash{}" in escaped
    # No raw special characters should survive escaping.
    for char in "%&$#_^~":
        # Each occurrence should be preceded by a backslash (or wrapped, for ^ ~ \).
        assert char not in raw or ("\\" + char) in escaped or "textas" in escaped


def test_escape_latex_handles_empty_and_none() -> None:
    assert escape_latex(None) == ""
    assert escape_latex("") == ""


def _sample_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "personal": {
            "full_name": "Jane Doe",
            "headline": "Backend Engineer",
            "email": "jane@example.com",
            "phone": "555-0100",
            "location": "Bengaluru, India",
            "links": [{"label": "GitHub", "url": "github.com/janedoe"}],
        },
        "sections": [
            {
                "id": "summary", "type": "summary", "title": "Professional Summary",
                "visible": True, "order": 0,
                "items": [{"text": "Backend engineer with 5 years building APIs."}],
            },
            {
                "id": "experience", "type": "experience", "title": "Experience",
                "visible": True, "order": 1,
                "items": [{
                    "title": "Senior Engineer", "company": "Acme Corp",
                    "location": "Remote", "dates": "2022 - Present",
                    "bullets": ["Shipped a 50% faster API (C++ & Go)"],
                }],
            },
            {
                "id": "hidden", "type": "custom", "title": "Hidden Section",
                "visible": False, "order": 2,
                "items": [{"heading": "Should not appear", "bullets": ["nope"]}],
            },
        ],
    }


def test_render_resume_latex_produces_compilable_looking_document() -> None:
    latex = render_resume_latex(_sample_document())

    assert latex.startswith(r"\documentclass")
    assert r"\begin{document}" in latex
    assert r"\end{document}" in latex
    assert "Jane Doe" in latex
    assert "Backend engineer with 5 years building APIs." in latex
    assert "Acme Corp" in latex


def test_render_resume_latex_omits_hidden_sections() -> None:
    latex = render_resume_latex(_sample_document())

    assert "Hidden Section" not in latex
    assert "Should not appear" not in latex


def test_render_resume_latex_escapes_bullet_content() -> None:
    latex = render_resume_latex(_sample_document())

    # The raw "50%" and "&" must not appear unescaped in the output.
    assert "50% faster" not in latex
    assert r"50\% faster" in latex
    assert r"C++ \& Go" in latex


def test_render_resume_latex_handles_empty_document() -> None:
    latex = render_resume_latex({"personal": {}, "sections": []})

    assert r"\begin{document}" in latex
    assert r"\end{document}" in latex
    assert "Your Name" in latex
