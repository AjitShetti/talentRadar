"""
tests/test_resume_structure_extraction.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Covers the completeness guarantee around extract_resume_structure().

The model that structures an uploaded resume drops content silently: whole
sections when its token budget runs out, a bullet here and there even when it
does not. The result used to be saved as the user's resume as-is. These tests
pin the deterministic check that notices, and the retry that follows.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.llm import extract_resume_structure
from services.resume_coverage import missing_resume_lines

RESUME_TEXT = """\
ADA LOVELACE
ada@example.com | GitHub (https://github.com/ada)
SUMMARY
Payments infrastructure engineer focused on ledger throughput and reliability.
TECHNICAL SKILLS
Languages: Python, Go, SQL, Rust
EXPERIENCE
Stripe - Senior Engineer\tBengaluru | 2022 - Present
- Cut p99 latency 38% by batching ledger writes across regional shards
- Led the migration of settlement jobs from cron to Temporal workflows
EDUCATION
IIT Bombay   B.Tech Computer Science   2018
"""


def _document(*, with_skills: bool = True, bullets: int = 2, skills_last: bool = False) -> dict[str, Any]:
    all_bullets = [
        "Cut p99 latency 38% by batching ledger writes across regional shards",
        "Led the migration of settlement jobs from cron to Temporal workflows",
    ]
    summary = {"type": "summary", "title": "Summary", "items": [
        {"text": "Payments infrastructure engineer focused on ledger throughput and reliability."},
    ]}
    skills = {"type": "skills", "title": "Technical Skills", "items": [
        {"category": "Languages", "items": ["Python", "Go", "SQL", "Rust"]},
    ]}
    experience = {"type": "experience", "title": "Experience", "items": [{
        "title": "Senior Engineer", "company": "Stripe", "location": "Bengaluru",
        "dates": "2022 - Present", "bullets": all_bullets[:bullets],
    }]}
    education = {"type": "education", "title": "Education", "items": [{
        "school": "IIT Bombay", "location": "", "degree": "B.Tech Computer Science",
        "dates": "2018", "bullets": [],
    }]}
    sections = [summary, experience, education]
    if with_skills:
        sections = [summary, experience, education, skills] if skills_last else [summary, skills, experience, education]
    return {
        "personal": {
            "full_name": "Ada Lovelace", "email": "ada@example.com",
            "links": [{"label": "GitHub", "url": "https://github.com/ada"}],
        },
        "sections": sections,
    }


class TestMissingResumeLines:
    def test_a_faithful_document_is_complete(self) -> None:
        assert missing_resume_lines(RESUME_TEXT, _document()) == []

    def test_a_dropped_section_is_reported(self) -> None:
        missing = missing_resume_lines(RESUME_TEXT, _document(with_skills=False))

        assert missing == ["Languages: Python, Go, SQL, Rust"]

    def test_a_dropped_bullet_is_reported(self) -> None:
        missing = missing_resume_lines(RESUME_TEXT, _document(bullets=1))

        assert missing == ["- Led the migration of settlement jobs from cron to Temporal workflows"]

    def test_rewording_within_a_line_is_tolerated(self) -> None:
        document = _document()
        document["sections"][2]["items"][0]["bullets"][0] = (
            "Cut p99 latency 38% by batching ledger writes across shards"
        )

        assert missing_resume_lines(RESUME_TEXT, document) == []


class TestExtractResumeStructure:
    async def test_a_gutted_first_answer_is_retried_with_what_it_dropped(self) -> None:
        chat = AsyncMock(side_effect=[
            json.dumps(_document(with_skills=False)),
            json.dumps(_document()),
        ])
        with patch("services.llm._chat", chat):
            document = await extract_resume_structure(RESUME_TEXT)

        assert [s["type"] for s in document["sections"]] == ["summary", "skills", "experience", "education"]
        assert chat.await_count == 2
        retry_prompt = chat.await_args_list[1].args[1]
        assert "Languages: Python, Go, SQL, Rust" in retry_prompt

    async def test_a_complete_first_answer_is_not_retried(self) -> None:
        chat = AsyncMock(return_value=json.dumps(_document()))
        with patch("services.llm._chat", chat):
            await extract_resume_structure(RESUME_TEXT)

        assert chat.await_count == 1

    async def test_the_fuller_answer_wins_when_neither_is_complete(self) -> None:
        chat = AsyncMock(side_effect=[
            json.dumps(_document(with_skills=False, bullets=1)),
            json.dumps(_document(bullets=1)),
        ])
        with patch("services.llm._chat", chat):
            document = await extract_resume_structure(RESUME_TEXT)

        assert "skills" in [s["type"] for s in document["sections"]]

    async def test_an_unparseable_answer_does_not_discard_a_usable_one(self) -> None:
        chat = AsyncMock(side_effect=[json.dumps(_document(bullets=1)), "{not json"])
        with patch("services.llm._chat", chat):
            document = await extract_resume_structure(RESUME_TEXT)

        assert document["personal"]["full_name"] == "Ada Lovelace"

    async def test_it_raises_when_no_attempt_produced_a_document(self) -> None:
        chat = AsyncMock(side_effect=ValueError("json_validate_failed"))
        with patch("services.llm._chat", chat), pytest.raises(ValueError):
            await extract_resume_structure(RESUME_TEXT)

    async def test_the_reasoning_model_gets_room_to_answer(self) -> None:
        chat = AsyncMock(return_value=json.dumps(_document()))
        with patch("services.llm._chat", chat):
            await extract_resume_structure(RESUME_TEXT)

        kwargs = chat.await_args.kwargs
        assert kwargs["max_tokens"] >= 4000
        assert kwargs["reasoning_effort"] == "low"

    async def test_shouted_headings_and_names_are_set_in_display_case(self) -> None:
        """The template sets both in small capitals, which needs mixed case to show."""
        shouted = _document()
        shouted["personal"]["full_name"] = "ADA LOVELACE"
        shouted["sections"][1]["title"] = "TECHNICAL SKILLS"
        shouted["sections"][2]["title"] = "AI / ML EXPERIENCE AND RESEARCH"
        chat = AsyncMock(return_value=json.dumps(shouted))
        with patch("services.llm._chat", chat):
            document = await extract_resume_structure(RESUME_TEXT)

        assert document["personal"]["full_name"] == "Ada Lovelace"
        titles = [s["title"] for s in document["sections"]]
        assert "Technical Skills" in titles
        assert "AI / ML Experience and Research" in titles
        assert "Summary" in titles  # already mixed case: left alone

    async def test_sections_follow_the_order_of_the_source(self) -> None:
        chat = AsyncMock(return_value=json.dumps(_document(skills_last=True)))
        with patch("services.llm._chat", chat):
            document = await extract_resume_structure(RESUME_TEXT)

        assert [s["type"] for s in document["sections"]] == ["summary", "skills", "experience", "education"]
        assert [s["order"] for s in document["sections"]] == [0, 1, 2, 3]
