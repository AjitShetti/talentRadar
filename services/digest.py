"""
services/digest.py
~~~~~~~~~~~~~~~~~~
The morning email: the day plan, mailed to the users who turned it on.

It is the Overview's briefing (``services/copilot.build_briefing``) as plain text, so it
says nothing the Overview would not, and it needs no model call. The in-process scheduler
(``api/main.py``) runs ``send_daily_digests`` once a day at ``digest_hour:digest_minute``.

* Opt-in only: ``profiles.digest_email`` is false until the user turns it on in Settings.
  Every mail ends with a one-click link that turns it off without signing in.
* Nothing to say, nothing sent: a briefing with no cards sends no mail.
* Once per user and IST day: a cache counter claims the send first, so a missed run that
  fires on wake, or a second process, cannot mail anyone twice.
* At most ``DAILY_CAP`` a run, under Resend's free 100 a day.
* Never raises: it runs unattended inside the API process. Logs carry counts only.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt
from sqlalchemy import select, update

from config.settings import get_settings
from domain.event_time import ist_date
from services.cache_backend import CacheBackend
from services.email import email_enabled, send_email, site_link
from storage.database import AsyncSessionLocal
from storage.models import Profile, User

logger = logging.getLogger(__name__)

#: Mails a run sends at most. Resend's free tier allows 100 a day; the reset links need some.
DAILY_CAP = 80
#: Cards one mail lists. The Overview shows three; a mail can carry a few more.
MAX_CARDS = 5
_CLAIM_TTL_SECONDS = 2 * 24 * 3600


def _now() -> datetime:
    return datetime.now(UTC)


def _unsubscribe_key() -> str:
    """Signs the link that turns the email off: derived from, never equal to, the session key."""
    secret = get_settings().jwt_secret_key.encode()
    return hmac.new(secret, b"digest-unsubscribe", hashlib.sha256).hexdigest()


def unsubscribe_token(user_id: str) -> str:
    """The token in a mail's "turn this off" link. It does nothing but turn the email off."""
    return jwt.encode({"sub": user_id, "use": "digest-off"}, _unsubscribe_key(), algorithm="HS256")


def _token_user(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(token, _unsubscribe_key(), algorithms=["HS256"])
        if payload.get("use") != "digest-off":
            return None
        return uuid.UUID(str(payload.get("sub")))
    except (jwt.PyJWTError, ValueError):
        return None


async def turn_off_digest(token: str) -> bool:
    """Turn the morning email off for the user a link names. ``False`` for an invalid link."""
    user_id = _token_user(token)
    if user_id is None:
        return False
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Profile).where(Profile.user_id == user_id).values(digest_email=False)
        )
        await session.commit()
    return True


def _link(href: str) -> str | None:
    """A card's own page as an absolute link; ``None`` for anything that is not a site path."""
    if href.startswith("/") and not href.startswith("//"):
        return site_link(href)
    return None


def compose(briefing: dict[str, Any], *, name: str | None, user_id: str) -> tuple[str, str] | None:
    """``(subject, text)`` for one user's mail, or ``None`` when the briefing has nothing to say."""
    cards = list(briefing.get("cards") or [])[:MAX_CARDS]
    if not cards:
        return None
    lines = [f"Good morning{', ' + name.split()[0] if name and name.strip() else ''}.", ""]
    headline = str(briefing.get("headline") or "").strip()
    if headline:
        lines += [headline, ""]
    for card in cards:
        lines.append(f"- {card['title']}")
        if card.get("detail"):
            lines.append(f"  {card['detail']}")
        actions = card.get("actions") or []
        link = _link(str(actions[0].get("href") or "")) if actions else None
        if link:
            lines.append(f"  {link}")
        lines.append("")
    lines += [
        f"Your whole day plan: {site_link('/dashboard')}",
        "",
        "You get this because you turned on the morning email in TalentRadar's Settings.",
        f"Turn it off: {site_link('/unsubscribe?token=' + unsubscribe_token(user_id))}",
    ]
    return "Your TalentRadar day plan", "\n".join(lines)


async def _claim(user_id: str, day: str) -> bool:
    """First claim of today's mail for this user wins; every later one is refused."""
    return await CacheBackend.incr(f"tr:digest:{user_id}:{day}", _CLAIM_TTL_SECONDS) == 1


async def _recipients() -> list[tuple[str, str, str | None]]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(User.id, User.email, Profile.full_name)
            .join(Profile, Profile.user_id == User.id)
            .where(Profile.digest_email.is_(True))
            .order_by(User.id)
        )
        return [(str(user_id), email, full_name) for user_id, email, full_name in rows.all()]


async def send_daily_digests() -> dict[str, int]:
    """Mail today's plan to every user who asked for it. Returns counts; never raises."""
    counts = {"recipients": 0, "sent": 0, "empty": 0, "skipped": 0, "failed": 0}
    if not email_enabled():
        return counts
    try:
        from services.copilot import build_briefing

        recipients = await _recipients()
        counts["recipients"] = len(recipients)
        day = ist_date(_now()).isoformat()
        for user_id, email, name in recipients:
            if counts["sent"] >= DAILY_CAP:
                counts["skipped"] += 1
                continue
            try:
                message = compose(await build_briefing(user_id=user_id), name=name, user_id=user_id)
                if message is None:
                    counts["empty"] += 1
                    continue
                if not await _claim(user_id, day):
                    counts["skipped"] += 1
                    continue
                subject, text = message
                if await send_email(to=email, subject=subject, text=text):
                    counts["sent"] += 1
                else:
                    counts["failed"] += 1
            except Exception as exc:
                logger.warning("Digest failed for one user: %s", type(exc).__name__)
                counts["failed"] += 1
    except Exception as exc:
        logger.warning("Digest run failed: %s", type(exc).__name__)
    logger.info("Digest run: %s", counts)
    return counts
