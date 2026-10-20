"""
services/outreach/retention.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Prune the long tail of dead threads, so a 0.5 GB database never fills with them.

A thread goes when it is **closed or cold** and nothing has happened in it for
``THREAD_RETENTION_DAYS``: no touch, and no edit to the thread itself (closing
a thread is an edit, so a thread closed last week is never pruned for having an
old last message). Its people and touches go with it, by the database's
``ON DELETE CASCADE``. An open thread with a reply, or one still waiting on a
follow-up, is never pruned however old it is. A closed thread is pruned whether
or not anyone replied: closing it is the user saying the conversation is over.

The live set is tiny (about 1 KB a thread, capped per user), so this exists for
the long tail rather than for pressure. It deletes at most
``MAX_THREADS_PER_RUN`` threads, oldest first, so one pass cannot hold a
transaction open: the rest go on the next nightly pass.

The statement is built from SQLAlchemy expressions with every cutoff computed in
Python, so the same text runs on SQLite (the tests) and Postgres (production).
There is no ``make_interval`` here, unlike the raw SQL beside it in
``services/job_retention.py``. ``role_sightings`` is a different table and is
never touched; this module only deletes from ``outreach_threads``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Delete, and_, delete, exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from domain.outreach import COLD_AFTER_DAYS, MAX_FOLLOW_UPS, THREAD_RETENTION_DAYS, aware
from storage.models import OutreachPerson, OutreachThread, OutreachTouch

logger = logging.getLogger(__name__)

#: Ceiling on one pass.
MAX_THREADS_PER_RUN = 500


def prune_statement(now: datetime) -> Delete:
    """The ``DELETE`` that removes the threads past retention at ``now``.

    * **Idle**: the thread was last edited, and last touched, at least
      ``THREAD_RETENTION_DAYS`` ago. A thread with no touches at all is judged
      by its last edit alone.
    * **Closed**: ``closed_at`` is set.
    * **Cold**: not closed, nobody has replied, nobody is still waiting, and
      somebody is cold. This is ``domain.outreach.thread_stage`` over
      ``effective_state`` written as a query: a person is cold when they are
      ``waiting``, both follow-ups are spent and the last message is at least
      ``COLD_AFTER_DAYS`` old. A dropped or planned person has no say.
    """
    moment = aware(now)
    idle_before = moment - timedelta(days=THREAD_RETENTION_DAYS)
    cold_before = moment - timedelta(days=COLD_AFTER_DAYS)

    # The inner select is aliased: against the table being deleted from, SQLAlchemy would
    # correlate it away and leave the subquery with no FROM clause.
    thread = aliased(OutreachThread)

    recent_touch = exists().where(
        OutreachTouch.thread_id == thread.id, OutreachTouch.happened_at > idle_before
    )

    cold_person = and_(
        OutreachPerson.state == "waiting",
        OutreachPerson.follow_ups_sent >= MAX_FOLLOW_UPS,
        OutreachPerson.last_outbound_at.is_not(None),
        OutreachPerson.last_outbound_at <= cold_before,
    )
    has_cold_person = exists().where(OutreachPerson.thread_id == thread.id, cold_person)
    has_live_person = exists().where(
        OutreachPerson.thread_id == thread.id,
        or_(
            OutreachPerson.state == "replied",
            and_(OutreachPerson.state == "waiting", not_(cold_person)),
        ),
    )
    cold = and_(thread.closed_at.is_(None), has_cold_person, not_(has_live_person))

    doomed = (
        select(thread.id)
        .where(
            thread.updated_at <= idle_before,
            not_(recent_touch),
            or_(thread.closed_at.is_not(None), cold),
        )
        .order_by(thread.updated_at, thread.id)
        .limit(MAX_THREADS_PER_RUN)
    )
    return (
        delete(OutreachThread)
        .where(OutreachThread.id.in_(doomed))
        .execution_options(synchronize_session=False)
    )


async def prune_outreach(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete the threads past retention and commit; returns how many went.

    At most ``MAX_THREADS_PER_RUN`` a call. The people and touches of a deleted
    thread go with it. Raises on a database error and leaves the transaction open:
    the caller owns the rollback (``prune_stale_jobs`` gives this step its own,
    because a database that predates migration 017 has no such table and that must
    not stop the job prune around it).
    """
    moment = datetime.now(UTC) if now is None else aware(now)
    result = await session.execute(prune_statement(moment))
    await session.commit()
    deleted = int(cast("CursorResult[Any]", result).rowcount or 0)
    if deleted:
        logger.info(
            "Outreach retention: removed %d thread(s) idle for %d days",
            deleted,
            THREAD_RETENTION_DAYS,
        )
    return deleted
