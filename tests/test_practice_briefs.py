"""
tests/test_practice_briefs.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The twelve take-home briefs under ``data/practice/challenges/`` (spec 4.7, Task 14).

These tests read the real files, not fixtures. They check what a reviewer would otherwise check
by eye: the set matches the spec's table, each brief is complete and fits what the judge reads,
nothing in a brief needs a paid service, and no brief names a real company.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from itertools import pairwise
from typing import Any

import pytest

from domain.practice_judge import CAP_BRIEF, JudgeInput, build_judge_prompt
from services.practice.challenges import BRIEF_MAX_BYTES, BRIEFS_DIR, TRACKS, load_briefs

#: Spec 4.7's table, in order: (slug, track, hours).
SPEC_TABLE: tuple[tuple[str, str, int], ...] = (
    ("idempotent-webhook-relay", "backend", 48),
    ("quick-commerce-order-tracker", "frontend", 24),
    ("issue-triage-agent", "ai", 48),
    ("restaurant-listing-merge", "data", 48),
    ("society-maintenance-dues", "fullstack", 48),
    ("rate-limiter-and-load-test", "backend", 24),
    ("ten-thousand-row-grid", "frontend", 48),
    ("resume-jd-matcher", "ai", 24),
    ("metro-ridership-dashboard", "data", 24),
    ("interview-slot-booking", "fullstack", 24),
    ("postgres-job-queue", "backend", 48),
    ("docs-rag-with-citations", "ai", 48),
)

#: A service a candidate would have to pay for, or give a card to, by name. Extend as needed.
PAID_SERVICES = (
    "openai",
    "anthropic",
    "claude api",
    "gemini api",
    "cohere api",
    "twilio",
    "stripe",
    "sendgrid",
    "mailgun",
    "algolia",
    "pinecone",
    "datadog",
    "mapbox",
    "google maps",
    "aws",
    "azure",
    "heroku",
    "vercel",
    "firebase",
)

#: Real companies a brief could be tempted to name. Briefs and sample data use fictional ones
#: only. Matched as whole words, case-insensitively.
REAL_COMPANIES = (
    "razorpay",
    "cashfree",
    "juspay",
    "phonepe",
    "paytm",
    "zepto",
    "blinkit",
    "swiggy",
    "zomato",
    "flipkart",
    "amazon",
    "google",
    "microsoft",
    "infosys",
    "wipro",
    "bigbasket",
    "dunzo",
    "zerodha",
    "uber",
    "ola",
    "linkedin",
    "naukri",
    "leetcode",
    "hackerrank",
    "codeforces",
)

LIST_KEYS = ("context", "must_have", "nice_to_have", "constraints", "deliverables", "judge_focus")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _all_text(brief: dict[str, Any]) -> str:
    return "\n".join(text for value in brief.values() for text in _strings(value))


BRIEFS = load_briefs()
FILES = sorted(BRIEFS_DIR.glob("*.json"), key=lambda path: path.name)


def _ids() -> list[str]:
    return [str(brief["slug"]) for brief in BRIEFS]


# =========================================================================== the set


class TestTheSet:
    def test_there_are_twelve_briefs(self) -> None:
        assert len(BRIEFS) == 12
        assert len(FILES) == 12

    def test_slugs_are_unique(self) -> None:
        assert len(set(_ids())) == 12

    def test_files_are_numbered_01_to_12_in_order_and_named_for_their_slug(self) -> None:
        expected = [
            f"{number:02d}-{slug}.json" for number, (slug, _t, _h) in enumerate(SPEC_TABLE, 1)
        ]

        assert [path.name for path in FILES] == expected
        assert [brief["slug"] for brief in BRIEFS] == [slug for slug, _t, _h in SPEC_TABLE]

    def test_each_brief_has_the_track_and_hours_of_the_spec_table(self) -> None:
        assert [(b["slug"], b["track"], b["hours"]) for b in BRIEFS] == list(SPEC_TABLE)

    def test_all_five_tracks_are_covered(self) -> None:
        assert {brief["track"] for brief in BRIEFS} == set(TRACKS)

    def test_no_two_consecutive_briefs_share_a_track(self) -> None:
        tracks = [brief["track"] for brief in BRIEFS]

        assert all(a != b for a, b in pairwise(tracks)), tracks

    def test_every_title_is_distinct(self) -> None:
        assert len({brief["title"] for brief in BRIEFS}) == 12


# =========================================================================== each brief


@pytest.mark.parametrize("brief", BRIEFS, ids=_ids())
class TestEachBrief:
    def test_it_is_a_24_or_48_hour_build(self, brief: dict[str, Any]) -> None:
        assert brief["hours"] in (24, 48)

    def test_it_has_enough_must_haves_but_not_a_syllabus(self, brief: dict[str, Any]) -> None:
        assert 3 <= len(brief["must_have"]) <= 6

    def test_it_says_no_paid_service_is_needed(self, brief: dict[str, Any]) -> None:
        assert any("No paid" in item for item in brief["constraints"])

    def test_it_says_its_data_is_fictional(self, brief: dict[str, Any]) -> None:
        assert any("fictional" in item.lower() for item in brief["constraints"])

    def test_a_deliverable_is_a_readme_that_says_how_to_run_it(self, brief: dict[str, Any]) -> None:
        readmes = [item for item in brief["deliverables"] if "README" in item]

        assert readmes
        assert any("how to run" in item.lower() for item in readmes)

    def test_a_deliverable_is_a_repo_made_after_start_with_commits_over_the_window(
        self, brief: dict[str, Any]
    ) -> None:
        repos = [item for item in brief["deliverables"] if "repo" in item.lower()]

        assert any("after you press Start" in item for item in repos)
        assert any("commits spread across the window" in item for item in repos)

    def test_it_has_at_least_two_judge_focus_items_and_one_is_about_the_trail(
        self, brief: dict[str, Any]
    ) -> None:
        assert len(brief["judge_focus"]) >= 2
        assert any("commit" in item.lower() for item in brief["judge_focus"])

    def test_it_names_the_bengaluru_team_that_does_this_work(self, brief: dict[str, Any]) -> None:
        context = brief["bengaluru_context"]

        assert context.strip()
        assert "Bengaluru" in context
        assert "teams" in context.lower()

    def test_it_has_context_paragraphs_and_a_summary(self, brief: dict[str, Any]) -> None:
        assert len(brief["context"]) >= 2
        assert brief["summary"].strip()

    def test_no_list_entry_is_empty_or_repeated(self, brief: dict[str, Any]) -> None:
        for key in LIST_KEYS:
            items = brief[key]
            assert all(item.strip() for item in items), key
            assert len(set(items)) == len(items), key


# =========================================================================== what the judge reads


@pytest.mark.parametrize("brief", BRIEFS, ids=_ids())
class TestFitsTheJudge:
    def test_the_text_of_the_brief_is_within_the_judges_brief_cap(
        self, brief: dict[str, Any]
    ) -> None:
        assert sum(len(text) for value in brief.values() for text in _strings(value)) <= CAP_BRIEF

    def test_the_file_is_far_under_the_stored_brief_limit(self, brief: dict[str, Any]) -> None:
        size = len(json.dumps(brief, ensure_ascii=False).encode("utf-8"))

        assert size <= BRIEF_MAX_BYTES // 2

    def test_no_entry_is_long_enough_to_be_cut_to_a_line(self, brief: dict[str, Any]) -> None:
        longest = max(len(text) for key in LIST_KEYS for text in brief[key])

        assert longest <= 400

    def test_the_judge_prompt_carries_every_entry_whole(self, brief: dict[str, Any]) -> None:
        # The judge keeps the lists most decisive first and cuts from the end when the brief is
        # over its cap, so a brief that fits shows every entry in the prompt, unchanged.
        _system, user = build_judge_prompt(
            JudgeInput(
                brief=brief, writeup={}, readme="", tree=[], commits=[], files=[], signals={}
            )
        )

        missing = [text for key in LIST_KEYS for text in brief[key] if text not in user]
        assert missing == []


# =========================================================================== content rules


@pytest.mark.parametrize("brief", BRIEFS, ids=_ids())
class TestContentRules:
    def test_it_names_no_paid_service(self, brief: dict[str, Any]) -> None:
        text = _all_text(brief).lower()

        found = [name for name in PAID_SERVICES if re.search(rf"\b{re.escape(name)}\b", text)]
        assert found == []

    def test_it_names_no_real_company(self, brief: dict[str, Any]) -> None:
        text = _all_text(brief).lower()

        found = [name for name in REAL_COMPANIES if re.search(rf"\b{re.escape(name)}\b", text)]
        assert found == []

    def test_it_has_no_link_and_no_markup_the_judge_would_neutralise(
        self, brief: dict[str, Any]
    ) -> None:
        text = _all_text(brief)

        assert "://" not in text
        assert "<<<" not in text
        assert ">>>" not in text

    def test_it_is_plain_text_with_no_invisible_or_control_characters(
        self, brief: dict[str, Any]
    ) -> None:
        odd = {
            f"U+{ord(char):04X}"
            for char in _all_text(brief)
            if unicodedata.category(char)[0] == "C" and char != "\n"
        }

        assert odd == set()

    def test_a_deliverable_never_asks_for_a_secret(self, brief: dict[str, Any]) -> None:
        text = "\n".join(brief["deliverables"]).lower()

        assert "api key" not in text
        assert "password" not in text
