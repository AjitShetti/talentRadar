"""
services/job_boards/discover.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Finding boards for the job-board roster. Design:
docs/superpowers/specs/2026-11-23-job-board-roster-design.md, "Discovery steps".

Two ways in live here; the YC list and the careers-page links are added to this module by
their own steps.

``discover_open_list``
    Seeds the roster from the open list of public ATS boards: **storesignalhq/ats-job-boards**
    (``boards.csv``, https://github.com/storesignalhq/ats-job-boards), built from the Common
    Crawl URL index and published under CC BY 4.0 (its announcement says so; the repository
    carries no licence file). The credit the licence asks for is this paragraph and the
    data-sources notes in ``DEPLOY.md``. Only the ``ats``, ``slug`` and ``company_name`` columns
    are read, plus the ``board_url`` of a Personio row (for its ``de`` or ``com`` host, never
    requested). Every row lands as a ``candidate``: the sweep re-checks each board against its
    own ATS before it is trusted, so a stale or wrong row costs one request.

``probe_company`` / ``discover_probe``
    Guesses a listed tech company's board id from its name and site (``slug_candidates``) on the
    ATSes whose unknown ids answer 404 (``PROBE_ATS``). A guess is kept only when the board
    has at least one India role **and**, where the ATS states a company name, that name is the
    company's: ``slice`` on Greenhouse is not Slice, the Bengaluru payments company.

Rules every step here follows
-----------------------------
* The list is data from outside. A row is bounded and validated (``domain.job_boards.board_ref``)
  before it can reach a table or a request; one it cannot use is skipped, and one bad row never
  stops the run.
* No database connection is held across a request. ``session`` belongs to the caller, so this
  module cannot close it, but it ends its transaction (``commit``) before each stretch of
  network work and opens the next only to write: the open list commits every
  :data:`BATCH_SIZE` rows, the probe once per hit.
* Requests carry the project User-Agent and never follow a redirect.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from domain.job_boards import (
    PACE_SECONDS,
    PROBE_ATS,
    AtsName,
    BoardRef,
    board_from_url,
    board_ref,
    is_india_role,
    names_match,
    slug_candidates,
)
from ingestion.boards import READERS
from ingestion.boards.base import USER_AGENT, BoardRead, one_line
from ingestion.boards.greenhouse import read_board_name
from services.cache_backend import CacheBackend
from services.job_boards.store import upsert_board
from storage.models import Company, JobBoard

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]

# -- the open list ------------------------------------------------------------------------

#: The list is about 3 MB; one over this is refused rather than read to the end.
MAX_LIST_BYTES = 20 * 1024 * 1024

#: A single line over this is not a row of this list.
_MAX_LINE_BYTES = 64 * 1024

#: A quoted field that spans lines is joined up to this many characters, then given up on.
_MAX_RECORD_CHARS = 8 * 1024

#: Rows upserted, then committed, together.
BATCH_SIZE = 500

#: A company name longer than this is cut: a list we did not write fills a ``Text`` column.
_MAX_NAME_CHARS = 200

_LIST_TIMEOUT_SECONDS = 60.0


class OpenListError(RuntimeError):
    """The open list could not be read (a bad status, a redirect, over the cap, an unknown layout)."""


@dataclass(frozen=True)
class _Columns:
    """Where the three columns this step reads, and the Personio host, sit in a row."""

    ats: int
    slug: int
    name: int | None
    board_url: int | None


def _columns(header: list[str]) -> _Columns:
    names = [cell.strip().lstrip("﻿").lower() for cell in header]
    if "ats" not in names or "slug" not in names:
        raise OpenListError("the open list has no ats and slug columns")

    def where(column: str) -> int | None:
        return names.index(column) if column in names else None

    return _Columns(
        ats=names.index("ats"),
        slug=names.index("slug"),
        name=where("company_name"),
        board_url=where("board_url"),
    )


def _cell(fields: list[str], index: int | None) -> str:
    return fields[index].strip() if index is not None and index < len(fields) else ""


def _board_from_row(fields: list[str], columns: _Columns) -> tuple[BoardRef, str | None] | None:
    """
    The board a row names and the company name it gives, or ``None`` for a row to skip.

    A row is skipped when its ATS is not one of the nine or its slug is not a valid board id
    (a few real Ashby ids hold a dot, which no other reader of a board id accepts). The slug's
    case follows the ATS (``board_ref``). A Personio row's region comes from the host of its
    ``board_url`` (``.de`` or ``.com``) and is ``de`` when the row has none.
    """
    ats = _cell(fields, columns.ats).lower()
    region = None
    if ats == "personio":
        named = board_from_url(_cell(fields, columns.board_url))
        region = named.region if named is not None and named.ats == "personio" else None
    ref = board_ref(ats, _cell(fields, columns.slug), region)
    if ref is None:
        return None
    name = one_line(_cell(fields, columns.name))[:_MAX_NAME_CHARS].strip()
    return ref, name or None


def _feed(open_record: str, raw: bytes) -> tuple[list[str] | None, str]:
    """
    One more line of the file: ``(the row it completes or None, the record still open)``.

    A quoted field may hold a line break, so a line with an odd number of quotes starts a
    record that the following lines finish. A record that grows past :data:`_MAX_RECORD_CHARS`
    (a stray quote) or that the CSV reader rejects is dropped.
    """
    line = raw.decode("utf-8", errors="replace").rstrip("\r")
    text = f"{open_record}\n{line}" if open_record else line
    if text.count('"') % 2:
        return None, (text if len(text) <= _MAX_RECORD_CHARS else "")
    if not text.strip():
        return None, ""
    try:
        return next(csv.reader([text]), []), ""
    except csv.Error:
        return None, ""


async def _records(response: httpx.Response) -> AsyncGenerator[list[str], None]:
    """The rows of a streamed CSV one at a time, never holding more than the line being read."""
    total = 0
    pending = b""
    open_record = ""
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_LIST_BYTES:
            raise OpenListError("the open list is over the size cap")
        pending += chunk
        *lines, pending = pending.split(b"\n")
        if len(pending) > _MAX_LINE_BYTES:
            raise OpenListError("the open list has a line over the length cap")
        for raw in lines:
            row, open_record = _feed(open_record, raw)
            if row is not None:
                yield row
    row, _ = _feed(open_record, pending) if (pending or open_record) else (None, "")
    if row is not None:
        yield row


async def discover_open_list(
    session: AsyncSession, client: httpx.AsyncClient, *, limit: int
) -> dict[str, int]:
    """
    Add the open list's boards to the roster as ``candidate`` rows (``discovered_via='open_list'``).

    The CSV (``settings.job_boards_open_list_url``, see the module docstring for its source and
    licence) is streamed and read a row at a time: at most ``limit`` data rows, at most
    :data:`MAX_LIST_BYTES` bytes, no redirect. Rows whose ATS is one of the nine and whose slug
    is valid are upserted through :func:`~services.job_boards.store.upsert_board` and committed
    :data:`BATCH_SIZE` at a time, so a board already on the roster is left exactly as it is and
    a second run inserts nothing.

    Returns ``{"rows": data rows read, "kept": rows that named a usable board, "inserted": new
    rows}``. Raises :class:`OpenListError` when the list cannot be read; the rows read before
    that are kept, which is safe because every row is independent.
    """
    url = get_settings().job_boards_open_list_url
    rows = kept = inserted = 0
    batch: list[tuple[BoardRef, str | None]] = []

    async def flush() -> None:
        nonlocal inserted
        for ref, name in batch:
            if await upsert_board(session, ref, via="open_list", company_name=name):
                inserted += 1
        batch.clear()
        await session.commit()

    failure: OpenListError | None = None
    try:
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv, text/plain"},
            follow_redirects=False,
            timeout=_LIST_TIMEOUT_SECONDS,
        ) as response:
            if response.status_code != 200:
                raise OpenListError(f"the open list answered {response.status_code}")
            declared = response.headers.get("content-length", "").strip()
            if declared.isdigit() and int(declared) > MAX_LIST_BYTES:
                raise OpenListError("the open list is over the size cap")
            columns: _Columns | None = None
            async with aclosing(_records(response)) as records:
                async for fields in records:
                    if columns is None:
                        columns = _columns(fields)
                        continue
                    if rows >= limit:
                        break
                    rows += 1
                    found = _board_from_row(fields, columns)
                    if found is None:
                        continue
                    kept += 1
                    batch.append(found)
                    if len(batch) >= BATCH_SIZE:
                        await flush()
            if columns is None:
                raise OpenListError("the open list is empty")
    except OpenListError as exc:
        failure = exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        failure = OpenListError(f"the open list could not be read: {type(exc).__name__}")
    await flush()
    if failure is not None:
        logger.warning("Open list stopped after %d rows: %s", rows, failure)
        raise failure
    logger.info("Open list: %d rows, %d usable, %d new", rows, kept, inserted)
    return {"rows": rows, "kept": kept, "inserted": inserted}


# -- the name probe -----------------------------------------------------------------------

#: One company is probed with at most this many requests, a hit's name check included.
MAX_PROBE_REQUESTS = 24

#: A company is probed at most once per this long (hit or miss).
PROBED_TTL_SECONDS = 90 * 24 * 60 * 60

#: No company is started after this many seconds. Workable's pace alone is 10 s a request.
PROBE_BUDGET_SECONDS = 480.0

#: The ATSes that state a company name a probe can check: Greenhouse's ``/v1/boards/{board}``,
#: Workable's account name and Keka's ``careerportalinfo``. A hit on one of them is kept only
#: when that name was read and is the company's; the others state none, so an India role is
#: all a hit on them can show.
_NAMED_ATS: frozenset[AtsName] = frozenset({"greenhouse", "workable", "keka"})


def probed_key(subject: str) -> str:
    """The cache key that says a company (its id, or a YC company's domain) was probed lately."""
    return f"tr:boards:probed:{subject}"


class _OutOfRequests(httpx.TransportError):
    """Raised inside a company's probe once its budget is spent; a reader reads it as an ``error``."""


@dataclass
class _Budget:
    """One company's requests."""

    limit: int
    spent: int = 0


@dataclass
class _ProbeRun:
    """What every company probed in one run shares: the pacing, and the ATSes that said stop."""

    sleep: Sleep
    #: Hosts already asked in this run: a second request to one waits its ``PACE_SECONDS``.
    touched: set[AtsName] = field(default_factory=set)
    #: ATSes that answered 429: not asked again for the rest of the run.
    rate_limited: set[AtsName] = field(default_factory=set)

    async def wait_turn(self, ats: AtsName) -> None:
        if ats in self.touched:
            await self.sleep(PACE_SECONDS[ats])
        self.touched.add(ats)


class _Metered:
    """
    The client as a board reader sees it, with one company's budget and the run's pacing in front.

    Every reader makes its requests through ``client.stream`` (``ingestion.boards.base._fetch``),
    so this is the one place the probe counts, paces and stops them, whatever a reader asks for
    (Keka makes two, Greenhouse's name check is a second). Past the budget the request is not
    sent: it fails with a transport error, which the reader reports as ``error``.
    """

    def __init__(
        self, client: httpx.AsyncClient, ats: AtsName, run: _ProbeRun, budget: _Budget
    ) -> None:
        self._client = client
        self._ats = ats
        self._run = run
        self._budget = budget

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs: Any) -> AsyncIterator[httpx.Response]:
        if self._budget.spent >= self._budget.limit:
            raise _OutOfRequests("the probe's request budget is spent")
        self._budget.spent += 1
        await self._run.wait_turn(self._ats)
        async with self._client.stream(method, url, **kwargs) as response:
            yield response


