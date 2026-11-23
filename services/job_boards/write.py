"""
services/job_boards/write.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Writing one board's India roles into ``jobs``: what the sweep does with a
:class:`~ingestion.boards.base.BoardRead`.

This is not ``persist_live_jobs``, for two reasons that are the point of the module:

* ``persist_live_jobs`` skips any URL it saw in the last seven days (the ``tr:seen:``
  cache), so a daily read of a board would never refresh a role the index already
  holds, and ``last_seen_at`` would stop moving for exactly the rows a board still
  lists. Here a role already stored is refreshed on every read.
* ``persist_live_jobs`` picks a non-board job's company by *name* (``posting_company``),
  which is how Greenhouse's ``slice`` (a company in Skopje) would land on the Indian
  fintech of the same name. A board's jobs go to the company the board is linked to, or
  to a placeholder company keyed by the board itself, and never to a name match. Live
  search files a board role the same way (``live_board_company_id``), and an existing
  board row keeps the company it was given.

What it keeps and what it does not
----------------------------------
* A role is written only when its own location text says India
  (:func:`domain.job_boards.is_india_role`), its URL passes
  :func:`ingestion.validation.validate_job_url`, and it is among the first
  :data:`MAX_ROLES_PER_BOARD` such roles.
* ``external_id`` is the MD5 of the posting URL, exactly as the live path computes it
  (``job_persistence._stable_external_id``), never the ATS's own id (a reader leaves that
  on the entity; it is ignored here). A role found by live search and by the sweep is
  therefore one row under ``uq_jobs_external_id_source``.
* A stored row of this board that is not in a **complete** read is closed. "In the read"
  means anywhere on the board: a role that moved from Bengaluru to plain "Remote" is
  still listed, so it is not closed (it is simply no longer an India role, and no longer
  refreshed, so it ages out). An incomplete read, and an absent role under a cap, close
  nothing.
* "The same role" is its URL **or** its ATS posting id (``_posting_key``): Greenhouse moves a
  board between hosts and employers link the posting on their own site (``?gh_jid=``), and a
  role whose URL changed is neither closed nor stored twice. The stored row follows the new URL.
* A role a complete read lists is confirmed open (``last_verified_at``, as an OPEN re-check
  records it). The re-check never reads a whole board of six of the ATSes on a request or on its
  own schedule: this read is how those rows are confirmed, and how they close.

The caller owns the transaction: nothing here commits, and nothing here makes a request.
Rows are written structurally (``enrichment_status='raw'``, no LLM call), and the items
to embed are returned for new rows, and for a row that moved to a new URL (its key, the URL's
MD5, changed), for the caller to embed once it has committed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities import Job
from domain.job_boards import is_india_role
from domain.posting_urls import posting_ref
from ingestion.boards.base import BoardRead
from ingestion.validation import validate_job_url
from services.job_persistence import (
    _stable_external_id,
    board_refresh_values,
    build_job_kwargs,
    embedding_text,
)
from services.liveness import record_sighting
from storage.models import Company, JobBoard
from storage.models import Job as JobRow
from storage.repository import JobRepository

#: At most this many India roles are written from one board.
MAX_ROLES_PER_BOARD = 200

#: ``companies.domain`` for a board that is linked to no company: keyed by the board
#: itself, so it never merges with a company the map lists under the same name. It ends in
#: ``.talentradar.internal``, which the company map already treats as a placeholder.
PLACEHOLDER_SUFFIX = ".board.talentradar.internal"

#: ``companies.name`` is ``VARCHAR(256)``.
_COMPANY_NAME_MAX = 256

#: Rows closed per statement, so a very large board does not bind thousands of ids at once.
_CLOSE_CHUNK = 500


@dataclass
class WriteSummary:
    """What one board write did."""

    inserted: int = 0
    updated: int = 0
    closed: int = 0
    rejected_url: int = 0
    skipped_budget: int = 0


def _placeholder_domain_for(ats: str, board: str) -> str:
    return f"{ats}-{board}{PLACEHOLDER_SUFFIX}"


def placeholder_domain(board: JobBoard) -> str:
    """The ``companies.domain`` of the placeholder company of an unlinked board."""
    return _placeholder_domain_for(board.ats, board.board)


async def _find_placeholder(session: AsyncSession, domain: str) -> uuid.UUID | None:
    """The id of the company holding this placeholder domain, if any."""
    return (
        await session.execute(select(Company.id).where(Company.domain == domain))
    ).scalar_one_or_none()


async def placeholder_company_id(
    session: AsyncSession, *, ats: str, board: str, name: str | None
) -> uuid.UUID:
    """
    The placeholder company of one board, keyed by the board and created on first use, named
    ``name`` or else the board id. Never matched by name. The caller owns the transaction.

    The sweep and live search can both create the same placeholder at once. The insert is made in
    a savepoint, so the loser's unique-key failure (``companies.domain``) rolls back only itself
    and the caller's transaction, which may hold a whole batch, survives; the winner's row is
    then the answer.
    """
    domain = _placeholder_domain_for(ats, board)
    found = await _find_placeholder(session, domain)
    if found is not None:
        return found

    label = (name or "").strip() or board
    company = Company(id=uuid.uuid4(), name=label[:_COMPANY_NAME_MAX], domain=domain)
    try:
        async with session.begin_nested():
            session.add(company)
    except IntegrityError:
        winner = await _find_placeholder(session, domain)
        if winner is None:
            raise
        return winner
    return company.id


async def board_company_id(session: AsyncSession, board: JobBoard) -> uuid.UUID:
    """
    The company a board's roles are filed under.

    ``board.company_id`` when the board is linked to a company we hold; otherwise the
    board's own placeholder company, created on first use (named ``board.company_name``,
    else the board id). Never matched by name. The caller owns the transaction.
    """
    if board.company_id is not None:
        return board.company_id
    return await placeholder_company_id(
        session, ats=board.ats, board=board.board, name=board.company_name
    )


async def live_board_company_id(session: AsyncSession, ats: str, board: str) -> uuid.UUID:
    """
    The company a live search's role from ``ats:board`` is filed under: the same one the sweep
    files that board's roles under. The board's row decides it (its link, or its placeholder)
    when the board is on the roster; otherwise the board's placeholder, keyed by the board id.
    Never matched by name. ``persist_live_jobs`` calls this for board sources only.
    """
    row = (
        await session.execute(
            select(JobBoard)
            .where(JobBoard.ats == ats, JobBoard.board == board)
            .order_by(JobBoard.company_id.is_(None), JobBoard.region.asc().nulls_first())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return await placeholder_company_id(session, ats=ats, board=board, name=None)
    return await board_company_id(session, row)


def _host_of(url: str | None) -> str:
    try:
        return urlsplit(url or "").hostname or ""
    except ValueError:
        return ""


def _own_host(board: JobBoard) -> str | None:
    """
    The host this board's posting URLs live on, for the one ATS whose ``source`` does not
    say it: Personio's ``.de`` and ``.com`` boards of one name share ``personio:<name>``.
    ``None`` for every other ATS, whose ``source`` is already unique to the board (a
    posting may sit on the employer's own domain, so its host is no evidence there).
    """
    if board.ats != "personio":
        return None
    return f"{board.board.lower()}.jobs.personio.{board.region or 'de'}"


def _embedding_item(
    role: Job, row: JobRow, external_id: str, company_name: str, source: str
) -> dict[str, Any]:
    """What the vector store is given for a new row (the shape ``persist_live_jobs`` uses)."""
    named = role.model_copy(
        update={"extra_metadata": {**(role.extra_metadata or {}), "company_name": company_name}}
    )
    return {
        "job_id": external_id,
        "text": embedding_text(named),
        "metadata": {
            "title": role.title or "",
            "company": company_name,
            "location": role.location_raw or "",
            "country": role.country or "",
            "city": role.city or "",
            "is_remote": bool(role.is_remote),
            "seniority": role.seniority.value if role.seniority else "",
            "employment_type": role.employment_type.value if role.employment_type else "",
            "skills_str": ", ".join(role.skills or []),
            "source_url": role.source_url or "",
            "source": source,
        },
        "internal_job_id": row.id,
    }


def _posting_key(board: JobBoard, source_url: str | None, external_id: str | None) -> str | None:
    """
    The ATS's own id for a posting of this board, in one spelling, or ``None``.

    Read the way a re-check reads it (``domain.posting_urls.posting_ref``): from the URL, from a
    Greenhouse ``gh_jid`` on the employer's own site, else from an id that is not our URL hash.
    So a posting's id survives a change of host, of path prefix, of query string and of case,
    which its URL (and the MD5 of it) does not.
    """
    ref = posting_ref(board.source, source_url, external_id)
    if ref is None:
        return None
    return ref.external_id if ref.ats == "workable" else ref.external_id.lower()


async def _stored_by_posting(
    session: AsyncSession, board: JobBoard, keys: set[str], *, exclude: set[uuid.UUID]
) -> dict[str, JobRow]:
    """
    The stored rows of this board, open or closed, whose posting id is one of ``keys``.

    One row per id, an open one in preference to a closed one. A row in ``exclude`` (one the read
    already matched by its own URL) and a Personio row of the other region are never returned.
    """
    rows = (
        await session.execute(
            select(JobRow.id, JobRow.external_id, JobRow.source_url, JobRow.closed_at).where(
                JobRow.source == board.source
            )
        )
    ).all()
    own_host = _own_host(board)

    found: dict[str, uuid.UUID] = {}
    for job_id, external_id, source_url, _closed_at in sorted(
        rows, key=lambda row: row.closed_at is not None
    ):
        if job_id in exclude or external_id is None:
            continue
        if own_host is not None and _host_of(source_url) != own_host:
            continue
        key = _posting_key(board, source_url, external_id)
        if key is not None and key in keys and key not in found:
            found[key] = job_id
    if not found:
        return {}
    matched = (
        (await session.execute(select(JobRow).where(JobRow.id.in_(list(found.values())))))
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in matched}
    return {key: by_id[job_id] for key, job_id in found.items() if job_id in by_id}


async def _settle_complete_read(
    session: AsyncSession,
    board: JobBoard,
    listed_ids: set[str],
    listed_urls: set[str],
    listed_keys: set[str],
) -> int:
    """
    What a **complete** read says about the board's open rows: close the absent, confirm the listed.
    Returns how many were closed.

    A row is listed when its id, its URL *or* its posting id (``_posting_key``) is anywhere in
    the read: the URL is the evidence that matters, a row whose id was derived some other way
    still shows up by it, and a posting the board now links under another URL is still the
    posting. A listed row is confirmed open (``last_verified_at``, the same stamp an OPEN
    re-check leaves), so the re-check does not read again a board the sweep has just read whole.
    A row with no ``external_id`` cannot be matched to anything and is left alone, and so is a
    Personio row of the other region. ``closed_at`` keeps the date a role was first found gone
    (only ``closed_at IS NULL`` rows move).
    """
    rows = (
        await session.execute(
            select(JobRow.id, JobRow.external_id, JobRow.source_url).where(
                JobRow.source == board.source, JobRow.closed_at.is_(None)
            )
        )
    ).all()
    own_host = _own_host(board)

    gone: list[uuid.UUID] = []
    listed: list[uuid.UUID] = []
    for job_id, external_id, source_url in rows:
        if external_id is None:
            continue
        if own_host is not None and _host_of(source_url) != own_host:
            continue
        key = _posting_key(board, source_url, external_id)
        if (
            external_id in listed_ids
            or (source_url and source_url in listed_urls)
            or (key is not None and key in listed_keys)
        ):
            listed.append(job_id)
        else:
            gone.append(job_id)

    now = datetime.now(UTC)
    for start in range(0, len(listed), _CLOSE_CHUNK):
        await session.execute(
            update(JobRow)
            .where(JobRow.id.in_(listed[start : start + _CLOSE_CHUNK]))
            .values(last_verified_at=now)
        )
    for start in range(0, len(gone), _CLOSE_CHUNK):
        await session.execute(
            update(JobRow)
            .where(JobRow.id.in_(gone[start : start + _CLOSE_CHUNK]), JobRow.closed_at.is_(None))
            .values(closed_at=now)
        )
    return len(gone)


def _company_label(board: JobBoard, read: BoardRead, role: Job) -> str:
    """The company's name as the embedding's metadata states it."""
    return str(
        board.company_name
        or read.company_name
        or (role.extra_metadata or {}).get("company_name")
        or board.board
    )


async def write_board_roles(
    session: AsyncSession, board: JobBoard, read: BoardRead, *, allow_new: bool
) -> tuple[WriteSummary, list[dict[str, Any]]]:
    """
    Write a board's India roles into ``jobs``, and close what a complete read no longer lists.

    ``allow_new`` is the sweep's row budget: when false only roles already stored are
    refreshed, and each new one is counted in ``skipped_budget``. Returns the summary and the
    items to embed, for the rows inserted by this call and the ones that moved to a new URL.
    Does not commit.
    """
    summary = WriteSummary()
    embedding_items: list[dict[str, Any]] = []

    # 1. The roles to write: India only, a valid posting URL, one per URL, at most the cap.
    candidates: list[tuple[str, Job]] = []
    chosen: set[str] = set()
    for role in read.roles:
        if not is_india_role(role.location_raw):
            continue
        valid, _reason = validate_job_url(role.source_url or "")
        if not valid:
            summary.rejected_url += 1
            continue
        external_id = _stable_external_id(role)
        if external_id in chosen:
            continue
        chosen.add(external_id)
        candidates.append((external_id, role))
    candidates = candidates[:MAX_ROLES_PER_BOARD]

    # 2. Upsert on (external_id, source), the unique key: one query to find what is stored.
    stored = {
        row.external_id: row
        for row in await JobRepository(session).get_by_external_ids(
            [external_id for external_id, _role in candidates], board.source
        )
    }
    # A role not found by its URL's key may still be a row we hold under the URL it had before:
    # the board moved, or the employer links it on its own site. It is found by its posting id.
    wanted_keys = {
        key
        for external_id, role in candidates
        if external_id not in stored
        and (key := _posting_key(board, role.source_url, role.external_id)) is not None
    }
    moved_from: dict[str, JobRow] = {}
    if wanted_keys:
        moved_from = await _stored_by_posting(
            session, board, wanted_keys, exclude={row.id for row in stored.values()}
        )
    taken: set[uuid.UUID] = set()  # rows already claimed by a role that moved in this call

    company_id: uuid.UUID | None = None  # resolved on the first row that needs one
    written: list[tuple[JobRow, Job, datetime]] = []
    for external_id, role in candidates:
        kwargs = build_job_kwargs(role)
        row = stored.get(external_id)
        moved = False
        if row is None:
            key = _posting_key(board, role.source_url, role.external_id)
            earlier = moved_from.get(key) if key is not None else None
            if earlier is not None and earlier.id not in taken:
                row, moved = earlier, True
                taken.add(row.id)
        if row is not None:
            # A re-read restates what the board says, never what enrichment derived.
            for column, value in board_refresh_values(kwargs).items():
                setattr(row, column, value)
            if moved:
                # The key follows the URL, so live search (which keys by the URL) finds this row
                # and not a second one. It cannot collide: no row of this board holds the new key,
                # or the role would have been found above. The old vector no longer names a row.
                row.external_id = external_id
                row.embedding_id = None
                embedding_items.append(
                    _embedding_item(
                        role, row, external_id, _company_label(board, read, role), board.source
                    )
                )
            if board.company_id is not None:
                # A link made after the row was first stored (by live search, under a
                # placeholder or a name match) is the authority from then on.
                row.company_id = board.company_id
            summary.updated += 1
        elif allow_new:
            if company_id is None:
                company_id = await board_company_id(session, board)
            row = JobRow(
                id=uuid.uuid4(),
                external_id=external_id,
                source=board.source,
                company_id=company_id,
                **kwargs,
            )
            session.add(row)
            summary.inserted += 1
            embedding_items.append(
                _embedding_item(
                    role, row, external_id, _company_label(board, read, role), board.source
                )
            )
        else:
            summary.skipped_budget += 1
            continue
        written.append((row, role, kwargs["last_seen_at"]))
    await session.flush()

    # 3. Remember the role itself, not just this listing of it (it outlives retention).
    for row, role, seen_at in written:
        await record_sighting(
            session,
            company_id=row.company_id,
            title=role.title or "",
            external_id=row.external_id,
            seen_at=seen_at,
        )

    # 4. A complete read is the whole board. Presence is judged on every role it listed, India
    #    or not and cap or not, so a role that moved to "Remote" is not mistaken for one that left.
    if read.outcome == "ok" and read.complete:
        summary.closed = await _settle_complete_read(
            session,
            board,
            {_stable_external_id(role) for role in read.roles},
            {role.source_url for role in read.roles if role.source_url},
            {
                key
                for role in read.roles
                if (key := _posting_key(board, role.source_url, role.external_id)) is not None
            },
        )
    return summary, embedding_items
