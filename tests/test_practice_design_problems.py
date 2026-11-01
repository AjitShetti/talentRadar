"""
tests/test_practice_design_problems.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The checked-in system design problems. Strict where the loader is forgiving: the loader skips a bad
file and logs it, so that a person still gets a problem; here a bad file fails the build.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import get_args

import pytest

from domain.practice_design import DESIGN_TOPICS, DesignProblem
from domain.practice_topics import Track
from services.practice.design_catalogue import DESIGN_DIR, load_problems, parse_problem

FILES = sorted(Path(DESIGN_DIR).glob("*.json"))
REAL_COMPANIES = (
    "Flipkart",
    "Swiggy",
    "Zomato",
    "Uber",
    "Google",
    "Amazon",
    "Netflix",
    "Razorpay",
)


def load_problems_strict() -> tuple[DesignProblem, ...]:
    """Every file, parsed. Any failure fails the test that uses it."""
    return tuple(parse_problem(json.loads(file.read_text("utf-8"))) for file in FILES)


PROBLEMS = load_problems_strict()


def words_of(problem: DesignProblem) -> str:
    return " ".join(
        (
            problem.title,
            problem.summary,
            *problem.context,
            *problem.must_handle,
            *problem.show_in_answer,
        )
    )


def test_there_are_about_forty() -> None:
    assert 38 <= len(PROBLEMS) <= 45


def test_slugs_are_unique_and_match_their_files() -> None:
    assert len({problem.slug for problem in PROBLEMS}) == len(PROBLEMS)
    for file, problem in zip(FILES, PROBLEMS, strict=True):
        assert re.fullmatch(r"\d\d-" + re.escape(problem.slug) + r"\.json", file.name)


def test_the_loader_reads_every_file() -> None:
    load_problems.cache_clear()
    assert len(load_problems()) == len(PROBLEMS)


@pytest.mark.parametrize("track", get_args(Track))
def test_every_track_has_at_least_twelve(track: str) -> None:
    fits = [p for p in PROBLEMS if track in p.tracks or "general" in p.tracks]
    assert len(fits) >= 12


def test_every_topic_has_at_least_two() -> None:
    counts = Counter(topic for problem in PROBLEMS for topic in problem.topics)
    assert {topic: counts[topic] for topic in DESIGN_TOPICS if counts[topic] < 2} == {}


@pytest.mark.parametrize("problem", PROBLEMS, ids=lambda problem: problem.slug)
def test_each_problem_has_the_agreed_shape(problem: DesignProblem) -> None:
    assert 20 <= problem.minutes <= 45
    assert 1 <= len(problem.topics) <= 3
    assert 2 <= len(problem.context) <= 3
    assert 4 <= len(problem.must_handle) <= 6
    assert 3 <= len(problem.show_in_answer) <= 5


def test_no_problem_names_a_real_company_on_the_list() -> None:
    for problem in PROBLEMS:
        for name in REAL_COMPANIES:
            assert not re.search(rf"\b{name}\b", words_of(problem), re.I), (problem.slug, name)
