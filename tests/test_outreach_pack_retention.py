"""Old daily packs are pruned after 30 IST days; their items go with them."""

# A fixture imported by name is rebound as every test's parameter, which F811 reads
# as a redefinition.
# ruff: noqa: F811

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.outreach.retention import PACK_RETENTION_DAYS, prune_packs
from storage.models import OutreachPack, OutreachPackItem
from tests.outreach_support import make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]

# 11:30 IST on 15 October 2026.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


async def test_prune_packs_drops_older_than_30_days_keeps_recent(outreach_db: Factory) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        for day in (date(2026, 9, 14), date(2026, 9, 15), date(2026, 10, 15)):
            session.add(
                OutreachPack(
                    user_id=user.id,
                    day=day,
                    items=[
                        OutreachPackItem(
                            company_name="Fictional Labs",
                            channel="linkedin",
                            format="linkedin_note",
                        )
                    ],
                )
            )
        await session.commit()

        assert PACK_RETENTION_DAYS == 30
        assert await prune_packs(session, NOW) == 1

        days = (
            await session.execute(select(OutreachPack.day).order_by(OutreachPack.day))
        ).scalars()
        assert list(days) == [date(2026, 9, 15), date(2026, 10, 15)]
        items = (await session.execute(select(func.count()).select_from(OutreachPackItem))).scalar()
        assert items == 2
