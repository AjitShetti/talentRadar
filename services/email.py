"""
services/email.py
~~~~~~~~~~~~~~~~~
The one way the server sends mail to a user: Resend's HTTP API.

Two things send mail: the password-reset link, and the daily digest a user opted into.
Nothing else does, and nothing is ever sent on a user's behalf to anyone else.

Without ``RESEND_API_KEY`` mail is off, which is an ordinary state and never an error:
``email_enabled()`` is ``False`` and ``send_email`` sends nothing. A failed send is logged
with the status only (never the address or the body) and returns ``False``; it never raises.
"""

from __future__ import annotations

import logging

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0


def email_enabled() -> bool:
    """Mail can be sent: a Resend key is configured."""
    return bool(get_settings().resend_api_key)


def site_link(path: str) -> str:
    """An absolute link to a page of the web app, for a mail body."""
    return get_settings().site_url.rstrip("/") + path


async def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one message. ``True`` when Resend accepted it; ``False`` when mail is off or it failed."""
    settings = get_settings()
    if not settings.resend_api_key:
        return False
    payload: dict[str, object] = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html is not None:
        payload["html"] = html
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                RESEND_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Mail not sent: %s", type(exc).__name__)
        return False
    if response.status_code >= 300:
        logger.warning("Mail not sent: Resend answered %s", response.status_code)
        return False
    return True
