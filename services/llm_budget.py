"""
services/llm_budget.py
~~~~~~~~~~~~~~~~~~~~~~
Daily caps on model-written drafts, kept in the shared cache.

Two features write a message with the model on request: the referral ask and
an outreach draft. They draw on one per-user allowance - "about 20 a day" is a
promise about drafting, whichever button produced the draft - so the counter
lives here and neither feature owns it.

This follows ``services/job_enrichment.py`` and
``services/company_map/summary.py``: increment first and compare after, so two
concurrent requests cannot both see the last unit as free, and fail open. The
budget is a cost guard on a free Groq tier, and losing the cache should not
turn drafting off. Callers that run out of budget fall back to a template; they
never fail.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from domain.outreach import ist_today
from services.cache_backend import CacheBackend

logger = logging.getLogger(__name__)

#: A counter outlives the day it counts, so a request just either side of
#: midnight (in any timezone) still finds its own key, and it is gone after two.
BUDGET_TTL_SECONDS = 48 * 3600

#: Model-written drafts one user can ask for in an IST day, referral asks
#: and outreach drafts together.
PER_USER_DAILY_DRAFTS = 20

#: Units of the shared outreach pool (``DAILY_OUTREACH_DRAFT_BUDGET``) one user may
#: spend in an IST day, so one busy user cannot drain it for everyone. A full daily
#: pack takes five; the rest is left for thread drafts and follow-ups.
PER_USER_SHARED_DRAFTS = 8


async def claim(key: str, limit: int, ttl_seconds: int = BUDGET_TTL_SECONDS) -> bool:
    """
    Claim one unit of the budget at ``key``.

    ``True`` when the unit was within ``limit``. A cache failure is ``True``:
    the budget limits spend, it does not gate the feature.
    """
    try:
        count = await CacheBackend.incr(key, ttl_seconds)
    except Exception as exc:
        logger.debug("Could not claim from budget %s: %s", key, exc)
        return True

    if count > limit:
        logger.info("Budget %s exhausted (limit %d)", key, limit)
        return False
    return True


async def used(key: str) -> int:
    """How many units the budget at ``key`` has handed out. ``0`` if it cannot be read."""
    try:
        return int(await CacheBackend.get(key) or 0)
    except Exception as exc:
        logger.debug("Could not read budget %s: %s", key, exc)
        return 0


def user_draft_key(user_id: str, now: datetime | None = None) -> str:
    """The counter for one user's drafts on one IST day."""
    day = ist_today(now or datetime.now(UTC))
    return f"tr:budget:drafts:user:{user_id}:{day.isoformat()}"


async def claim_user_draft(user_id: str) -> bool:
    """Claim one of today's model-written drafts for this user."""
    return await claim(user_draft_key(user_id), PER_USER_DAILY_DRAFTS)


def user_shared_key(user_id: str, now: datetime | None = None) -> str:
    """The counter for one user's share of the shared outreach pool on one IST day."""
    day = ist_today(now or datetime.now(UTC))
    return f"tr:budget:outreach-shared:{user_id}:{day.isoformat()}"


async def claim_user_shared(user_id: str) -> bool:
    """Claim one unit of this user's daily share of the shared outreach pool."""
    return await claim(user_shared_key(user_id), PER_USER_SHARED_DRAFTS)


async def user_drafts_left(user_id: str) -> int:
    """How many model-written drafts this user has left today."""
    return max(0, PER_USER_DAILY_DRAFTS - await used(user_draft_key(user_id)))
