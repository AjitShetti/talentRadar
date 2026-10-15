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

:func:`load_catalogue` reads the file once per process. A missing, unreadable or
structurally wrong file loads as ``()``, logged once; the daily practice page then says
the list is not loaded yet and nothing else breaks. A single bad row is skipped and
counted in the log, never allowed to hide the rest.
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
#: The names under which saved responses say a question is paid-only.
PAID_FLAGS = ("isPaidOnly", "paidOnly")


# ---------------------------------------------------------------------------
# saved leetcode.com responses -> questions
# ---------------------------------------------------------------------------


def parse_saved_responses(payloads: Sequence[Any]) -> list[Question]:
    """The free questions found in saved leetcode.com responses, sorted by id.

    The responses come in several shapes (the problem list, its V2 form, the daily
    challenge), so this does not follow any one of them: it walks each payload and takes
    every object that has a string ``titleSlug``. Such an object is not searched any
    further, so a question's related questions are never collected as if they were
    listed. A row is kept only when it says outright that it is free (``isPaidOnly`` or
    ``paidOnly`` is present and ``False``; see :func:`_is_known_free`) and has a slug, a
    title, a numeric ``questionFrontendId`` and a known difficulty. The same slug in
    several payloads counts once.
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


def _is_known_free(item: dict[str, Any]) -> bool:
    """True only when the node itself says it is not paid-only.

    Fails closed: the flag (``isPaidOnly`` in the problem list, ``paidOnly`` in its V2
    form) must be present and be the boolean ``False``. A node with no flag, a null, or
    any other value might be a paid question, so it is not listed. If a node carries both
    names, both must say ``False``.
    """
    flags = [item[name] for name in PAID_FLAGS if name in item]
    return bool(flags) and all(flag is False for flag in flags)


def _question_from_node(item: dict[str, Any]) -> Question | None:
    if not _is_known_free(item):
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
    """The catalogue in file order.

    ``()`` when the file is missing, unreadable, not JSON, or has no ``questions`` list.
    A row of the wrong shape is skipped on its own, so one typo never hides the other
    ~3,000 questions; the skipped count is logged. Cached per path, so a problem is
    logged once.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("practice catalogue %s is missing; daily practice has no questions", path)
        return ()
    except (OSError, ValueError) as exc:
        logger.warning("practice catalogue %s cannot be read: %s", path, exc)
        return ()
    rows = document.get("questions") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        logger.warning("practice catalogue %s is malformed: no 'questions' list", path)
        return ()
    parsed = (_question_from_row(row) for row in rows)
    questions = tuple(question for question in parsed if question is not None)
    if len(questions) < len(rows):
        logger.warning(
            "practice catalogue %s: skipped %d malformed row(s) of %d",
            path,
            len(rows) - len(questions),
            len(rows),
        )
    return questions


def catalogue_by_slug() -> dict[str, Question]:
    """The catalogue indexed by problem slug; empty without a catalogue."""
    return {question.slug: question for question in load_catalogue()}


def _question_from_row(row: object) -> Question | None:
    """One catalogue row as a question, or ``None`` when any of its fields is the wrong kind."""
    if not isinstance(row, list) or len(row) != ROW_FIELDS:
        return None
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
        return None
    return Question(id=question_id, slug=slug, title=title, difficulty=known, topics=tuple(topics))
