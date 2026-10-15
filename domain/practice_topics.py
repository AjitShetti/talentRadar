"""
domain/practice_topics.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Which interview topics matter for which kind of role.

A user's target roles ("Senior Backend Engineer", "SDE 2") are mapped onto one
of a few *tracks*, and each track carries a weight per LeetCode topic tag. The
daily pick (``domain/practice_pick.py``) draws from those weights. Pure: no
I/O, no clock.

The topic keys are LeetCode tag slugs (``hash-table``, ``dynamic-programming``).
Every one of them must exist in the question catalogue; the catalogue's own
tests enforce that.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

Track = Literal["backend", "frontend", "data", "ml", "mobile", "devops", "general"]

TRACK_LABELS: dict[Track, str] = {
    "backend": "Backend",
    "frontend": "Frontend",
    "data": "Data",
    "ml": "Machine learning",
    "mobile": "Mobile",
    "devops": "DevOps",
    "general": "General",
}

#: Keywords that put a target role on a track, in the order they are tried. A role is
#: tokenised into words (letters and digits split apart, so "SDE2" is "sde", "2"), and a
#: keyword matches a whole run of words, so ``ml`` is not found inside ``HTML`` and
#: ``front-end`` matches "Front End" as well. The order matters: "Data Scientist" is ml
#: because ml is tried before data; "React Native Developer" and "Flutter UI Engineer" are
#: mobile because mobile is tried before frontend (``react``, ``ui engineer``); and
#: "React Developer" is frontend because ``react`` is tried before ``developer`` (backend).
TRACK_KEYWORDS: tuple[tuple[Track, tuple[str, ...]], ...] = (
    ("ml", ("machine learning", "ml", "ai engineer", "data scientist")),
    ("data", ("data engineer", "analyst", "analytics", "sql")),
    ("mobile", ("android", "ios", "mobile", "flutter", "react native")),
    ("frontend", ("frontend", "front-end", "react", "ui engineer")),
    ("devops", ("devops", "sre", "site reliability", "platform", "cloud", "infrastructure")),
    (
        "backend",
        (
            "backend",
            "back-end",
            "java",
            "python",
            "golang",
            "node",
            "sde",
            "software engineer",
            "full stack",
            "fullstack",
            "developer",
        ),
    ),
)

#: Base weight per LeetCode tag slug for each track. Relative, not a probability.
TRACK_TOPICS: dict[Track, dict[str, float]] = {
    "backend": {
        "array": 3,
        "hash-table": 3,
        "string": 2,
        "two-pointers": 2,
        "sliding-window": 2,
        "binary-search": 2,
        "tree": 2,
        "graph": 2,
        "heap-priority-queue": 2,
        "dynamic-programming": 2,
        "linked-list": 1,
        "stack": 1,
        "database": 1,
        "concurrency": 1,
    },
    "frontend": {
        "string": 3,
        "array": 3,
        "hash-table": 2,
        "stack": 2,
        "tree": 2,
        "depth-first-search": 2,
        "breadth-first-search": 2,
        "design": 2,
        "recursion": 2,
        "two-pointers": 1,
        "sliding-window": 1,
        "linked-list": 1,
        "queue": 1,
        "trie": 1,
        "matrix": 1,
    },
    "data": {
        "database": 4,
        "hash-table": 3,
        "array": 2,
        "string": 2,
        "sorting": 2,
        "two-pointers": 1,
        "math": 1,
        "prefix-sum": 1,
        "counting": 1,
        "binary-search": 1,
        "stack": 1,
        "heap-priority-queue": 1,
        "dynamic-programming": 1,
    },
    "ml": {
        "array": 3,
        "math": 3,
        "dynamic-programming": 2,
        "hash-table": 2,
        "matrix": 2,
        "binary-search": 2,
        "heap-priority-queue": 2,
        "sorting": 2,
        "string": 1,
        "graph": 1,
        "tree": 1,
        "greedy": 1,
        "prefix-sum": 1,
        "probability-and-statistics": 1,
    },
    "mobile": {
        "array": 3,
        "string": 3,
        "hash-table": 2,
        "linked-list": 2,
        "tree": 2,
        "stack": 2,
        "queue": 2,
        "design": 2,
        "two-pointers": 1,
        "sorting": 1,
        "depth-first-search": 1,
        "breadth-first-search": 1,
        "dynamic-programming": 1,
        "matrix": 1,
        "binary-search": 1,
    },
    "devops": {
        "string": 3,
        "hash-table": 3,
        "array": 2,
        "graph": 2,
        "topological-sort": 2,
        "depth-first-search": 2,
        "breadth-first-search": 2,
        "heap-priority-queue": 2,
        "stack": 2,
        "sorting": 2,
        "design": 2,
        "concurrency": 2,
        "queue": 1,
        "simulation": 1,
        "greedy": 1,
        "binary-search": 1,
        "sliding-window": 1,
        "prefix-sum": 1,
    },
    "general": {
        "array": 3,
        "string": 3,
        "hash-table": 3,
        "two-pointers": 2,
        "sliding-window": 2,
        "binary-search": 2,
        "stack": 2,
        "linked-list": 2,
        "tree": 2,
        "graph": 2,
        "dynamic-programming": 2,
        "heap-priority-queue": 1,
        "greedy": 1,
        "sorting": 1,
        "depth-first-search": 1,
        "breadth-first-search": 1,
        "backtracking": 1,
    },
}

_WORD = re.compile(r"[a-z]+|[0-9]+")


def _words(text: str) -> tuple[str, ...]:
    """Lower-case words, letters and digits apart: "Front-end SDE2" -> ("front", "end", "sde", "2")."""
    return tuple(_WORD.findall(text.lower()))


def _contains_run(words: tuple[str, ...], run: tuple[str, ...]) -> bool:
    """True when ``run`` appears in ``words`` as consecutive whole words."""
    size = len(run)
    return size > 0 and any(words[i : i + size] == run for i in range(len(words) - size + 1))


_TRACK_RUNS: tuple[tuple[Track, tuple[tuple[str, ...], ...]], ...] = tuple(
    (track, tuple(_words(keyword) for keyword in keywords)) for track, keywords in TRACK_KEYWORDS
)


def track_for_roles(roles: Sequence[str] | None) -> Track:
    """The practice track for a user's target roles.

    Roles are read in the order the user listed them: the first role that matches any
    keyword decides, and within one role the first track in ``TRACK_KEYWORDS`` wins.
    No roles, or none that match, is ``general``.
    """
    for role in roles or ():
        words = _words(role)
        for track, runs in _TRACK_RUNS:
            if any(_contains_run(words, run) for run in runs):
                return track
    return "general"