async def _read(ats: AtsName, client: httpx.AsyncClient, ref: BoardRef) -> BoardRead | None:
    try:
        return await READERS[ats](client, ref)
    except Exception as exc:  # a reader never raises; one that does is a miss, not a crash
        logger.warning("Probe read of %s raised %s", ref.source, type(exc).__name__)
        return None


async def _is_the_company(
    client: httpx.AsyncClient, ref: BoardRef, read: BoardRead, name: str
) -> bool:
    """
    Whether a board that has India roles is ``name``'s.

    On an ATS that states a name (:data:`_NAMED_ATS`) the name must have been read and must
    match under ``domain.company_identity.normalize_name``: one that could not be read is no
    confirmation, and a wrong match would file another company's roles under this one. The
    other ATSes state none, so there is nothing more to check.
    """
    if ref.ats not in _NAMED_ATS:
        return True
    stated = await read_board_name(client, ref) if ref.ats == "greenhouse" else read.company_name
    return stated is not None and names_match(name, stated)


async def _probe(
    client: httpx.AsyncClient, *, name: str, domain: str | None, run: _ProbeRun
) -> BoardRef | None:
    budget = _Budget(MAX_PROBE_REQUESTS)
    # Slug-major: the likeliest guess is tried on every ATS before the next guess is.
    for slug in slug_candidates(name, domain):
        for ats in PROBE_ATS:
            if budget.spent >= budget.limit:
                return None
            ref = board_ref(ats, slug)
            if ref is None or ats in run.rate_limited:
                continue
            metered = cast("httpx.AsyncClient", _Metered(client, ats, run, budget))
            read = await _read(ats, metered, ref)
            if read is None:
                continue
            if read.outcome == "rate_limited":
                run.rate_limited.add(ats)
                continue
            if read.outcome != "ok" or not any(is_india_role(r.location_raw) for r in read.roles):
                continue
            if await _is_the_company(metered, ref, read, name):
                return ref
    return None


