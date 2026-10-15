"""
domain/outreach_rank.py
~~~~~~~~~~~~~~~~~~~~~~~
Whom to write to: which companies are worth a message, and who inside one.

Two rankings, both deterministic and explainable. A score is a sum of named
weights, and every point comes with a reason line a person can read, so the
suggestion never has to say "because the model said so". No LLM runs here and
nothing is fetched: the service gathers the signals (a company's open roles,
its stack, its funding, its people) and this module only weighs them.

* **Companies** (spec 5.1): hiring for the target role, fresh funding, skills
  and sector the user shares, open source to contribute to. A company needs
  ``SUGGEST_THRESHOLD`` points, which means at least one strong signal; a
  named person and a few open roles are not a reason to write.
* **People** (spec 5.2): who to write to depends on how big the company is.
  At twenty people the founder is the hiring manager; at two thousand the
  founder is unreachable and a recruiter or a peer engineer is the way in.

Pure: no I/O, no clock. ``now`` is passed in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from domain.company_taxonomy import SECTORS, is_bigger_company, sector_from_text, size_bucket

# --- company weights (spec 5.1) -----------------------------------------------

#: Hiring for the user's target role in Bengaluru: the most direct route to a job.
HIRING_MATCH_POINTS = 35
HIRING_MATCH_MANY_POINTS = 40
HIRING_MATCH_MANY_AT = 3

#: Open roles, none of them matching: the company is growing, that is all.
OPEN_ROLES_POINTS = 8

#: Fresh money is the strongest predictor of new headcount. ``(days up to and
#: including, points)``, narrowest band first; past the last band it is worth nothing.
FUNDING_BANDS: tuple[tuple[int, int], ...] = ((30, 30), (90, 20), (180, 8))

SKILL_POINTS_EACH = 5
SKILL_POINTS_MAX = 20

SECTOR_POINTS = 10

#: Open source is a real but slower route in.
GOOD_FIRST_ISSUES_POINTS = 12

#: Someone on record to write to. Worth a nudge, never a reason on its own.
NAMED_PERSON_POINTS = 5

#: A company needs this many points to be suggested.
SUGGEST_THRESHOLD = 20

#: Reason lines kept per suggestion, strongest first.
MAX_REASONS = 3

DEFAULT_SUGGESTION_LIMIT = 25

# --- people weights (spec 5.2) ------------------------------------------------

SizeClass = Literal["small", "mid", "large"]

#: A company at one of these rounds is small however its headcount is recorded.
_SMALL_STAGES = frozenset({"pre_seed", "seed", "series_a"})

#: Base points per contact kind and company size. ``referral`` is someone the
#: user saved because they know them: the best way in at any size.
KIND_WEIGHTS: dict[SizeClass, dict[str, int]] = {
    "small": {
        "referral": 55,
        "founder": 50,
        "leader": 40,
        "engineer": 25,
        "recruiter": 20,
        "other": 15,
        "careers_inbox": 10,
    },
    "mid": {
        "referral": 55,
        "founder": 35,
        "leader": 45,
        "engineer": 30,
        "recruiter": 35,
        "other": 15,
        "careers_inbox": 15,
    },
    "large": {
        "referral": 55,
        "founder": 10,
        "leader": 35,
        "engineer": 40,
        "recruiter": 45,
        "other": 15,
        "careers_inbox": 20,
    },
}

USER_SAVED_BONUS = 15
VERIFIED_BONUS = 10
#: A stored profile link (LinkedIn, X, GitHub) or a sourced public email. One
#: bonus for either or both: a second channel is not a better person.
REACH_BONUS = 8

#: A careers page is a link, not a person. It is the thread's careers channel.
_NOT_A_PERSON = frozenset({"careers_page"})

_KIND_REASONS: dict[tuple[SizeClass, str], str] = {
    ("small", "founder"): "Founders read their own messages at this size",
    ("mid", "leader"): "Engineering leads hire for their own teams",
    ("large", "recruiter"): "Recruiters run the pipeline at this size",
    ("large", "engineer"): "An engineer here can refer you",
}
_USER_SAVED_REASON = "Someone you know"


# --- companies ----------------------------------------------------------------


@dataclass(frozen=True)
class CompanySignals:
    """What the service knows about one candidate company."""

    company_id: str
    name: str
    summary: str | None
    area: str | None
    stage: str | None
    sector: str | None
    last_funding_at: datetime | None
    last_round: str | None
    open_roles: int
    matching_roles: int
    target_role: str | None
    stack: tuple[str, ...]
    good_first_issues: int
    has_named_person: bool


@dataclass(frozen=True)
class UserSignals:
    """What the user's profile says: skills, and the sectors their goals name."""

    skills: tuple[str, ...]
    sectors: frozenset[str]


