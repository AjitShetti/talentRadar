"""
services/company_map/refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled work behind the company map, one bounded step per call.

``POST /api/v1/ingest/company-map/refresh?step=…`` (run by
``.github/workflows/company-map.yml``) and ``scripts/refresh_company_map.py``
both call :func:`run_step`. A step:

* ``wikidata`` / ``osm`` — import Bengaluru companies and merge them in.
* ``sites`` — read up to ``limit`` company homepages (robots.txt respected,
  each site at most once per 30 days): address, PIN, people, and a summary
  when the site or Wikidata already says what the company does.
* ``geocode`` — place up to ``limit`` companies through Nominatim (1 req/s),
  falling back to the centroid of a known area.
* ``summaries`` — the budgeted LLM line for companies whose sources had text
  but no usable sentence. A company with no source text gets none.
* ``github`` — public org members as engineers.

:func:`run_step` never raises: a failure comes back as ``{"error": …}`` and
the rows keep their last good data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from domain.areas import area_for_text, jitter
from domain.company_identity import merge_fields
from services.company_map import osm, wikidata
from services.company_map.geocode import geocode, resolve_location
from services.company_map.people import github_members, upsert_people
from services.company_map.store import apply_location, due, mark_tried, upsert_company_ex
from services.company_map.summary import choose_summary, has_source_text, llm_summary
from services.company_map.types import GeoHit, ImportedCompany, SiteFacts
from services.company_map.website import read_site
from storage.database import AsyncSessionLocal
from storage.models import Company

logger = logging.getLogger(__name__)

STEPS = ("wikidata", "osm", "sites", "geocode", "summaries", "github")

#: Hard ceilings whatever the caller asks for: Nominatim's policy, the daily
#: summary budget, and what one request can finish.
MAX_LIMIT = {
    "wikidata": 5000,
    "osm": 5000,
    "sites": 100,
    "geocode": 40,
    "summaries": 60,
    "github": 50,
}

SITE_CONCURRENCY = 4
_SITE_TEXT_KEEP = 1500
_COMMIT_EVERY = 50


def _metadata(company: Company) -> dict[str, Any]:
    return dict(company.extra_metadata or {})


def _site_from_metadata(company: Company) -> SiteFacts | None:
    """The homepage facts the ``sites`` step kept, rebuilt for the summary step."""
    stored = (company.extra_metadata or {}).get("site")
    if not isinstance(stored, dict):
        return None
    return SiteFacts(
        title=stored.get("title"),
        description=stored.get("description"),
        text=str(stored.get("text") or ""),
        address=None,
        pincode=None,
    )


def _wikidata_description(company: Company) -> str | None:
    value = (company.extra_metadata or {}).get("wikidata_description")
    return value if isinstance(value, str) else None


def _set_summary(company: Company, summary: str, source: str) -> None:
    company.summary = summary[:200]
    company.summary_source = source


async def _import(
    session: AsyncSession, companies: list[ImportedCompany], source: str
) -> dict[str, int]:
    created = updated = 0
    for i, imported in enumerate(companies, start=1):
        company, was_created = await upsert_company_ex(session, imported, source)
        if company is None:
            continue
        created += was_created
        updated += not was_created
        if i % _COMMIT_EVERY == 0:
            await session.commit()
    await session.commit()
    return {"seen": len(companies), "created": created, "updated": updated}


async def _step_import(session: AsyncSession, step: str, limit: int) -> dict[str, int]:
    module = wikidata if step == "wikidata" else osm
    async with httpx.AsyncClient() as client:
        payload = await module.fetch(client)
    return await _import(session, module.parse(payload)[:limit], step)


def _apply_site(company: Company, facts: SiteFacts | None) -> None:
    company.enriched_at = datetime.now(UTC)
    if facts is None:
        return
    metadata = _metadata(company)
    metadata["site"] = {
        "title": facts.title,
        "description": facts.description,
        "text": facts.text[:_SITE_TEXT_KEEP],
    }
    current = {"address": company.address, "pincode": company.pincode}
    updates, provenance = merge_fields(
        current,
        dict(metadata.get("provenance") or {}),
        {"address": facts.address, "pincode": facts.pincode},
        "site",
    )
    for field, value in updates.items():
        setattr(company, field, value)
    metadata["provenance"] = provenance
    company.extra_metadata = metadata
    if not company.area and facts.address:
        company.area = area_for_text(facts.address)
    if not company.summary:
        chosen = choose_summary(
            curated=company.description,
            site=facts.description,
            wikidata=_wikidata_description(company),
            name=company.name,
        )
        if chosen:
            _set_summary(company, *chosen)


async def _step_sites(session: AsyncSession, limit: int) -> dict[str, int]:
    companies = await due(session, "sites", limit)
    gate = asyncio.Semaphore(SITE_CONCURRENCY)

    async def read(client: httpx.AsyncClient, url: str) -> SiteFacts | None:
        async with gate:
            return await read_site(client, url)

    async with httpx.AsyncClient() as client:
        facts = await asyncio.gather(
            *(read(client, c.website_url or "") for c in companies), return_exceptions=True
        )
    read_ok = people = 0
    for company, result in zip(companies, facts, strict=True):
        site = result if isinstance(result, SiteFacts) else None
        _apply_site(company, site)
        if site is not None:
            read_ok += 1
            if site.people:
                people += await upsert_people(session, company.id, site.people)
    await session.commit()
    return {"due": len(companies), "read": read_ok, "people": people}


async def _locate(client: httpx.AsyncClient, company: Company) -> GeoHit | None:
    hit: GeoHit | None = None
    if company.address:
        hit = await geocode(client, address=company.address)
    if hit is None and company.pincode:
        hit = await geocode(client, postcode=company.pincode)
    if hit is None and not company.address and not company.pincode:
        hit = await geocode(client, name=company.name)
    if hit is not None and hit.precision == "area":
        # A suburb-level address or a PIN code names an area, not a building:
        # nudge off the centroid so the companies sharing it do not stack.
        lat, lng = jitter(str(company.id), hit.lat, hit.lng)
        hit = GeoHit(lat=lat, lng=lng, precision="area", source=hit.source, area=hit.area)
    area_hint = company.area or area_for_text(company.address)
    return resolve_location(str(company.id), known=hit, area_hint=area_hint)


async def _step_geocode(session: AsyncSession, limit: int) -> dict[str, int]:
    companies = await due(session, "geocode", limit)
    placed = 0
    async with httpx.AsyncClient() as client:
        for company in companies:
            hit = await _locate(client, company)
            if hit is not None:
                apply_location(company, hit)
                placed += company.lat is not None
            mark_tried(company, "geocode_at")
            await session.commit()
    return {"due": len(companies), "placed": placed}


async def _step_summaries(session: AsyncSession, limit: int) -> dict[str, int]:
    companies = await due(session, "summaries", limit)
    sourced = generated = 0
    for company in companies:
        site = _site_from_metadata(company)
        wikidata_desc = _wikidata_description(company)
        chosen = choose_summary(
            curated=company.description,
            site=site.description if site else None,
            wikidata=wikidata_desc,
            name=company.name,
        )
        if chosen:
            _set_summary(company, *chosen)
            sourced += 1
        elif has_source_text(site, wikidata_desc):
            line = await llm_summary(company.name, site, wikidata_desc)
            if line:
                _set_summary(company, line, "llm")
                generated += 1
        mark_tried(company, "summary_at")
        await session.commit()
    return {"due": len(companies), "from_sources": sourced, "llm": generated}


async def _step_github(session: AsyncSession, limit: int) -> dict[str, int]:
    companies = await due(session, "github", limit)
    inserted = 0
    async with httpx.AsyncClient() as client:
        for company in companies:
            members = await github_members(company.github_org or "", client=client)
            inserted += await upsert_people(session, company.id, members)
            mark_tried(company, "github_people_at")
            await session.commit()
    return {"due": len(companies), "people": inserted}


async def run_step(step: str, limit: int) -> dict[str, int | str]:
    """Run one refresh step; never raises."""
    if step not in STEPS:
        return {"error": f"unknown step {step!r}; expected one of {', '.join(STEPS)}"}
    bounded = max(1, min(limit, MAX_LIMIT[step]))
    try:
        async with AsyncSessionLocal() as session:
            if step in ("wikidata", "osm"):
                counts = await _step_import(session, step, bounded)
            elif step == "sites":
                counts = await _step_sites(session, bounded)
            elif step == "geocode":
                counts = await _step_geocode(session, bounded)
            elif step == "summaries":
                counts = await _step_summaries(session, bounded)
            else:
                counts = await _step_github(session, bounded)
    except Exception as exc:  # a scheduled step must never take the caller down
        logger.exception("company-map step %s failed", step)
        return {"step": step, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"step": step, **counts}