async def probe_company(
    client: httpx.AsyncClient,
    *,
    name: str,
    domain: str | None,
    sleep: Sleep = asyncio.sleep,
) -> BoardRef | None:
    """
    The board of the company ``name`` (site ``domain``) on one of the :data:`PROBE_ATS`, or ``None``.

    Tries each of ``slug_candidates(name, domain)`` on each ATS, likeliest guess first, and
    returns the first board that has at least one India role and, on an ATS that states a company
    name (Greenhouse, Workable, Keka), whose name matches ``name``. At most
    :data:`MAX_PROBE_REQUESTS` requests are made, the name check's included. A second request to
    one ATS host waits its ``PACE_SECONDS`` through ``sleep`` (injectable, so a test does not
    wait); an ATS that answers 429 is not asked again by this call.
    """
    return await _probe(client, name=name, domain=domain, run=_ProbeRun(sleep))


@dataclass(frozen=True)
class _Target:
    """A company to probe, as plain values: the query's transaction is over before the probe starts."""

    id: uuid.UUID
    name: str
    domain: str | None


async def _unprobed_companies(session: AsyncSession, limit: int) -> list[_Target]:
    """
    Up to ``limit`` listed tech companies with no board and no probe in the last 90 days.

    Taken in a random order each run. With a cache that remembers (Redis) the order does not
    matter; without one (the in-process fallback forgets on every restart) a fixed order would
    probe the same first companies every run and never reach the rest.
    """
    has_board = select(JobBoard.id).where(JobBoard.company_id == Company.id).exists()
    rows = (
        await session.execute(
            select(Company.id, Company.name, Company.domain)
            .where(Company.is_tech.is_(True), ~has_board)
            .order_by(func.random())
        )
    ).all()
    chosen: list[_Target] = []
    for company_id, name, domain in rows:
        if len(chosen) >= limit:
            break
        if await CacheBackend.get(probed_key(str(company_id))) is None:
            chosen.append(_Target(company_id, name, domain))
    return chosen


