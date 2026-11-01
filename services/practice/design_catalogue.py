"""
services/practice/design_catalogue.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The system design problems: the checked-in files in ``data/practice/system_design``.

:func:`load_problems` reads them once per process. A missing folder loads as ``()``, and
an unreadable or invalid file is logged and skipped, so one bad file never breaks today's
page. :func:`parse_problem` is what decides a file is valid; it raises ``ValueError``
naming the bad field.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import cast, get_args

from domain.practice_design import DESIGN_TOPICS, DesignProblem
from domain.practice_topics import Track

logger = logging.getLogger(__name__)

DESIGN_DIR = Path(__file__).resolve().parents[2] / "data" / "practice" / "system_design"

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_KEYS = frozenset(DesignProblem.__dataclass_fields__)
MINUTES_MIN = 15
MINUTES_MAX = 60


def _text(raw: dict[str, object], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value


def _texts(raw: dict[str, object], field: str) -> tuple[str, ...]:
    value = raw[field]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a list with at least one entry")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} must hold only non-empty text")
    return tuple(cast("list[str]", value))


def parse_problem(raw: object) -> DesignProblem:
    """A problem from a file's JSON, or ``ValueError`` naming what is wrong with it."""
    if not isinstance(raw, dict):
        raise ValueError("a problem must be a JSON object")
    document = cast("dict[str, object]", raw)
    unknown = sorted(set(document) - _KEYS)
    if unknown:
        raise ValueError(f"unknown key(s): {', '.join(unknown)}")
    missing = sorted(_KEYS - set(document))
    if missing:
        raise ValueError(f"missing key(s): {', '.join(missing)}")

    slug = _text(document, "slug")
    if not _SLUG.fullmatch(slug):
        raise ValueError("slug must be lowercase words joined by hyphens")
    tracks = _texts(document, "tracks")
    if not set(tracks) <= set(get_args(Track)):
        raise ValueError(f"tracks must be among {', '.join(get_args(Track))}")
    topics = _texts(document, "topics")
    if not set(topics) <= set(DESIGN_TOPICS):
        raise ValueError(f"topics must be among {', '.join(DESIGN_TOPICS)}")
    minutes = document["minutes"]
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("minutes must be a whole number")
    if not MINUTES_MIN <= minutes <= MINUTES_MAX:
        raise ValueError(f"minutes must be {MINUTES_MIN} to {MINUTES_MAX}")

    return DesignProblem(
        slug=slug,
        title=_text(document, "title"),
        tracks=cast("tuple[Track, ...]", tracks),
        minutes=minutes,
        topics=topics,
        summary=_text(document, "summary"),
        context=_texts(document, "context"),
        must_handle=_texts(document, "must_handle"),
        show_in_answer=_texts(document, "show_in_answer"),
    )


@lru_cache(maxsize=4)
def load_problems(path: Path = DESIGN_DIR) -> tuple[DesignProblem, ...]:
    """Every valid problem in ``path``, in file-name order. Cached per path."""
    if not path.is_dir():
        logger.warning("practice design problems: %s is missing", path)
        return ()
    problems: dict[str, DesignProblem] = {}
    for file in sorted(path.glob("*.json"), key=lambda candidate: candidate.name):
        try:
            problem = parse_problem(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning("practice design problem %s skipped: %s", file.name, exc)
            continue
        if problem.slug in problems:
            logger.warning("practice design problem %s skipped: repeated slug", file.name)
            continue
        problems[problem.slug] = problem
    return tuple(problems.values())
