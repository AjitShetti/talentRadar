"""
domain/posting_urls.py
~~~~~~~~~~~~~~~~~~~~~~
Recognising a job-posting URL, and naming a role's public page.

The public "is this posting still open?" check accepts a URL from anyone on
the internet. That URL is never fetched. It is only *read*: if it is a posting
on an applicant-tracking system we know how to ask, the company and posting id
are lifted out of it - each matched against a strict pattern - and the caller
builds a request to that system's own API from those pieces. A URL that does
not match exactly is not a posting we can verify, and that is the whole answer.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048

_SLUG = r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# host -> (ats, pattern over the path). Hosts are matched exactly, so a
# look-alike such as boards.greenhouse.io.evil.example never reaches a pattern.
_PATTERNS: dict[str, tuple[str, re.Pattern[str]]] = {
    "boards.greenhouse.io": ("greenhouse", re.compile(rf"^/({_SLUG})/jobs/(\d{{1,20}})/?$")),
    "job-boards.greenhouse.io": ("greenhouse", re.compile(rf"^/({_SLUG})/jobs/(\d{{1,20}})/?$")),
    "jobs.lever.co": ("lever", re.compile(rf"^/({_SLUG})/({_UUID})(?:/apply)?/?$")),
    "jobs.ashbyhq.com": ("ashby", re.compile(rf"^/({_SLUG})/({_UUID})(?:/application)?/?$")),
}

# Stored rows: our own URL hash standing in for a posting id, and the query
# parameter Greenhouse uses when an employer hosts the posting on its own site.
_URL_HASH = re.compile(r"^[0-9a-f]{32}$")
_GH_JID = re.compile(r"(?:^|&)gh_jid=(\d{1,20})(?:&|$)")
_KNOWN_ATS = frozenset({"greenhouse", "lever", "ashby"})

_SLUG_TAIL = re.compile(rf"(?:^|-)({_UUID})$")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PostingRef:
    """A posting on a known ATS: enough to ask that ATS about it."""

    ats: str
    company_slug: str
    external_id: str

    @property
    def source(self) -> str:
        """The ``jobs.source`` value our own scraper writes for this board."""
        return f"{self.ats}:{self.company_slug}"


def parse_posting_url(url: str) -> PostingRef | None:
    """Read an ATS posting out of a URL, or ``None`` if it is not one."""
    text = (url or "").strip()
    if not text or len(text) > MAX_URL_LENGTH:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.port:
        return None
    known = _PATTERNS.get((parts.hostname or "").lower())
    if known is None:
        return None
    ats, pattern = known
    match = pattern.match(parts.path)
    if match is None:
        return None
    return PostingRef(ats=ats, company_slug=match.group(1).lower(), external_id=match.group(2).lower())


def posting_ref(
    source: str | None, source_url: str | None, external_id: str | None
) -> PostingRef | None:
    """
    The posting to ask an ATS about for a row we already hold, or ``None``.

    ``jobs.external_id`` cannot be trusted to be the ATS's own id: the
    live-scrape path stores an MD5 of the posting URL there. Asking the ATS
    for a posting by that hash is a 404, and a 404 means "closed" - so the id
    is read from the posting URL, which does carry it. A row that offers no
    real id cannot be asked about at all, and the caller must treat that as
    "unknown", never as "closed".
    """
    ats, _, slug = (source or "").partition(":")
    ats, slug = ats.strip().lower(), slug.strip()
    if ats not in _KNOWN_ATS or not slug:
        return None

    ref = parse_posting_url(source_url or "")
    if ref is not None:
        # A URL on some other ATS than the row claims proves nothing about it.
        return ref if ref.ats == ats else None

    if ats == "greenhouse" and source_url and len(source_url) <= MAX_URL_LENGTH:
        try:
            match = _GH_JID.search(urlsplit(source_url).query)
        except ValueError:
            match = None
        if match is not None:
            return PostingRef(ats=ats, company_slug=slug, external_id=match.group(1))

    stored = (external_id or "").strip()
    if not stored or _URL_HASH.match(stored):
        return None
    return PostingRef(ats=ats, company_slug=slug, external_id=stored)


def _slugify(text: str | None, limit: int) -> str:
    return _NON_SLUG.sub("-", (text or "").lower()).strip("-")[:limit].strip("-")


def company_slug(name: str | None) -> str:
    return _slugify(name, 60)


def role_slug(title: str | None, company: str | None, job_id: uuid.UUID) -> str:
    """A readable public path for a role. The id at the end is what resolves it."""
    words = "-".join(part for part in (_slugify(title, 70), _slugify(company, 40)) if part)
    return f"{words}-{job_id}" if words else str(job_id)


def slug_job_id(slug: str) -> uuid.UUID | None:
    """The job id a public slug ends in, or ``None``. The words are decoration."""
    match = _SLUG_TAIL.search(slug or "")
    if match is None:
        return None
    try:
        return uuid.UUID(match.group(1))
    except ValueError:
        return None
