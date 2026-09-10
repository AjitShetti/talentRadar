"""
ingestion/scrapers/_parsing.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared HTML parsing for the scrapers.

Parsing is synchronous, CPU-bound and - on a board that ships a 1.5 MB search
page - slow enough to matter. Two things follow, and both were wrong before
this module existed:

**It must not run on the event loop.** ``asyncio.wait_for`` cannot interrupt
synchronous work, so a scraper whose parse blocks for a second overruns its
own timeout and stalls every other scraper in the fan-out with it. Freshersworld
was measured returning at 4984 ms against a 4000 ms budget for exactly this
reason. On the deployed single-worker instance the same block also stalls
request handling for every other user. ``parse_html`` hands the work to a
thread so the timeout can actually fire.

**It should use lxml.** BeautifulSoup's default ``html.parser`` is pure
Python; lxml is a C parser and several times faster on documents this size.
lxml is already a core dependency, so this costs nothing - but it is only
*preferred*, not required: if the binary is unavailable the helper falls back
to ``html.parser`` rather than failing the scrape.
"""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup

# Resolved once. Selecting a missing parser raises from BeautifulSoup's
# constructor, and finding that out on every call inside a thread would hide
# the cause behind a scraper-level "returned nothing".
try:
    BeautifulSoup("<b>probe</b>", "lxml")
    _PARSER = "lxml"
except Exception:  # pragma: no cover - only when lxml is not installed
    _PARSER = "html.parser"


def parse_html_sync(html: str) -> BeautifulSoup:
    """Parse *html* on the calling thread. Prefer :func:`parse_html`."""
    return BeautifulSoup(html, _PARSER)


async def parse_html(html: str) -> BeautifulSoup:
    """Parse *html* off the event loop, so concurrent scrapers keep running."""
    return await asyncio.to_thread(parse_html_sync, html)
