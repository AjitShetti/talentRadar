"""
services/outreach/oss.py
~~~~~~~~~~~~~~~~~~~~~~~~
Open issues a candidate could pick up at a company, from GitHub's search API.

Two halves, kept apart on purpose:

* :func:`refresh_issues` is the scheduled step behind
  ``POST /api/v1/ingest/outreach-issues`` (``.github/workflows/outreach.yml``).
  It asks GitHub about a bounded number of companies and stores one
  ``oss_issue_snapshots`` row for each. It never raises.
* :func:`snapshot_for` is the read the thread drawer uses. It only reads the
  database: **a page load never calls GitHub.** Unauthenticated search is ten
  requests a minute from a shared host, and one page that opened ten threads
  would use them all.

Rules the step keeps:

* **One request per company**, built with ``params=`` from an organisation name
  that was checked against GitHub's login pattern first. A stored value that is
  not a login is skipped and counted as an error; it never reaches a URL.
* **The rate limit is a stop, not a retry.** With ``GITHUB_TOKEN`` the requests
  are 2.2 s apart and a run is at most 60; without it 6.5 s and 20. A 403 or 429
  ends the run at once, and so does an answer that says none are left
  (``X-RateLimit-Remaining: 0``).
* **A run is bounded and stops for a reason.** It will not start a request that
  could finish after the wall-clock budget (a hard backstop cancels a hung one),
  it stops after five errors in a row (GitHub is down, not this company), and it
  stops at a 401 (a revoked token) without recording anything for that company.
  The result's ``stopped`` says which: ``budget``, ``rate_limited``, ``auth``,
  ``errors`` or ``None`` for a run that finished its list.
* **A failed check keeps what was known, but still counts as a check.** A 404 or
  422 (no such organisation) is recorded as ``not_found`` with no issues. Any other
  failure marks the row ``error`` and leaves its issues and total, so the drawer
  can still show the last good list; ``fetched_at`` moves to now, so a company that
  keeps failing waits its normal interval (a day in a thread, three otherwise)
  instead of sitting first in the queue. A refusal (``rate_limited``) leaves the
  date alone, so the company is first in line once the limit lifts.
* **Each company is written in its own transaction**, so one company that cannot
  be stored loses nothing found for the others.
* **An issue is only a link if it is in the organisation.** The URL must be an
  issue page of ``https://github.com/<org>/<repo>/issues/<n>``; anything else is
  dropped, and the kept link is rebuilt from the captured pieces. Titles and
  labels are flattened and cut, since they later reach a prompt and a page.
* **No model call.** Whatever GitHub returns is data.

Order of work, so a small budget goes where it matters: companies in somebody's
open thread (checked again after a day), then listed tech companies that raised
in the last 90 days or have open roles, then the rest, oldest check first and
never-checked first (the last two checked again after three days).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Select, and_, case, exists, or_, select

from agents.interview.role_context import _flat
from config.settings import get_settings
from domain.outreach import aware
from services.base import parse_uuid
from services.company_map.read import AREA_CITY, listed_clause
from services.github import _headers
from storage.database import AsyncSessionLocal
from storage.models import Company, Job, JobStatus, OssIssueSnapshot, OutreachThread

logger = logging.getLogger(__name__)

#: One comma-separated ``label:`` qualifier is an OR in GitHub's search syntax.
ISSUE_LABELS = ('"good first issue"', '"good-first-issue"', '"help wanted"')

#: How old a snapshot must be before the company is checked again.
ISSUE_REFRESH_DAYS = 3
#: The same, for a company somebody has an open thread with.
THREAD_ISSUE_REFRESH_DAYS = 1

#: What one snapshot keeps.
MAX_ISSUES = 5
MAX_LABELS = 4
LABEL_MAX = 40
TITLE_MAX = 200

#: Issues not updated in this long are not offered.
ISSUE_WINDOW_DAYS = 365
#: A company that raised this recently is checked ahead of the rest.
RAISED_WITHIN_DAYS = 90

SEARCH_URL = "https://api.github.com/search/issues"
#: The most one request is expected to take; the budget check allows for it.
REQUEST_TIMEOUT_SECONDS = 8.0
_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=4.0)

#: A run starts no request that could end after this many seconds...
DEFAULT_BUDGET_SECONDS = 240.0
#: ...and is cancelled outright this long after the budget, whatever is hung.
BACKSTOP_SECONDS = 30.0
#: This many errors in a row mean GitHub (or the network) is down: stop, do not stamp more rows.
MAX_CONSECUTIVE_ERRORS = 5

#: ``(seconds between requests, requests in a run)``, by whether a token is set.
_WITH_TOKEN = (2.2, 60)
_WITHOUT_TOKEN = (6.5, 20)

_RATE_LIMITED = frozenset({403, 429})
_NOT_FOUND = frozenset({404, 422})

# What this accepts as a GitHub login: 1 to 39 characters, the first a letter or digit,
# the rest letters, digits or hyphens. (GitHub also forbids a trailing or doubled hyphen
# in new names; older logins have them, so they are not rejected here.)
_ORG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_UPDATED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

#: Which counter a stored outcome adds to.
_COUNTER = {"ok": "ok", "not_found": "not_found", "error": "errors"}


# --- pure helpers ------------------------------------------------------------------


def search_query(org: str, today: date) -> str:
    """The search for open, unassigned beginner issues in ``org``, updated in the last year."""
    since = today - timedelta(days=ISSUE_WINDOW_DAYS)
    return (
        f"org:{org} is:issue is:open no:assignee archived:false "
        f"label:{','.join(ISSUE_LABELS)} updated:>={since.isoformat()}"
    )


def run_limits(has_token: bool) -> tuple[float, int]:
    """``(seconds between requests, requests in a run)`` for GitHub's search limits."""
    return _WITH_TOKEN if has_token else _WITHOUT_TOKEN


