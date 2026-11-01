"""
domain/practice_design.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The system design problem of the day: what one is, and which one a person gets.

Pure rules over problems already loaded and the user's own practice history. No LLM,
no network, no clock: ``day`` is always passed in, and the same ``(user_id, day, swaps)``
always gives the same pick.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from domain.practice_judge import flat_line
from domain.practice_pick import (
    NEGLECT_AFTER_DAYS,
    NEGLECT_FACTOR,
    RECENT_DAYS,
    RECENT_FACTOR,
    HistoryItem,
    day_rng,
    topic_label,
)
from domain.practice_topics import Track

DESIGN_TOPICS: tuple[str, ...] = (
    "caching",
    "queues",
    "data-modelling",
    "realtime",
    "search",
    "payments",
    "rate-limiting",
    "storage",
    "feeds",
    "scheduling",
    "consistency",
    "observability",
    "ml-serving",
    "pipelines",
    "offline-sync",
    "api-design",
)

#: A stored drawing's limits, and what a request for feedback must have to read.
MAX_SCENE_BYTES = 200_000
MAX_ELEMENTS = 500
MAX_REASONING_CHARS = 4_000
MIN_LABELLED_SHAPES = 3
MIN_REASONING_CHARS = 40
#: Elements that carry a file or a page. A drawing here is shapes, arrows and text.
REFUSED_ELEMENT_TYPES = frozenset({"image", "embeddable", "iframe"})

_SHAPE_TYPES = frozenset({"rectangle", "ellipse", "diamond"})
_UNREAD_TYPES = frozenset({"line", "freedraw"})
_LABEL_CHARS = 80
_UNLABELLED = "(unlabelled)"


@dataclass(frozen=True)
class DesignProblem:
    """One system design problem, written by us. It holds a scenario and never a solution."""

    slug: str
    title: str
    tracks: tuple[Track, ...]
    minutes: int
    topics: tuple[str, ...]
    summary: str
    context: tuple[str, ...]
    must_handle: tuple[str, ...]
    show_in_answer: tuple[str, ...]


@dataclass(frozen=True)
class SceneReading:
    """A drawing as the model reads it, and how much of it could not be read."""

    text: str
    labelled_shapes: int
    unread: int


@dataclass(frozen=True)
class DesignPick:
    """A suggestion. ``repeat`` says it was done before, every other one being done too."""

    problem: DesignProblem
    topic: str
    reason: str
    repeat: bool


def _last_days(
    history: Sequence[HistoryItem], key: Callable[[HistoryItem], str]
) -> dict[str, date]:
    """The newest done day per topic or per slug."""
    latest: dict[str, date] = {}
    for item in history:
        if item.done:
            latest[key(item)] = max(item.day, latest.get(key(item), item.day))
    return latest


def _topic_weight(topic: str, last: dict[str, date], day: date) -> float:
    seen = last.get(topic)
    if seen is None or (day - seen).days > NEGLECT_AFTER_DAYS:
        return NEGLECT_FACTOR
    if (day - seen).days <= RECENT_DAYS:
        return RECENT_FACTOR
    return 1.0


def _reason(topic: str, last: dict[str, date], day: date) -> str:
    label = topic_label(topic)
    seen = last.get(topic)
    if seen is None:
        return f"{label} — you have not practised it yet."
    gap = (day - seen).days
    when = "yesterday" if gap == 1 else f"{gap} days ago"
    return f"{label} — you last practised it {when}."


def _repeat_reason(done_on: date, day: date) -> str:
    weeks = max(1, (day - done_on).days // 7)
    return f"You did this {weeks} week{'' if weeks == 1 else 's'} ago. See what you would change."


def _draw_fresh(
    rng: random.Random, pool: Sequence[DesignProblem], last: dict[str, date], day: date
) -> DesignPick:
    topics = sorted({topic for problem in pool for topic in problem.topics})
    weights = [_topic_weight(topic, last, day) for topic in topics]
    topic = rng.choices(topics, weights=weights)[0]
    problem = rng.choice([item for item in pool if topic in item.topics])
    return DesignPick(problem=problem, topic=topic, reason=_reason(topic, last, day), repeat=False)


def pick_design(
    *,
    user_id: str,
    day: date,
    swaps: int,
    track: Track,
    history: Sequence[HistoryItem],
    problems: Sequence[DesignProblem],
) -> DesignPick | None:
    """The problem to suggest on ``day``, or ``None`` when there are no problems.

    Candidates are the problems of ``track`` and of ``general``, or every problem when none
    fits. Draw a topic by the same neglect rule the DSA pick uses, then a problem on it that
    is not done. When every candidate is done, the one done longest ago comes back and says
    so. Each swap is a fresh draw that never repeats an earlier one of the same day unless
    nothing else is left.

    Only history strictly before ``day`` counts, so marking today's problem done cannot
    change today's pick.
    """
    if swaps < 0:
        raise ValueError("swaps must not be negative")
    ordered = sorted(problems, key=lambda item: item.slug)
    fitting = [item for item in ordered if track in item.tracks or "general" in item.tracks]
    candidates = fitting or ordered
    if not candidates:
        return None

    earlier = [item for item in history if item.day < day]
    last_topic = _last_days(earlier, lambda item: item.topic)
    done_on = _last_days(earlier, lambda item: item.slug)
    # Done longest ago first, with the slug settling a tie, so a repeat never depends on input order.
    repeats = sorted(
        (item for item in candidates if item.slug in done_on),
        key=lambda item: (done_on[item.slug], item.slug),
    )
    fresh = [item for item in candidates if item.slug not in done_on]

    taken: set[str] = set()
    chosen: DesignPick | None = None
    for swap in range(swaps + 1):
        if len(taken) >= len(candidates):
            break
        pool = [item for item in fresh if item.slug not in taken]
        if pool:
            chosen = _draw_fresh(day_rng(user_id, day, swap), pool, last_topic, day)
        else:
            problem = next(item for item in repeats if item.slug not in taken)
            chosen = DesignPick(
                problem=problem,
                topic=problem.topics[0],
                reason=_repeat_reason(done_on[problem.slug], day),
                repeat=True,
            )
        taken.add(chosen.problem.slug)
    return chosen


# --------------------------------------------------------------------------- reading a drawing


def scene_refusal(elements: object) -> str | None:
    """Why a drawing cannot be stored, as a sentence for the person, or ``None``."""
    if not isinstance(elements, list):
        return "That drawing could not be read. Reload the page and try again."
    if len(elements) > MAX_ELEMENTS:
        return f"A drawing can hold at most {MAX_ELEMENTS} elements. Remove some and save again."
    for element in elements:
        if isinstance(element, Mapping) and _kind(element) in REFUSED_ELEMENT_TYPES:
            return "A drawing here is shapes, arrows and text. Remove the image or embed."
    try:
        size = len(json.dumps(elements, ensure_ascii=False).encode())
    except (TypeError, ValueError, RecursionError):
        return "That drawing could not be read. Reload the page and try again."
    if size > MAX_SCENE_BYTES:
        return "This drawing is too large to save. Remove some of it and save again."
    return None


def _bound_id(binding: object) -> str | None:
    element_id = binding.get("elementId") if isinstance(binding, Mapping) else None
    return element_id if isinstance(element_id, str) else None


def _block(title: str, lines: Sequence[str]) -> str:
    return "\n".join([title, *(f"- {line}" for line in sorted(lines))])


def _kind(element: Mapping[Any, Any]) -> str | None:
    """An element's type, when it has one that could be a type: this came from the browser."""
    kind = element.get("type")
    return kind if isinstance(kind, str) else None


