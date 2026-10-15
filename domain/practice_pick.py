"""
domain/practice_pick.py
~~~~~~~~~~~~~~~~~~~~~~~
The daily practice question: which LeetCode problem to suggest today.

Pure rules over metadata we already hold (a slug, a title, a difficulty and
topic tags per problem, and the user's own practice history). No LLM, no
network, no clock: ``day`` and ``today`` are always passed in, and the same
``(user_id, day, swaps)`` always gives the same pick.

We never author problems. A :class:`Question` is a pointer to the problem page on
leetcode.com; the user solves it there.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Literal

from domain.practice_topics import TRACK_LABELS, TRACK_TOPICS, Track

Difficulty = Literal["easy", "medium", "hard"]
Feel = Literal["easy", "right", "hard"]

DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")

#: Easy / medium / hard percentages by experience, easiest band first.
_BANDS: tuple[tuple[int, int, int], ...] = ((60, 40, 0), (30, 60, 10), (10, 70, 20), (0, 65, 35))
#: Exclusive upper bound, in years, of each band but the last: <1, 1-3, 3-6, 6 and up.
_BAND_LIMITS: tuple[float, ...] = (1.0, 3.0, 6.0)
_UNKNOWN_YEARS_BAND = 1
#: How many of the latest ratings tune the band, and how many of them must agree.
FEEL_WINDOW = 5
FEEL_QUORUM = 3

#: A topic is neglected once its last practice is more than this many days old (x2).
NEGLECT_AFTER_DAYS = 21
NEGLECT_FACTOR = 2.0
#: A topic last found hard is revisited sooner (x1.5).
HARD_FACTOR = 1.5
#: A topic practised within this many days is rested, so no topic comes up three days in a row.
RECENT_DAYS = 2
RECENT_FACTOR = 0.3
#: A question done within this many days is not suggested again unless nothing else is left.
REPEAT_AFTER_DAYS = 180


@dataclass(frozen=True)
class Question:
    """One free LeetCode problem: a pointer, never its statement."""

    id: int
    slug: str
    title: str
    difficulty: Difficulty
    topics: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"https://leetcode.com/problems/{self.slug}/"


@dataclass(frozen=True)
class HistoryItem:
    """One day's pick as the user left it. Only ``done`` days count as practice."""

    day: date
    slug: str
    topic: str
    difficulty: Difficulty
    done: bool
    feel: Feel | None


@dataclass(frozen=True)
class Pick:
    """A suggestion. ``relaxed`` names the rules that had to give way to find one:
    any of ``"difficulty"``, ``"topic"``, ``"history"``, in that order."""

    question: Question
    topic: str
    difficulty: Difficulty
    reason: str
    relaxed: tuple[str, ...]


def topic_label(slug: str) -> str:
    """A tag slug as words: ``heap-priority-queue`` -> ``Heap Priority Queue``."""
    return slug.replace("-", " ").title()


# --------------------------------------------------------------------------- difficulty


def difficulty_band(years: float | None, recent_feels: Sequence[Feel]) -> dict[Difficulty, int]:
    """Percent chance of an easy, medium or hard question.

    The band comes from years of experience (unknown counts as 1-3). If three of the
    last five ratings were "hard" it moves one step easier; three "easy" moves it one
    step harder. ``recent_feels`` runs oldest to newest; only its last five are read.
    """
    if years is None:
        index = _UNKNOWN_YEARS_BAND
    else:
        index = next((i for i, limit in enumerate(_BAND_LIMITS) if years < limit), len(_BANDS) - 1)

    latest = recent_feels[-FEEL_WINDOW:]
    if latest.count("hard") >= FEEL_QUORUM:
        index -= 1
    elif latest.count("easy") >= FEEL_QUORUM:
        index += 1
    index = max(0, min(index, len(_BANDS) - 1))

    easy, medium, hard = _BANDS[index]
    return {"easy": easy, "medium": medium, "hard": hard}


# --------------------------------------------------------------------------- topic weights


@dataclass(frozen=True)
class _LastPractice:
    day: date
    feel: Feel | None