@dataclass(frozen=True)
class Suggestion:
    company_id: str
    name: str
    summary: str | None
    area: str | None
    stage: str | None
    sector: str | None
    score: int
    reasons: tuple[str, ...]
    last_funding_at: datetime | None


def _utc(moment: datetime) -> datetime:
    """A timestamp as UTC; a naive one is taken to be UTC already."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _age(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def funding_points(days: int) -> int:
    """Points for funding raised ``days`` ago: 30 up to a month, 20 to three, 8 to six."""
    for up_to, points in FUNDING_BANDS:
        if days <= up_to:
            return points
    return 0


def _skill_overlap(stack: Sequence[str], skills: Sequence[str]) -> list[str]:
    """The user's skills the company uses, spelled as the company spells them, in its order."""
    wanted = {s.strip().casefold() for s in skills if s.strip()}
    seen: set[str] = set()
    shared: list[str] = []
    for item in stack:
        label = item.strip()
        key = label.casefold()
        if key and key in wanted and key not in seen:
            seen.add(key)
            shared.append(label)
    return shared


def score_company(c: CompanySignals, u: UserSignals, now: datetime) -> Suggestion | None:
    """
    Weigh one company. ``None`` when it falls short of ``SUGGEST_THRESHOLD``.

    Each signal adds points and, where it has something to say, one reason line.
    The reasons are cut to the strongest ``MAX_REASONS``; equal strengths keep
    the order of the spec's table.
    """
    score = 0
    reasons: list[tuple[int, str]] = []

    if c.matching_roles > 0:
        points = (
            HIRING_MATCH_MANY_POINTS
            if c.matching_roles >= HIRING_MATCH_MANY_AT
            else HIRING_MATCH_POINTS
        )
        role = c.target_role or "for your target role"
        reasons.append((points, f"Hiring {role} · {c.matching_roles} open"))
        score += points
    elif c.open_roles > 0:
        reasons.append((OPEN_ROLES_POINTS, _plural(c.open_roles, "open role")))
        score += OPEN_ROLES_POINTS

    if c.last_funding_at is not None:
        days = max(0, (now - _utc(c.last_funding_at)).days)
        points = funding_points(days)
        if points:
            raised = (c.last_round or "").strip() or "funding"
            reasons.append((points, f"Raised {raised} · {_age(days)}"))
            score += points

    shared = _skill_overlap(c.stack, u.skills)
    if shared:
        points = min(len(shared) * SKILL_POINTS_EACH, SKILL_POINTS_MAX)
        reasons.append((points, f"Uses {', '.join(shared[:2])}"))
        score += points

    if c.sector is not None and c.sector in u.sectors:
        reasons.append((SECTOR_POINTS, SECTORS.get(c.sector, c.sector)))
        score += SECTOR_POINTS

    if c.good_first_issues >= 1:
        reasons.append((GOOD_FIRST_ISSUES_POINTS, _plural(c.good_first_issues, "good first issue")))
        score += GOOD_FIRST_ISSUES_POINTS

    if c.has_named_person:
        score += NAMED_PERSON_POINTS

    if score < SUGGEST_THRESHOLD:
        return None

    # Stable: equal strengths stay in the order they were weighed.
    strongest = sorted(reasons, key=lambda item: -item[0])[:MAX_REASONS]
    return Suggestion(
        company_id=c.company_id,
        name=c.name,
        summary=c.summary,
        area=c.area,
        stage=c.stage,
        sector=c.sector,
        score=score,
        reasons=tuple(text for _, text in strongest),
        last_funding_at=c.last_funding_at,
    )