async def discover_probe(
    session: AsyncSession,
    client: httpx.AsyncClient,
    *,
    limit: int,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    budget_seconds: float = PROBE_BUDGET_SECONDS,
) -> dict[str, int]:
    """
    Probe up to ``limit`` listed tech companies that have no board yet, and add the boards found.

    The companies are chosen first and the transaction that read them is ended before any
    request. Each is probed with :func:`probe_company`'s rules (pacing shared across the whole
    run, so the next company's first request to a host waits too); a hit is upserted as a
    ``candidate`` with ``via='name_probe'`` and the company's id, and committed at once, so a run
    that is cut short keeps what it found. A hit on a board already on the roster (the open
    list got there first) adds the company to it and inserts nothing. Every company probed,
    hit or miss, gets the ``tr:boards:probed:<company_id>`` key for 90 days, so it is not
    probed again soon. No company is started after ``budget_seconds``; those left are picked up
    by the next run.

    Returns ``{"probed": companies probed, "found": boards found, "inserted": new rows}``.
    """
    started = clock()
    targets = await _unprobed_companies(session, limit) if limit > 0 else []
    await session.commit()  # the read is over: no connection is held while requests are made

    run = _ProbeRun(sleep)
    probed = found = inserted = 0
    for target in targets:
        if clock() - started >= budget_seconds:
            break
        ref = await _probe(client, name=target.name, domain=target.domain, run=run)
        probed += 1
        if ref is not None:
            found += 1
            if await upsert_board(
                session, ref, via="name_probe", company_id=target.id, company_name=target.name
            ):
                inserted += 1
            await session.commit()
        await CacheBackend.set(
            probed_key(str(target.id)), "hit" if ref is not None else "miss", PROBED_TTL_SECONDS
        )
    logger.info("Name probe: %d companies, %d boards found, %d new", probed, found, inserted)
    return {"probed": probed, "found": found, "inserted": inserted}
