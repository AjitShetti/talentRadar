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
* A role a complete read lists is confirmed open (``last_verified_at``, as an OPEN re-check
  records it). The re-check never reads a whole board of six of the ATSes on a request or on its
  own schedule: this read is how those rows are confirmed, and how they close.

The caller owns the transaction: nothing here commits, and nothing here makes a request.
Rows are written structurally (``enrichment_status='raw'``, no LLM call), and the items
to embed are returned for new rows only, for the caller to embed once it has committed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities import Job
from domain.job_boards import is_india_role
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


async def placeholder_company_id(
    session: AsyncSession, *, ats: str, board: str, name: str | None
) -> uuid.UUID:
    """
    The placeholder company of one board, keyed by the board and created on first use, named
    ``name`` or else the board id. Never matched by name. The caller owns the transaction.
    """
    domain = _placeholder_domain_for(ats, board)
    found = (
        await session.execute(select(Company.id).where(Company.domain == domain))
    ).scalar_one_or_none()
    if found is not None:
        return found

    label = (name or "").strip() or board
    company = Company(id=uuid.uuid4(), name=label[:_COMPANY_NAME_MAX], domain=domain)
    session.add(company)
    await session.flush()
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


async def _settle_complete_read(
    session: AsyncSession, board: JobBoard, listed_ids: set[str], listed_urls: set[str]
) -> int:
    """
    What a **complete** read says about the board's open rows: close the absent, confirm the listed.
    Returns how many were closed.

    A row is listed when its id *or* its URL is anywhere in the read: the URL is the
    evidence that matters, and a row whose id was derived some other way still shows up by it.
    A listed row is confirmed open (``last_verified_at``, the same stamp an OPEN re-check
    leaves), so the re-check does not read again a board the sweep has just read whole.
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
        if external_id in listed_ids or (source_url and source_url in listed_urls):
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


async def write_board_roles(
    session: AsyncSession, board: JobBoard, read: BoardRead, *, allow_new: bool
) -> tuple[WriteSummary, list[dict[str, Any]]]:
    """
    Write a board's India roles into ``jobs``, and close what a complete read no longer lists.

    ``allow_new`` is the sweep's row budget: when false only roles already stored are
    refreshed, and each new one is counted in ``skipped_budget``. Returns the summary and the
    items to embed, for the rows inserted by this call. Does not commit.
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
    company_id: uuid.UUID | None = None  # resolved on the first row that needs one
    written: list[tuple[JobRow, Job, datetime]] = []
    for external_id, role in candidates:
        kwargs = build_job_kwargs(role)
        row = stored.get(external_id)
        if row is not None:
            # A re-read restates what the board says, never what enrichment derived.
            for column, value in board_refresh_values(kwargs).items():
                setattr(row, column, value)
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
            name = (
                board.company_name
                or read.company_name
                or (role.extra_metadata or {}).get("company_name")
                or board.board
            )
            embedding_items.append(_embedding_item(role, row, external_id, name, board.source))
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
        )
    return summary, embedding_items
