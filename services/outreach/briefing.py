"""
services/outreach/briefing.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Outreach cards on the Overview day plan (spec section 10, and the daily pack's
spec of 2026-10-26).

* ``outreach_followup`` - one card for everything owed today. It is a date the
  user set themselves, so it cannot be dismissed: it clears when they act on
  /outreach or snooze the person there.
* ``outreach_pack`` - today's pack: how many companies are left to review and how
  many messages are ready. Counts only, and only from a pack that already exists:
  the briefing never builds one. While it shows, the suggestion card does not.
* ``outreach_suggestion`` - the single best company to write to, only while the
  user is under their daily target and there is no pack card.

Both come from rows that already exist (the due list, the day's count and the
cached ranking), so no model is called and nothing is fetched. ``outreach_cards``
never raises: a broken Outreach query costs the briefing its two cards, not the
page. Logs carry the failure's class and nothing the user typed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from domain.outreach import IST, MAX_FOLLOW_UPS, DueItem, aware, ist_today
from services.copilot import _card
from services.outreach.pack import pack_counts
from services.outreach.suggest import suggestion_card_id, suggestions
from services.outreach.touches import due_items, get_target, reached_today

logger = logging.getLogger(__name__)

#: The follow-up card's id. One card, so the id carries no thread.
FOLLOWUP_CARD_ID = "outreach_followup"


def _day(moment: datetime) -> str:
    """A calendar day the way the briefing writes it, in India: ``10 Oct``."""
    local = aware(moment).astimezone(IST)
    return f"{local.day} {local.strftime('%b')}"


def _others(count: int) -> str:
    return f" and {count} other" if count == 1 else f" and {count} others"


def _followup_card(items: list[DueItem], now: datetime) -> dict[str, Any] | None:
    """One card for everything owed. A next step the user set leads, then the oldest
    follow-up; the card opens the thread it names."""
    if not items:
        return None
    lead = next((item for item in items if item.kind == "next_step"), items[0])
    others = len(items) - 1

    if lead.kind == "next_step":
        title = f"Next step with {lead.company_name}"
        if lead.next_step:
            title = f"{title}: {lead.next_step}"
        when = "today" if ist_today(lead.due_at) == ist_today(now) else _day(lead.due_at)
        detail = f"You set this for {when}."
        if others:
            detail = f"{detail} {others} more due on Outreach."
    else:
        person = lead.person_name or "your contact"
        title = f"Follow up with {person} at {lead.company_name}"
        if others:
            title = f"{title}{_others(others)}"
        wrote = (
            f"You wrote on {_day(lead.last_outbound_at)}"
            if lead.last_outbound_at is not None
            else "You wrote"
        )
        detail = (
            f"{wrote} and haven't heard back. "
            f"This is follow-up {lead.follow_up_number or 1} of {MAX_FOLLOW_UPS} "
            "— one short note, then let it rest."
        )

    return _card(
        card_id=FOLLOWUP_CARD_ID,
        kind="outreach_followup",
        title=title,
        detail=detail,
        tone="primary",
        actions=[
            {
                "label": "Open Outreach",
                "href": f"/outreach?thread={lead.thread_id}",
                "style": "primary",
            }
        ],
        meta={"count": len(items), "thread_id": lead.thread_id},
        dismissible=False,
    )


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


async def _pack_card(user_id: str, now: datetime) -> dict[str, Any] | None:
    """Today's pack, while it still has a company to review."""
    counts = await pack_counts(user_id, now=now)
    if counts is None or counts[0] == 0:
        return None
    companies, ready = counts
    return _card(
        card_id=f"outreach_pack:{ist_today(now).isoformat()}",
        kind="outreach_pack",
        title=f"Today's pack: {_plural(companies, 'company', 'companies')}",
        detail=f"{_plural(ready, 'message', 'messages')} ready",
        tone="action",
        actions=[{"label": "Review", "href": "/outreach#today", "style": "primary"}],
        meta={"companies": companies, "messages_ready": ready},
    )


async def _suggestion_card(user_id: str, now: datetime) -> dict[str, Any] | None:
    """The top suggestion, while ``reached_today`` is under a target above zero.

    The ranking is not asked for when the card cannot show: a user at their target,
    or with the target switched off, costs the briefing no ranking query.
    """
    target = await get_target(user_id)
    if target <= 0:
        return None
    reached = await reached_today(user_id, now=now)
    if reached >= target:
        return None
    ranked = (await suggestions(user_id, limit=1, now=now))["suggestions"]
    if not ranked:
        return None

    top = ranked[0]
    company_id = str(top["company_id"])
    reasons = [str(reason) for reason in top.get("reasons", [])]
    tally = f"Today: {reached} of {target} reached."
    return _card(
        card_id=suggestion_card_id(company_id),
        kind="outreach_suggestion",
        title=f"Reach out to {top['name']}",
        detail=f"{' · '.join(reasons)}. {tally}" if reasons else tally,
        tone="action",
        actions=[
            {
                "label": "See who to contact",
                "href": f"/outreach?company={company_id}",
                "style": "primary",
            }
        ],
        meta={
            "company_id": company_id,
            "reached_today": reached,
            "daily_target": target,
            "reasons": reasons,
        },
    )


async def outreach_cards(
    user_id: str, *, now: datetime | None = None
) -> dict[str, dict[str, Any] | None]:
    """The follow-up, pack and suggestion cards, each ``None`` when it has nothing to say.

    ``{"followup", "suggestion", "pack"}``. The suggestion is not ranked while a pack
    card shows. Never raises: any failure yields ``None`` for all three, since one
    broken read means none of them can be trusted. ``now`` is for tests.
    """
    moment = datetime.now(UTC) if now is None else aware(now)
    try:
        followup = _followup_card(await due_items(user_id, now=moment), moment)
        pack = await _pack_card(user_id, moment)
        suggestion = None if pack is not None else await _suggestion_card(user_id, moment)
        return {"followup": followup, "suggestion": suggestion, "pack": pack}
    except Exception as exc:
        logger.warning("outreach briefing cards unavailable: %s", type(exc).__name__)
        return {"followup": None, "suggestion": None, "pack": None}
