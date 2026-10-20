"""
services/company_map/news_feeds.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The four startup-news RSS feeds and a parser for them.

:func:`parse_feed` is pure and defensive: it reads RSS 2.0 ``<item>`` and Atom
``<entry>`` with the standard library, refuses anything over
:data:`MAX_FEED_BYTES` and any document that declares a DOCTYPE or an entity
(the stdlib parser expands internal entities, so a "billion laughs" feed would
otherwise be a memory bomb), and keeps only items with a title and an absolute
``https`` link. Malformed XML is an empty list, never an exception.

:func:`fetch_feed` is the one network call; it streams and stops at the cap.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from services.company_map.types import FeedItem

FEEDS: dict[str, str] = {
    "inc42": "https://inc42.com/feed/",
    "yourstory": "https://yourstory.com/feed",
    "ettech": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
    "mint": "https://www.livemint.com/rss/companies",
}

MAX_FEED_BYTES = 2 * 1024 * 1024
SNIPPET_MAX = 600
TITLE_MAX = 400
URL_MAX = 1024
FETCH_TIMEOUT_S = 15.0
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"

_DECLARATION = re.compile(rb"<!\s*(?:doctype|entity)", re.I)
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")


def _local(tag: object) -> str:
    """An element's tag without its namespace (``{ns}item`` -> ``item``)."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(raw: str | None) -> str:
    """Markup to one line of plain text: tags dropped, entities decoded, spaces collapsed."""
    if not raw:
        return ""
    text = _TAG.sub(" ", _SCRIPT.sub(" ", raw))
    # Feeds often escape twice ("&amp;nbsp;"), so decode, strip, decode again.
    text = html.unescape(_TAG.sub(" ", html.unescape(text)))
    return _WS.sub(" ", text).strip()


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _https_url(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip()
    if len(url) > URL_MAX or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return url


def _published(raw: str | None) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom), always timezone-aware; naive means UTC."""
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    parsed: datetime | None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _child(entry: ET.Element, *names: str) -> ET.Element | None:
    for name in names:
        for child in entry:
            if _local(child.tag) == name:
                return child
    return None


def _child_text(entry: ET.Element, *names: str) -> str | None:
    child = _child(entry, *names)
    if child is None:
        return None
    return "".join(child.itertext())


def _link(entry: ET.Element) -> str | None:
    """RSS ``<link>text</link>``, else Atom ``<link rel="alternate" href>``, else a URL guid."""
    fallback: str | None = None
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href is None:
            url = _https_url(child.text)
            if url:
                return url
            continue
        if child.get("rel", "alternate") == "alternate":
            url = _https_url(href)
            if url:
                return url
        elif fallback is None:
            fallback = _https_url(href)
    if fallback:
        return fallback
    guid = _child(entry, "guid", "id")
    if guid is not None and guid.get("isPermaLink", "true").lower() != "false":
        return _https_url(guid.text)
    return None


def parse_feed(xml: bytes, source: str) -> list[FeedItem]:
    """Every usable item of an RSS 2.0 or Atom document; ``[]`` for anything unsafe or broken."""
    if not xml or len(xml) > MAX_FEED_BYTES:
        return []
    # Strip NULs so a UTF-16 document cannot hide its declarations from the check.
    if _DECLARATION.search(xml.replace(b"\x00", b"")):
        return []
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return []

    items: list[FeedItem] = []
    seen: set[str] = set()
    for entry in root.iter():
        if _local(entry.tag) not in ("item", "entry"):
            continue
        url = _link(entry)
        title = _bounded(_text(_child_text(entry, "title")), TITLE_MAX)
        if not url or not title or url in seen:
            continue
        seen.add(url)
        snippet = _text(_child_text(entry, "description", "summary", "content"))
        items.append(
            FeedItem(
                source=source,
                url=url,
                title=title,
                snippet=_bounded(snippet, SNIPPET_MAX) if snippet else None,
                published_at=_published(
                    _child_text(entry, "pubDate", "published", "updated", "date")
                ),
            )
        )
    return items


async def fetch_feed(client: httpx.AsyncClient, source: str) -> bytes:
    """The raw bytes of one feed in :data:`FEEDS`.

    Raises ``KeyError`` for an unknown source, ``httpx.HTTPError`` on a
    transport error or non-2xx status, and ``ValueError`` when the body passes
    :data:`MAX_FEED_BYTES` (the download stops there). The caller records the
    outcome against the source's health counter.
    """
    url = FEEDS[source]
    async with client.stream(
        "GET",
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_FEED_BYTES:
                raise ValueError(f"feed {source} is larger than {MAX_FEED_BYTES} bytes")
    return bytes(body)