def _last_practice(history: Sequence[HistoryItem], today: date) -> dict[str, _LastPractice]:
    """The newest done item per topic, ignoring anything dated after ``today``."""
    latest: dict[str, _LastPractice] = {}
    for item in sorted(history, key=lambda h: h.day):
        if item.done and item.day <= today:
            latest[item.topic] = _LastPractice(item.day, item.feel)
    return latest


def topic_weights(track: Track, history: Sequence[HistoryItem], today: date) -> dict[str, float]:
    """The track's topic weights, adjusted by what the user has and has not practised.

    - x2.0 for a topic not practised in the last 21 days (or ever);
    - x1.5 when the last question on the topic was rated "hard";
    - x0.3 for a topic practised in the last 2 days.

    The adjustments multiply. Only ``done`` items are practice.
    """
    last = _last_practice(history, today)
    weights: dict[str, float] = {}
    for topic, base in TRACK_TOPICS[track].items():
        weight = float(base)
        seen = last.get(topic)
        if seen is None or (today - seen.day).days > NEGLECT_AFTER_DAYS:
            weight *= NEGLECT_FACTOR
        elif (today - seen.day).days <= RECENT_DAYS:
            weight *= RECENT_FACTOR
        if seen is not None and seen.feel == "hard":
            weight *= HARD_FACTOR
        weights[topic] = weight
    return weights


# --------------------------------------------------------------------------- the pick


