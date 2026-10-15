"""
services/practice/catalogue.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The question catalogue: every free LeetCode problem, as a pointer.

``data/practice/leetcode_free.json`` is built by a person, not fetched by the server:
robots.txt disallows leetcode.com's ``/graphql``, so the builder saves the responses the
problem list makes in their own browser and ``scripts/build_practice_catalogue.py`` turns
them into the file. :func:`parse_saved_responses` is that conversion.

A row keeps five fields (id, slug, title, difficulty, topic slugs). Statements, examples,
hints, acceptance rates and paid-only questions are never kept: the :class:`Question`
type has nowhere to put them.

:func:`load_catalogue` reads the file once per process. A missing or malformed file loads
as ``()``, logged once; the daily practice page then says the list is not loaded yet and
nothing else breaks.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from domain.practice_pick import DIFFICULTIES, Difficulty, Question

logger = logging.getLogger(__name__)

CATALOGUE_PATH = Path(__file__).resolve().parents[2] / "data" / "practice" / "leetcode_free.json"

#: A catalogue row is ``[id, slug, title, difficulty, [topic slugs]]``.
ROW_FIELDS = 5


# ---------------------------------------------------------------------------
# saved leetcode.com responses -> questions
# ---------------------------------------------------------------------------


def parse_saved_responses(payloads: Sequence[Any]) -> list[Question]:
    """The free questions found in saved leetcode.com responses, sorted by id.

    The responses come in several shapes (the problem list, its V2 form, the daily
    challenge), so this does not follow any one of them: it walks each payload and takes
    every object that has a string ``titleSlug``. Such an object is not searched any
    further, so a question's related questions are never collected as if they were
    listed. A row is dropped when it is paid-only (``isPaidOnly`` or ``paidOnly``) or
    lacks a slug, a title, a numeric ``questionFrontendId`` or a known difficulty. The
    same slug in several payloads counts once.
    """
    found: dict[str, Question] = {}
    for payload in payloads:
        for item in _question_nodes(payload):
            question = _question_from_node(item)
            if question is not None:
                found.setdefault(question.slug, question)
    return sorted(found.values(), key=lambda question: (question.id, question.slug))


def _question_nodes(value: object) -> Iterator[dict[str, Any]]:
    """Every dict under ``value`` that has a string ``titleSlug``, outermost only."""
    if isinstance(value, dict):
        if isinstance(value.get("titleSlug"), str):
            yield value
            return
        for child in value.values():
            yield from _question_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _question_nodes(child)


def _question_from_node(item: dict[str, Any]) -> Question | None:
    if item.get("isPaidOnly") or item.get("paidOnly"):
        return None
    slug = str(item["titleSlug"]).strip()
    title = item.get("title")
    difficulty = _difficulty(item.get("difficulty"))
    question_id = _frontend_id(item.get("questionFrontendId"))
    if not slug or not isinstance(title, str) or not title.strip():
        return None
    if difficulty is None or question_id is None:
        return None
    return Question(
        id=question_id,
        slug=slug,
        title=title.strip(),
        difficulty=difficulty,
        topics=_topic_slugs(item.get("topicTags")),
    )


def _difficulty(value: object) -> Difficulty | None:
    text = value.strip().lower() if isinstance(value, str) else ""
    for known in DIFFICULTIES:
        if known == text:
            return known
    return None


def _frontend_id(value: object) -> int | None:
    """The number shown beside the title; ``True`` and labels such as "LCP 01" are not ids."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _topic_slugs(tags: object) -> tuple[str, ...]:
    """The ``slug`` of each tag, in order, without repeats."""
    if not isinstance(tags, list):
        return ()
    slugs: dict[str, None] = {}
    for tag in tags:
        slug = tag.get("slug") if isinstance(tag, dict) else None
        if isinstance(slug, str) and slug.strip():
            slugs.setdefault(slug.strip(), None)
    return tuple(slugs)


# ---------------------------------------------------------------------------
# the checked-in file -> questions
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def load_catalogue(path: Path = CATALOGUE_PATH) -> tuple[Question, ...]:
    """The catalogue in file order; ``()`` when the file is missing or malformed.

    Cached per path, so a problem is logged once. A row that fails validation discards
    the whole file: the builder writes every row itself, so a bad one means the file was
    edited or cut short, and half a catalogue would skew the pick without saying so.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("practice catalogue %s is missing; daily practice has no questions", path)
        return ()
    except (OSError, ValueError) as exc:
        logger.warning("practice catalogue %s cannot be read: %s", path, exc)
        return ()
    try:
        return _questions_from_document(document)
    except ValueError as exc:
        logger.warning("practice catalogue %s is malformed: %s", path, exc)
        return ()


def catalogue_by_slug() -> dict[str, Question]:
    """The catalogue indexed by problem slug; empty without a catalogue."""
    return {question.slug: question for question in load_catalogue()}


def _questions_from_document(document: object) -> tuple[Question, ...]:
    rows = document.get("questions") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("expected an object with a 'questions' list")
    return tuple(_question_from_row(index, row) for index, row in enumerate(rows))


def _question_from_row(index: int, row: object) -> Question:
    if not isinstance(row, list) or len(row) != ROW_FIELDS:
        raise ValueError(f"row {index} is not a list of {ROW_FIELDS} fields")
    question_id, slug, title, difficulty, topics = row
    known = _difficulty(difficulty)
    if (
        not isinstance(question_id, int)
        or isinstance(question_id, bool)
        or not isinstance(slug, str)
        or not slug
        or not isinstance(title, str)
        or known is None
        or not isinstance(topics, list)
        or not all(isinstance(topic, str) for topic in topics)
    ):
        raise ValueError(f"row {index} has a field of the wrong kind")
    return Question(id=question_id, slug=slug, title=title, difficulty=known, topics=tuple(topics))
