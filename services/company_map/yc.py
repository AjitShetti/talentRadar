"""
services/company_map/yc.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Bengaluru companies from Y Combinator's public directory, through the
community mirror ``yc-oss`` (one static JSON file, refreshed daily from YC's
own listing; no key and no rate limit).

What is kept: companies with a Bengaluru location (``all_locations`` lists
every office, ``"Bengaluru, KA, India; Remote"``) that YC does not mark
``Inactive`` or ``Dead``, and that state a website of their own, since a
company is keyed on its domain.

What it claims, and how firmly. The directory is curated by YC but written by
the founders, so ``yc`` outranks OpenStreetMap and yields to Wikidata
(``domain.company_identity.SOURCE_PRECEDENCE``). The one-liner is the
source's description and goes to ``metadata.yc_description`` like Wikidata's
does, never to the ``description`` column.

Stage: ``seed`` for an ``Early`` company. YC files the ones it considers
past that as ``Growth``; calling Razorpay "Seed" would be wrong, so no stage
is asserted and funding news fills it in. A listed or acquired company says
so.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from domain.company_taxonomy import sector_from_text, tier_for_import
from domain.geo import mentions_city
from services.company_map.sourced import SourcedCompany, clean_website

URL = "https://yc-oss.github.io/api/companies/all.json"
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
_TIMEOUT_S = 60.0

_DEAD = frozenset({"inactive", "dead"})
_BATCH_YEAR = re.compile(r"(\d{4})\s*$")
_ONE_LINER_MAX = 500  # the store keeps 500 characters of a source description


async def fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The whole directory (about 10 MB, 6,000 companies). Raises on failure."""
    response = await client.get(
        URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=_TIMEOUT_S,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("YC directory mirror returned a body that is not a list")
    return [entry for entry in payload if isinstance(entry, dict)]


def _text(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    return value.strip() or None if isinstance(value, str) else None


def _words(entry: dict[str, Any], key: str) -> list[str]:
    value = entry.get(key)
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _in_bengaluru(entry: dict[str, Any]) -> bool:
    return mentions_city(_text(entry, "all_locations"), "Bengaluru")


def _is_alive(entry: dict[str, Any]) -> bool:
    return (_text(entry, "status") or "").lower() not in _DEAD


def _stage(status: str | None, yc_stage: str | None) -> str | None:
    """See the module docstring."""
    state = (status or "").lower()
    if state == "public":
        return "public"
    if state == "acquired":
        return "acquired"
    return "seed" if (yc_stage or "").lower() == "early" else None


def _employee_count(entry: dict[str, Any]) -> int | None:
    size = entry.get("team_size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    return size


def _tier(batch: str | None, status: str | None) -> str | None:
    """A company that went through YC since 2008 and is not listed is a startup."""
    match = _BATCH_YEAR.search(batch or "")
    return tier_for_import(
        is_vc=False,
        founded_year=int(match.group(1)) if match else None,
        is_public=(status or "").lower() == "public",
    )


def _company(entry: dict[str, Any]) -> SourcedCompany | None:
    """The company ``entry`` describes, or ``None`` when it has no usable name or site."""
    name = _text(entry, "name")
    site = clean_website(entry.get("website"))
    if name is None or site is None:
        return None
    website_url, domain = site
    batch = _text(entry, "batch")
    status = _text(entry, "status")
    description = _text(entry, "one_liner") or _text(entry, "long_description")
    # is_tech: True (source is tech by nature)
    return SourcedCompany(
        name=name,
        domain=domain,
        website_url=website_url,
        employee_count=_employee_count(entry),
        description=description[:_ONE_LINER_MAX] if description else None,
        sector=sector_from_text("; ".join([*_words(entry, "industries"), *_words(entry, "tags")])),
        tier=_tier(batch, status),
        stage=_stage(status, _text(entry, "stage")),
        source_metadata={"yc_batch": batch} if batch else {},
    )


def scan(payload: Any) -> tuple[list[SourcedCompany], int]:
    """
    ``(companies, skipped)`` for the directory.

    ``skipped`` counts the live Bengaluru entries that could not become a row
    (no website, or a website that is somebody else's page). Companies in
    other cities and ``Inactive`` ones are filtered, not skipped.
    """
    if not isinstance(payload, list):
        return [], 0
    companies: list[SourcedCompany] = []
    skipped = 0
    for entry in payload:
        if not isinstance(entry, dict) or not _is_alive(entry) or not _in_bengaluru(entry):
            continue
        company = _company(entry)
        if company is None:
            skipped += 1
        else:
            companies.append(company)
    return companies, skipped


def parse_yc(payload: Any) -> list[SourcedCompany]:
    """One :class:`SourcedCompany` per live Bengaluru YC company, in directory order."""
    return scan(payload)[0]