def _suggestion_order(s: Suggestion) -> tuple[int, bool, float, str, str]:
    funded = s.last_funding_at
    return (
        -s.score,
        funded is None,
        -_utc(funded).timestamp() if funded is not None else 0.0,
        s.name.casefold(),
        s.company_id,
    )


def rank_companies(
    candidates: Iterable[CompanySignals],
    u: UserSignals,
    now: datetime,
    limit: int = DEFAULT_SUGGESTION_LIMIT,
) -> list[Suggestion]:
    """The best ``limit`` suggestions: score, then most recent funding, then name."""
    scored = (score_company(c, u, now) for c in candidates)
    ranked = sorted((s for s in scored if s is not None), key=_suggestion_order)
    return ranked[: max(limit, 0)]


# --- people -------------------------------------------------------------------


def size_class(
    tier: str | None,
    employee_count: int | None,
    employee_count_range: str | None,
    stage: str | None,
) -> SizeClass:
    """
    How a company's size changes who is worth writing to.

    ``large`` when it is a big company by type or headcount; ``small`` when the
    headcount is 50 or fewer or it has only raised an early round; else ``mid``.
    """
    if is_bigger_company(tier, employee_count, employee_count_range):
        return "large"
    if size_bucket(employee_count, employee_count_range) == "1-50" or stage in _SMALL_STAGES:
        return "small"
    return "mid"


@dataclass(frozen=True)
class PersonSignals:
    """One stored person at a company, as far as ranking cares."""

    contact_id: str
    name: str
    title: str | None
    kind: str
    verified: bool
    user_saved: bool
    has_direct_link: bool
    has_public_email: bool
    source_host: str | None


@dataclass(frozen=True)
class RankedPerson:
    contact_id: str
    name: str
    title: str | None
    kind: str
    verified: bool
    user_saved: bool
    source_host: str | None
    score: int
    reason: str


def _person_reason(kind: str, size: SizeClass, user_saved: bool) -> str:
    if user_saved:
        return _USER_SAVED_REASON
    listed = _KIND_REASONS.get((size, kind))
    return listed or f"{kind.replace('_', ' ').capitalize()} at this company"


def _rank_person(p: PersonSignals, size: SizeClass) -> RankedPerson:
    weights = KIND_WEIGHTS[size]
    score = weights.get(p.kind, weights["other"])
    if p.user_saved:
        score += USER_SAVED_BONUS
    if p.verified:
        score += VERIFIED_BONUS
    if p.has_direct_link or p.has_public_email:
        score += REACH_BONUS
    return RankedPerson(
        contact_id=p.contact_id,
        name=p.name,
        title=p.title,
        kind=p.kind,
        verified=p.verified,
        user_saved=p.user_saved,
        source_host=p.source_host,
        score=score,
        reason=_person_reason(p.kind, size, p.user_saved),
    )


def rank_people(people: Iterable[PersonSignals], size: SizeClass) -> list[RankedPerson]:
    """
    The people at a company, best first. Ties break by name.

    Unverified people are ranked too: almost every sourced person is unverified,
    and hiding them would hide the founders. Verification only adds points.
    """
    ranked = [_rank_person(p, size) for p in people if p.kind not in _NOT_A_PERSON]
    ranked.sort(key=lambda r: (-r.score, r.name.casefold(), r.contact_id))
    return ranked


# --- the user's profile -------------------------------------------------------

_CLAUSE_BREAK = re.compile(r"[.;,\n]+")


def sectors_for_profile(target_roles: list[str], career_goals: str | None) -> frozenset[str]:
    """
    The sectors a profile names, from target roles and the career-goals text.

    The goals text is read a clause at a time, because ``sector_from_text`` stops
    at the first sector it finds and "healthcare, fintech" names two.
    """
    texts = [*target_roles]
    if career_goals:
        texts.extend(_CLAUSE_BREAK.split(career_goals))
    found = (sector_from_text(text.strip()) for text in texts if text.strip())
    return frozenset(sector for sector in found if sector is not None)
