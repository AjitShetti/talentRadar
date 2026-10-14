"""
domain/company_identity.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
When two records are the same employer, and whose value wins when the
sources that describe it disagree.

* **Identity** is the registrable domain (``razorpay.com``), enforced by
  ``uq_companies_domain``. A profile on someone else's site
  (``linkedin.com/company/acme``) is not a domain: keying on it would fold
  every company whose only link is its LinkedIn page into one row.
* **Names** only ever attach data to an existing row; they never merge two.
  :func:`normalize_name` is the comparison form.
* **Precedence**: curated > company site > Wikidata > OSM > news > LLM. A
  lower source never overwrites a higher one, a source refreshes its own
  value, and ``None`` never clears anything. ``stage`` and
  ``last_funding_at`` are the exception: they follow the newest *dated*
  funding evidence, so this month's Series B beats a curated "Series A".

Everything here is pure.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

SOURCE_PRECEDENCE: tuple[str, ...] = ("curated", "site", "wikidata", "osm", "news", "llm")

_RANK = {source: rank for rank, source in enumerate(SOURCE_PRECEDENCE)}

#: Public suffixes with two labels that Indian and nearby companies use. The
#: ``.in`` zones matter most: without them every bank on ``bank.in`` and every
#: college on ``ac.in`` collapses into one company.
_TWO_LABEL_SUFFIXES = frozenset(
    {
        "co.in",
        "org.in",
        "net.in",
        "firm.in",
        "gen.in",
        "ind.in",
        "bank.in",
        "ac.in",
        "edu.in",
        "res.in",
        "gov.in",
        "nic.in",
        "mil.in",
        "co.uk",
        "com.au",
    }
)

#: Hosting platforms where the subdomain is the tenant: ``acme.github.io``
#: is Acme's, not GitHub's.
_TENANT_SUFFIXES = frozenset(
    {
        "github.io",
        "gitlab.io",
        "vercel.app",
        "netlify.app",
        "herokuapp.com",
        "pages.dev",
        "webflow.io",
        "notion.site",
        "substack.com",
        "wixsite.com",
        "blogspot.com",
    }
)

#: Sites whose pages name somebody else. A link there is a profile, never
#: the company's own domain.
_PROFILE_DOMAINS = frozenset(
    {
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "medium.com",
        "github.com",
        "gitlab.com",
        "crunchbase.com",
        "wellfound.com",
        "angel.co",
        "linktr.ee",
        "t.me",
        "notion.so",
        "wa.me",
    }
)
_PROFILE_HOSTS = frozenset({"sites.google.com", "docs.google.com", "play.google.com"})

_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]+)$")

_LEGAL_SUFFIX = re.compile(r"(?:\s+(?:pvt|private|ltd|limited|llp|inc|corp))+$")

_SLUG_MAX = 96

#: Fields decided by the date of the funding evidence, not by its source.
_FUNDING_FIELDS = ("stage", "last_funding_at")


def normalize_domain(value: str | None) -> str | None:
    """The registrable domain a URL or host names, or ``None``."""
    if not value or not value.strip():
        return None
    text = value.strip().lower()
    if "://" not in text:
        text = f"http://{text}"
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".")
    if not _HOST.match(host) or host in _PROFILE_HOSTS:
        return None

    labels = host.split(".")
    suffix = ".".join(labels[-2:])
    if suffix in _TENANT_SUFFIXES or suffix in _TWO_LABEL_SUFFIXES:
        if len(labels) < 3:
            return None
        domain = ".".join(labels[-3:])
    else:
        domain = suffix
    return None if domain in _PROFILE_DOMAINS else domain


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_name(name: str) -> str:
    """Lowercase, punctuation as spaces, legal suffixes (``Pvt. Ltd.``) dropped."""
    words = re.sub(r"[\W_]+", " ", _fold(name).lower()).strip()
    return _LEGAL_SUFFIX.sub("", words)


def slugify(name: str) -> str:
    """An ASCII URL slug of at most 96 characters; ``company`` if nothing is left."""
    ascii_name = _fold(name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    slug = slug[:_SLUG_MAX].rstrip("-")
    return slug or "company"


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | dict | set):
        return not value
    return False


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _merge_funding(
    current: dict[str, Any],
    incoming: dict[str, Any],
    source: str,
    updates: dict[str, Any],
    provenance: dict[str, str],
) -> None:
    """Dated funding evidence moves the stage only when it is the newest."""
    dated = incoming.get("last_funding_at")
    if not isinstance(dated, datetime):
        return
    known = current.get("last_funding_at")
    if isinstance(known, datetime) and _utc(dated) <= _utc(known):
        return
    for field in _FUNDING_FIELDS:
        value = incoming.get(field)
        if not _is_empty(value) and value != current.get(field):
            updates[field] = value
            provenance[field] = source


def merge_fields(
    current: dict[str, Any],
    provenance: dict[str, str],
    incoming: dict[str, Any],
    source: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    The fields ``source`` may write over ``current``, and the provenance after.

    A filled field with no recorded provenance counts as curated: every row
    seeded before provenance existed came from the catalogue.
    """
    if source not in _RANK:
        raise ValueError(f"unknown source {source!r}")
    rank = _RANK[source]
    updates: dict[str, Any] = {}
    new_provenance = dict(provenance)

    if isinstance(incoming.get("last_funding_at"), datetime):
        _merge_funding(current, incoming, source, updates, new_provenance)
        skip = set(_FUNDING_FIELDS)
    elif isinstance(current.get("last_funding_at"), datetime):
        # An undated stage never undoes a dated one: re-seeding the catalogue
        # must not put back the "Series A" that this month's news replaced.
        skip = set(_FUNDING_FIELDS)
    else:
        skip = set()

    for field, value in incoming.items():
        if field in skip or _is_empty(value):
            continue
        existing = current.get(field)
        if _is_empty(existing):
            updates[field] = value
            new_provenance[field] = source
            continue
        holder = _RANK.get(new_provenance.get(field, "curated"), 0)
        if rank > holder:
            continue
        if value != existing:
            updates[field] = value
        new_provenance[field] = source
    return updates, new_provenance
