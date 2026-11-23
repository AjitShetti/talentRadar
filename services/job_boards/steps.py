"""
services/job_boards/steps.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled work behind the job-board roster, one call per step.

``POST /api/v1/ingest/job-boards?step=…&limit=…`` (run by ``.github/workflows/job-boards.yml``;
there is no worker) and ``scripts/run_job_boards.py`` both call :func:`run_step`. A step:

* ``open_list`` - :func:`~services.job_boards.discover.discover_open_list`: seeds the roster
  from the open list of public ATS boards, **storesignalhq/ats-job-boards**
  (https://github.com/storesignalhq/ats-job-boards), published under CC BY 4.0 as stated by its
  publisher. Monthly.
* ``yc`` - :func:`~services.job_boards.discover.discover_yc`: reads YC's hiring list
  (``yc-oss``, a list of companies with their websites) and finds the boards of its India
  companies. Weekly.
* ``probe`` - :func:`~services.job_boards.discover.discover_probe`: guesses the board of a listed
  tech company that has none. Daily.
* ``sweep`` - :func:`~services.job_boards.sweep.sweep`: reads the due boards, paced per ATS, and
  writes their India roles. Every two hours.
* ``prune`` - :func:`prune_gone_boards`: deletes ``gone`` boards not read for
  :data:`GONE_RETENTION_DAYS`. Daily, after the probe.

:func:`run_step` never raises. A part that fails comes back as ``{"step": ..., "error": <class
name>}``: the class name only, because an exception's text can carry a URL or a token. The
detail goes to the log. ``limit`` is clamped to :data:`MAX_LIMIT` and to at least 1. An unknown
step is ``{"error": "unknown step"}``.

Each discovery step runs with one session and one HTTP client. The discovery functions commit
before their network work, so no transaction is open across a request. The sweep opens its own
short sessions.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from services.job_boards.discover import discover_open_list, discover_probe, discover_yc
from services.job_boards.sweep import sweep as run_sweep
from storage import database
from storage.models import JobBoard

logger = logging.getLogger(__name__)

STEPS = ("open_list", "yc", "probe", "sweep", "prune")

#: The most each step may take in one call. ``run_step`` clamps a larger ``limit`` to it.
MAX_LIMIT: dict[str, int] = {
    "open_list": 20000,
    "yc": 100,
    "probe": 60,
    "sweep": 600,
    "prune": 5000,
}

#: A ``gone`` board is deleted once it has not been read (or first seen, if never read) this long.
GONE_RETENTION_DAYS = 90

#: One request's timeout for the discovery steps. Their readers send the User-Agent themselves.
_REQUEST_TIMEOUT_SECONDS = 20.0


async def prune_gone_boards(session: AsyncSession, *, limit: int) -> int:
    """
    Delete at most ``limit`` ``gone`` boards that are :data:`GONE_RETENTION_DAYS` old and commit.

    A board's age is its last successful read, or when it was first seen if it was never read
    (``last_read_at`` is set only by an ok read). Boards of any other status are never touched,
    and the jobs they listed keep their own rows: a ``jobs`` row carries only the ``ats:board``
    text, not a key to the board. Returns how many boards were deleted.
    """
    cutoff = datetime.now(UTC) - timedelta(days=GONE_RETENTION_DAYS)
    last_seen = func.coalesce(JobBoard.last_read_at, JobBoard.first_seen_at)
    oldest = (
        select(JobBoard.id)
        .where(JobBoard.status == "gone", last_seen < cutoff)
        .order_by(last_seen)
        .limit(limit)
    )
    result = await session.execute(delete(JobBoard).where(JobBoard.id.in_(oldest)))
    await session.commit()
    return int(cast("CursorResult[Any]", result).rowcount or 0)


async def _discover(step: str, limit: int) -> Mapping[str, int]:
    """Run one discovery step with one session and one client for the whole step."""
    async with (
        database.AsyncSessionLocal() as session,
        httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client,
    ):
        if step == "open_list":
            return await discover_open_list(session, client, limit=limit)
        if step == "yc":
            return await discover_yc(session, client, limit=limit)
        return await discover_probe(session, client, limit=limit)


async def _prune(limit: int) -> Mapping[str, int]:
    async with database.AsyncSessionLocal() as session:
        return {"deleted": await prune_gone_boards(session, limit=limit)}


async def run_step(step: str, limit: int) -> dict[str, int | str]:
    """Run one scheduled step and return its counts. Never raises."""
    if step not in STEPS:
        return {"error": "unknown step"}
    try:
        bounded = max(1, min(int(limit), MAX_LIMIT[step]))
        counts: Mapping[str, int | str]
        if step == "sweep":
            counts = await run_sweep(limit=bounded)
        elif step == "prune":
            counts = await _prune(bounded)
        else:
            counts = await _discover(step, bounded)
    except Exception as exc:  # a scheduled step must never take the caller down
        logger.exception("job boards: step %s failed", step)
        return {"step": step, "error": type(exc).__name__}
    return {"step": step, **counts}