def describe_scene(elements: Sequence[object]) -> SceneReading:
    """A drawing as text, for the model: its shapes, its arrows and its notes.

    Pure, and sorted, so the order of the elements cannot change the text or its hash. Junk is
    skipped: the elements come from the browser. An arrow not bound to a shape at both ends, a
    line and a freehand stroke are counted in ``unread``; the model never sees them.
    """
    live = [
        element
        for element in elements
        if isinstance(element, Mapping) and element.get("isDeleted") is not True
    ]
    shape_ids = {
        element["id"]
        for element in live
        if _kind(element) in _SHAPE_TYPES and isinstance(element.get("id"), str)
    }
    arrows = [element for element in live if _kind(element) == "arrow"]
    containers = shape_ids | {arrow["id"] for arrow in arrows if isinstance(arrow.get("id"), str)}

    labels: dict[str, str] = {}
    notes: list[str] = []
    for element in live:
        if _kind(element) != "text" or not isinstance(element.get("text"), str):
            continue
        text = flat_line(element["text"], _LABEL_CHARS)
        container = element.get("containerId")
        if not text:
            continue
        if isinstance(container, str) and container in containers:
            labels[container] = min(text, labels.get(container, text))
        else:
            notes.append(text)

    arrow_lines: list[str] = []
    unread = sum(1 for element in live if _kind(element) in _UNREAD_TYPES)
    for arrow in arrows:
        start = _bound_id(arrow.get("startBinding"))
        end = _bound_id(arrow.get("endBinding"))
        if start is None or end is None or start not in shape_ids or end not in shape_ids:
            unread += 1
            continue
        arrow_id = arrow.get("id")
        label = labels.get(arrow_id) if isinstance(arrow_id, str) else None
        link = f"-[{label}]->" if label else "->"
        from_shape = labels.get(start, _UNLABELLED)
        to_shape = labels.get(end, _UNLABELLED)
        arrow_lines.append(f"{from_shape} {link} {to_shape}")

    shape_lines = [labels.get(shape_id, _UNLABELLED) for shape_id in shape_ids]
    reading = "\n".join(
        [_block("Shapes:", shape_lines), _block("Arrows:", arrow_lines), _block("Notes:", notes)]
    )
    labelled = sum(1 for shape_id in shape_ids if shape_id in labels)
    return SceneReading(text=reading, labelled_shapes=labelled, unread=unread)
