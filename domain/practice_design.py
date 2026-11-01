"""
domain/practice_design.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The system design problem of the day: what one is, and which one a person gets.

Pure rules over problems already loaded and the user's own practice history. No LLM,
no network, no clock: ``day`` is always passed in, and the same ``(user_id, day, swaps)``
always gives the same pick.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

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