def _issue_url(org: str) -> re.Pattern[str]:
    """An issue page inside ``org``. The org matches in any case, as GitHub logins do."""
    return re.compile(
        rf"https://github\.com/(?P<owner>(?i:{re.escape(org)}))"
        r"/(?P<repo>(?![.]{1,2}/)[A-Za-z0-9._-]{1,100})/issues/(?P<number>[0-9]{1,9})"
    )


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _labels(value: Any) -> list[str]:
    labels: list[str] = []
    for entry in value if isinstance(value, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        label = _flat(name, LABEL_MAX) if isinstance(name, str) else ""
        if label:
            labels.append(label)
        if len(labels) == MAX_LABELS:
            break
    return labels


def _issue(item: Any, pattern: re.Pattern[str]) -> dict[str, Any] | None:
    """One search item as a stored issue, or ``None`` if it is not a link into the org."""
    if not isinstance(item, dict):
        return None
    url = item.get("html_url")
    match = pattern.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        return None
    owner, repo, number = match["owner"], match["repo"], match["number"]
    updated = item.get("updated_at")
    return {
        "repo": f"{owner}/{repo}",
        "number": int(number),
        "title": _flat(item.get("title"), TITLE_MAX),
        "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        "labels": _labels(item.get("labels")),
        "updated_at": updated
        if isinstance(updated, str) and _UPDATED_AT.fullmatch(updated)
        else None,
        "comments": _count(item.get("comments")),
    }


def parse_search(payload: dict[str, Any], org: str) -> tuple[int, list[dict[str, Any]]]:
    """``(GitHub's total, the issues worth keeping)`` from one search response.

    An item whose link is not an issue page of ``org`` is dropped, whatever else it
    says. At most :data:`MAX_ISSUES` are kept, and the total is never lower than the
    number kept.
    """
    pattern = _issue_url(org)
    items = payload.get("items")
    issues: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        issue = _issue(item, pattern)
        if issue is not None:
            issues.append(issue)
        if len(issues) == MAX_ISSUES:
            break
    return max(_count(payload.get("total_count")), len(issues)), issues


# --- the read ----------------------------------------------------------------------


async def snapshot_for(company_id: str | uuid.UUID) -> dict[str, Any] | None:
    """The stored snapshot for a company as an ``IssueSnapshot``, or ``None``.

    Only reads the database. ``None`` means the company has never been checked (or
    the id is not one), which the drawer shows as "not checked yet", not as an error.
    """
    parsed = parse_uuid(company_id)
    if parsed is None:
        return None
    async with AsyncSessionLocal() as session:
        row = await session.get(OssIssueSnapshot, parsed)
    if row is None:
        return None
    return {
        "org": row.org,
        "status": row.status,
        "total_count": row.total_count,
        "issues": list(row.issues or []),
        "fetched_at": aware(row.fetched_at).isoformat(),
    }


# --- the step ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Outcome:
    """What GitHub said about one organisation."""

    status: str  # ok | not_found | rate_limited | error | auth (never stored)
    total: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    #: The response said no requests are left in this window.
    exhausted: bool = False


def _due_statement(now: datetime) -> Select[tuple[uuid.UUID, str | None]]:
    """The query behind :func:`_due_companies`, apart from running it."""
    in_open_thread = exists().where(
        OutreachThread.company_id == Company.id, OutreachThread.closed_at.is_(None)
    )
    has_open_roles = exists().where(Job.company_id == Company.id, Job.status == JobStatus.ACTIVE)
    raised_lately = Company.last_funding_at >= now - timedelta(days=RAISED_WITHIN_DAYS)
    tier = case((in_open_thread, 0), (or_(has_open_roles, raised_lately), 1), else_=2)

    checked = OssIssueSnapshot.fetched_at
    due = or_(
        checked.is_(None),
        checked <= now - timedelta(days=ISSUE_REFRESH_DAYS),
        and_(in_open_thread, checked <= now - timedelta(days=THREAD_ISSUE_REFRESH_DAYS)),
    )
    return (
        select(Company.id, Company.github_org)
        .outerjoin(OssIssueSnapshot, OssIssueSnapshot.company_id == Company.id)
        .where(
            Company.github_org.is_not(None),
            Company.github_org != "",
            or_(in_open_thread, listed_clause(AREA_CITY)),
            due,
        )
        .order_by(tier, checked.asc().nulls_first(), Company.name, Company.id)
    )


async def _due_companies(now: datetime) -> list[tuple[uuid.UUID, str]]:
    """Companies due a check, in the order to check them: ``(id, github_org)``.

    A company needs a ``github_org``. One in somebody's open thread is due once its
    snapshot is a day old, wherever it sits on the map; every other must be a listed
    Bengaluru tech employer (suggestions read no other city, so a search for one would
    help no one) and is due at three days. Never-checked sorts first in each tier.
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(_due_statement(now))).all()
    return [(company_id, org) for company_id, org in rows if org]


def _none_left(response: httpx.Response) -> bool:
    """Does ``X-RateLimit-Remaining`` say the window is used up?"""
    raw = response.headers.get("X-RateLimit-Remaining", "").strip()
    return raw.isascii() and raw.isdigit() and int(raw) == 0


def _log_retry_after(response: httpx.Response) -> None:
    """Log how long GitHub asked us to wait, only if it is a plain number of seconds.

    The header is the other side's text: it is logged as an ``int`` or not at all.
    """
    raw = response.headers.get("Retry-After", "").strip()
    if raw.isascii() and raw.isdigit() and len(raw) <= 7:
        logger.info("GitHub asked to wait %d seconds", int(raw))


async def _search(client: httpx.AsyncClient, org: str, today: date) -> _Outcome:
    """Ask GitHub once. A refusal, a bad token, a missing org, a bad answer and a dead
    network are each an outcome; anything else is a bug and propagates."""
    try:
        response = await client.get(
            SEARCH_URL,
            params={
                "q": search_query(org, today),
                "sort": "updated",
                "order": "desc",
                "per_page": MAX_ISSUES,
            },
            headers=_headers(),
        )
    except httpx.HTTPError as exc:
        logger.info("GitHub issue search got no answer: %s", type(exc).__name__)
        return _Outcome("error")

    code = response.status_code
    if code == 401:
        # A revoked or expired token: it says nothing about this organisation.
        logger.warning("GitHub rejected the configured token (401); stopping the run")
        return _Outcome("auth")
    if code in _RATE_LIMITED:
        _log_retry_after(response)
        return _Outcome("rate_limited")
    exhausted = _none_left(response)
    if code in _NOT_FOUND:
        return _Outcome("not_found", exhausted=exhausted)
    if code != 200:
        logger.info("GitHub issue search answered %s", code)
        return _Outcome("error", exhausted=exhausted)
    try:
        payload = response.json()
    except ValueError:
        return _Outcome("error", exhausted=exhausted)
    if not isinstance(payload, dict):
        return _Outcome("error", exhausted=exhausted)
    total, issues = parse_search(payload, org)
    return _Outcome("ok", total, issues, exhausted)


async def _record(company_id: uuid.UUID, org: str, outcome: _Outcome, now: datetime) -> None:
    """Store one company's outcome in its own transaction.

    ``ok`` and ``not_found`` replace the list and the date. ``error`` keeps the
    last good list and total but moves the date to now: the attempt was a check, so
    a company that keeps failing waits its interval instead of heading the queue
    every run. ``rate_limited`` changes only the status and leaves the date, and a
    company with no row yet gets no ``rate_limited`` row, so either stays first in
    line once the limit lifts. A company with no row gets an empty ``error`` row.
    """
    async with AsyncSessionLocal() as session:
        row = await session.get(OssIssueSnapshot, company_id)
        if row is None:
            if outcome.status == "rate_limited":
                return
            row = OssIssueSnapshot(
                company_id=company_id,
                org=org,
                status=outcome.status,
                total_count=0,
                issues=[],
                fetched_at=now,
            )
            session.add(row)
        elif row.org.casefold() != org.casefold():
            # The company's organisation changed: the old list belongs to another one.
            row.org, row.total_count, row.issues, row.fetched_at = org, 0, [], now
        row.status = outcome.status
        if outcome.status in ("ok", "not_found"):
            row.total_count, row.issues = outcome.total, outcome.issues
        if outcome.status != "rate_limited":
            row.fetched_at = now
        await session.commit()


@dataclass
class _Tally:
    """What a run has done so far, kept outside :func:`_run` so a failure cannot lose it."""

    counts: dict[str, int] = field(
        default_factory=lambda: {
            "checked": 0,
            "ok": 0,
            "not_found": 0,
            "errors": 0,
            "rate_limited": 0,
        }
    )
    #: Why the run ended before its list did: budget | rate_limited | auth | errors.
    stopped: str | None = None


async def _run(
    tally: _Tally,
    limit: int,
    client: httpx.AsyncClient | None,
    sleep: Callable[[float], Awaitable[None]],
    now: datetime | None,
    clock: Callable[[], float],
    budget: float,
) -> None:
    started = clock()
    counts = tally.counts
    moment = datetime.now(UTC) if now is None else aware(now)
    gap, run_cap = run_limits(bool(get_settings().github_token))
    cap = min(limit, run_cap)
    if cap <= 0:
        return

    targets: list[tuple[uuid.UUID, str]] = []
    for company_id, org in await _due_companies(moment):
        if len(targets) == cap:
            break
        if _ORG.fullmatch(org) is None:
            # Not a login, so never put in a URL; it does not use up a request.
            logger.warning("Company %s has a github_org that is not a GitHub login", company_id)
            counts["checked"] += 1
            counts["errors"] += 1
            continue
        targets.append((company_id, org))
    if not targets:
        return

    http = httpx.AsyncClient(timeout=_TIMEOUT) if client is None else client
    errors_in_a_row = 0
    try:
        for index, (company_id, org) in enumerate(targets):
            if clock() - started + gap + REQUEST_TIMEOUT_SECONDS > budget:
                tally.stopped = "budget"
                break
            if index:
                await sleep(gap)
            counts["checked"] += 1
            outcome = await _search(http, org, moment.date())
            if outcome.status == "auth":
                counts["errors"] += 1
                tally.stopped = "auth"
                break
            try:
                await _record(company_id, org, outcome, moment)
                stored = True
            except Exception:
                logger.warning(
                    "Could not store the issues of company %s", company_id, exc_info=True
                )
                stored = False
            if outcome.status == "rate_limited":
                counts["rate_limited"] += 1
                tally.stopped = "rate_limited"
                break
            counts[_COUNTER[outcome.status] if stored else "errors"] += 1
            if outcome.exhausted:
                tally.stopped = "rate_limited"
                break
            errors_in_a_row = errors_in_a_row + 1 if outcome.status == "error" or not stored else 0
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                tally.stopped = "errors"
                break
    finally:
        if client is None:
            await http.aclose()


async def refresh_issues(
    limit: int,
    *,
    client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: datetime | None = None,
    clock: Callable[[], float] = time.monotonic,
    budget: float = DEFAULT_BUDGET_SECONDS,
) -> dict[str, int | str | None]:
    """Check up to ``limit`` due companies against GitHub and store what it says.

    The cap is ``limit`` or the run limit for the token (60 with one, 20 without),
    whichever is smaller. Returns ``{"checked", "ok", "not_found", "errors",
    "rate_limited", "stopped"}``; ``checked`` counts every company looked at,
    including one skipped for an unusable organisation name, so the four outcome
    counts add up to it. ``stopped`` is why the run ended before its list did
    (``"budget"``, ``"rate_limited"``, ``"auth"``, ``"errors"``), or ``None``.

    No request is started that could end after ``budget`` seconds of ``clock``; a
    backstop of ``BACKSTOP_SECONDS`` more cancels a request that hangs, and the counts
    still come back (``stopped="budget"``).

    Never raises. An unexpected failure comes back as the same counts plus
    ``"error"`` (the exception's class name); what was stored before it stays.
    ``client``, ``sleep``, ``now`` and ``clock`` are for tests.
    """
    tally = _Tally()
    error: str | None = None
    scope = asyncio.timeout(budget + BACKSTOP_SECONDS)
    try:
        async with scope:
            await _run(tally, limit, client, sleep, now, clock, budget)
    except Exception as exc:
        if isinstance(exc, TimeoutError) and scope.expired():
            logger.warning("The good-first-issue refresh hit its time limit")
            tally.stopped = "budget"
        else:
            logger.warning("The good-first-issue refresh failed", exc_info=True)
            error = type(exc).__name__
    summary: dict[str, int | str | None] = {**tally.counts, "stopped": tally.stopped}
    if error is not None:
        summary["error"] = error
    logger.info("Good-first-issue refresh: %s", summary)
    return summary