def _rng(user_id: str, day: date, swaps: int) -> random.Random:
    digest = sha256(f"{user_id}:{day.isoformat()}:{swaps}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _weighted_order(rng: random.Random, weights: Mapping[str, float], skip: str) -> Iterator[str]:
    """Every topic but ``skip``, one weighted draw at a time without replacement."""
    remaining = {topic: weight for topic, weight in weights.items() if topic != skip}
    while remaining:
        names = sorted(remaining)
        chosen = rng.choices(names, weights=[remaining[name] for name in names])[0]
        del remaining[chosen]
        yield chosen


def _nearest_first(drawn: Difficulty, band: Mapping[Difficulty, int]) -> list[Difficulty]:
    """The other difficulties, closest to ``drawn`` first.

    Equidistant ones go to whichever the user's band favours, then to the harder.
    """
    position = {d: i for i, d in enumerate(DIFFICULTIES)}
    others = [d for d in DIFFICULTIES if d != drawn]
    return sorted(
        others,
        key=lambda d: (abs(position[d] - position[drawn]), -band[d], -position[d]),
    )


def _attempts(
    rng: random.Random,
    weights: Mapping[str, float],
    tail_topics: Sequence[str],
    topic: str,
    difficulty: Difficulty,
    band: Mapping[Difficulty, int],
) -> Iterator[tuple[str, Difficulty]]:
    """``(topic, difficulty)`` cells to try, the drawn one first.

    Then the same topic at other difficulties; then each other topic, its draw
    difficulty first. Topics outside the track come last, in name order. Lazy, so
    the weighted order costs nothing unless the first cells are empty.
    """
    fallback = _nearest_first(difficulty, band)
    yield topic, difficulty
    for other in fallback:
        yield topic, other
    others = [*_weighted_order(rng, weights, topic)]
    others.extend(sorted(set(tail_topics) - set(others) - {topic}))
    for other_topic in others:
        yield other_topic, difficulty
        for other in fallback:
            yield other_topic, other


def _why(track: Track, topic: str, last: Mapping[str, _LastPractice], day: date) -> str:
    """Why a topic is on the plate, naming the practice-gap rule that applies to it, if any."""
    if topic not in TRACK_TOPICS[track]:
        return "a common interview topic"
    part_of = (
        "a core interview topic"
        if track == "general"
        else f"part of {TRACK_LABELS[track].lower()} interviews"
    )
    seen = last.get(topic)
    if seen is None:
        return f"{part_of}, and you have not practised it yet"
    gap = (day - seen.day).days
    if gap > NEGLECT_AFTER_DAYS:
        return f"{part_of}, and you last practised it {gap} days ago"
    if seen.feel == "hard":
        return f"{part_of}, and you rated your last one hard"
    return part_of


def _reason(
    track: Track,
    drawn: tuple[str, Difficulty],
    question: Question,
    topic: str,
    last: Mapping[str, _LastPractice],
    day: date,
    relaxed: tuple[str, ...],
) -> str:
    parts = [f"{topic_label(topic)} — {_why(track, topic, last, day)}."]
    if "topic" in relaxed:
        parts.append(
            f"Nothing left on {topic_label(drawn[0])} for you right now, so this is "
            f"{topic_label(topic)} instead."
        )
    if "difficulty" in relaxed:
        parts.append(
            f"No {drawn[1].title()} question left on it, so here is a {question.difficulty.title()}."
        )
    if "history" in relaxed:
        parts.append(
            f"You have done every free question that fits in the last {REPEAT_AFTER_DAYS} days, "
            "so this is a repeat."
        )
    return " ".join(parts)


def pick_question(
    *,
    user_id: str,
    day: date,
    swaps: int,
    track: Track,
    years: float | None,
    history: Sequence[HistoryItem],
    catalogue: Sequence[Question],
) -> Pick | None:
    """The question to suggest on ``day``, or ``None`` when the catalogue has nothing to offer.

    Deterministic in ``(user_id, day, swaps)``: draw a topic by weight, then a
    difficulty by band, then a question from that pool in id order. An empty pool
    relaxes the difficulty, then the topic, then the 180-day no-repeat rule, and the
    pick says which it relaxed. Each swap is a fresh draw that never repeats an earlier
    one of the same day unless the catalogue has nothing else.

    Only history strictly before ``day`` counts, so marking today's question done (or
    rating it) cannot change today's pick. Questions without topic tags are never picked.
    """
    if swaps < 0:
        raise ValueError("swaps must not be negative")
    ordered = sorted((q for q in catalogue if q.topics), key=lambda q: (q.id, q.slug))
    if not ordered:
        return None

    earlier = [item for item in history if item.day < day]
    last = _last_practice(earlier, day)
    recent = {
        item.slug for item in earlier if item.done and (day - item.day).days <= REPEAT_AFTER_DAYS
    }
    feels = [item.feel for item in sorted(earlier, key=lambda h: h.day) if item.feel is not None]
    band = difficulty_band(years, feels)
    weights = topic_weights(track, earlier, day)
    tail_topics = sorted({t for q in ordered for t in q.topics})

    taken: set[str] = set()
    chosen: Pick | None = None
    for swap in range(swaps + 1):
        chosen = _draw(
            _rng(user_id, day, swap),
            ordered=ordered,
            weights=weights,
            band=band,
            tail_topics=tail_topics,
            recent=recent,
            taken=taken,
            track=track,
            last=last,
            day=day,
        )
        if chosen is None:
            return None
        taken.add(chosen.question.slug)
    return chosen


def _draw(
    rng: random.Random,
    *,
    ordered: Sequence[Question],
    weights: Mapping[str, float],
    band: Mapping[Difficulty, int],
    tail_topics: Sequence[str],
    recent: set[str],
    taken: set[str],
    track: Track,
    last: Mapping[str, _LastPractice],
    day: date,
) -> Pick | None:
    topics = sorted(weights)
    topic = rng.choices(topics, weights=[weights[t] for t in topics])[0]
    difficulty = rng.choices(DIFFICULTIES, weights=[band[d] for d in DIFFICULTIES])[0]

    # Pass 0 honours every rule; pass 1 allows a question done within the last 180 days; pass 2
    # also allows an earlier swap of the same day, which only matters for a tiny catalogue.
    for relax_history, allow_taken in ((False, False), (True, False), (True, True)):
        blocked = set() if allow_taken else taken
        for cell_topic, cell_difficulty in _attempts(
            rng, weights, tail_topics, topic, difficulty, band
        ):
            pool = [
                q
                for q in ordered
                if q.difficulty == cell_difficulty
                and cell_topic in q.topics
                and q.slug not in blocked
                and (relax_history or q.slug not in recent)
            ]
            if not pool:
                continue
            question = rng.choice(pool)
            relaxed = tuple(
                name
                for name, applied in (
                    ("difficulty", question.difficulty != difficulty),
                    ("topic", cell_topic != topic),
                    ("history", question.slug in recent),
                )
                if applied
            )
            return Pick(
                question=question,
                topic=cell_topic,
                difficulty=question.difficulty,
                reason=_reason(
                    track, (topic, difficulty), question, cell_topic, last, day, relaxed
                ),
                relaxed=relaxed,
            )
    return None
