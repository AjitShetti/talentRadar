"""
services/company_map/refresh.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled work behind the company map, one bounded step per call.

``POST /api/v1/ingest/company-map/refresh?step=…`` (run by
``.github/workflows/company-map.yml``) and ``scripts/refresh_company_map.py``
both call :func:`run_step`. A step:

* ``wikidata`` / ``osm`` — import Bengaluru companies and merge them in.
* ``yc`` — Bengaluru companies from Y Combinator's public directory (a
  community mirror; one request).
* ``hiring_list`` — Bengaluru employers from the shared recruiter list checked
  into ``data/company_sources`` (name and website only; no request).
* ``github_orgs`` — Bengaluru organisations on GitHub: the next ``limit``
  results of a cursor-driven search, each looked up once (an organisation
  checked in the last 30 days is not looked up again) and kept only if it is a
  real company. The cursor lives in the cache, so a cold cache restarts the
  walk, which costs requests but loses nothing.
* ``sites`` — read up to ``limit`` company homepages (robots.txt respected,
  each site at most once per 30 days): address, PIN, people, and a summary
  when the site or Wikidata already says what the company does.
* ``geocode`` — place up to ``limit`` companies through Nominatim (1 req/s),
  falling back to the centroid of a known area.
* ``summaries`` — the budgeted LLM line for companies whose sources had text
  but no usable sentence. A company with no source text gets none.
* ``github`` — public org members as engineers.
* ``tech`` — the tech verdict (``services/company_map/tech.py``) for every row
  that is not yet listed, from the descriptions stored since: the map lists
  tech employers only. Run last, after ``sites`` and ``summaries`` have added text.

:func:`run_step` never raises: a failure comes back as ``{"error": …}`` and
the rows keep their last good data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.areas import area_for_text, jitter
from domain.company_identity import merge_fields
from services.cache_backend import CacheBackend
from services.company_map import github_orgs, hiring_list, osm, wikidata, yc
from services.company_map.geocode import geocode, resolve_location
from services.company_map.people import github_members, upsert_people
from services.company_map.sourced import SourcedCompany
from services.company_map.store import apply_location, due, mark_tried, upsert_company_ex
from services.company_map.summary import choose_summary, has_source_text, llm_summary
from services.company_map.tech import apply_tech, reclassify_tech
from services.company_map.types import GeoHit, ImportedCompany, SiteFacts
from services.company_map.website import read_site
from storage.database import AsyncSessionLocal
from storage.models import Company

logger = logging.getLogger(__name__)

STEPS = (
    "wikidata",
    "osm",
    "yc",
    "hiring_list",
    "github_orgs",
    "sites",
    "geocode",
    "summaries",
    "github",
    "tech",
)

#: Hard ceilings whatever the caller asks for: Nominatim's policy, the daily
#: summary budget, and what one request can finish.
MAX_LIMIT = {
    "wikidata": 5000,
    "osm": 5000,
    "yc": 5000,
    "hiring_list": 5000,
    "github_orgs": 100,
    "sites": 100,
    "geocode": 40,
    "summaries": 60,
    "github": 50,
    "tech": 5000,
}

#: Where the GitHub search walk stands (a ``github_orgs.Cursor``), and for how long.
GITHUB_ORGS_CURSOR = "tr:company-map:github-orgs:cursor"
_CURSOR_TTL_SECONDS = 120 * 24 * 3600
#: An organisation is not looked up again for this long.
GITHUB_ORG_REFRESH = timedelta(days=30)
#: Consecutive failed lookups that end a run: GitHub or the network is down.
_MAX_LOOKUP_FAILURES = 5

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


#: Summary sources a better-ranked line may replace.
_DIRECTORY_SOURCES = frozenset({"yc", "github_org"})


def _directory_description(company: Company) -> tuple[str, str] | None:
    """A YC or GitHub one-liner as ``(text, source)``, YC first."""
    metadata = company.extra_metadata or {}
    for key, source in (("yc_description", "yc"), ("github_org_description", "github_org")):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value, source
    return None


def _set_summary(company: Company, summary: str, source: str) -> None:
    company.summary = summary[:200]
    company.summary_source = source


def _merge_source_metadata(company: Company, imported: ImportedCompany) -> None:
    """Keep the facts only the source states (a YC batch, a follower count) on the row."""
    if isinstance(imported, SourcedCompany) and imported.source_metadata:
        metadata = _metadata(company)
        metadata.update(imported.source_metadata)
        company.extra_metadata = metadata


async def _upsert(
    session: AsyncSession, imported: ImportedCompany, source: str
) -> tuple[Company | None, bool]:
    company, created = await upsert_company_ex(session, imported, source)
    if company is not None:
        _merge_source_metadata(company, imported)
        if not company.summary and (directory := _directory_description(company)):
            chosen = choose_summary(
                curated=company.description,
                site=None,
                wikidata=_wikidata_description(company),
                name=company.name,
                directory=directory,
            )
            if chosen:
                _set_summary(company, *chosen)
    return company, created


async def _import(
    session: AsyncSession,
    companies: Sequence[ImportedCompany],
    source: str,
    *,
    skipped: int = 0,
) -> dict[str, int]:
    created = updated = 0
    for i, imported in enumerate(companies, start=1):
        company, was_created = await _upsert(session, imported, source)
        if company is None:
            skipped += 1
            continue
        created += was_created
        updated += not was_created
        if i % _COMMIT_EVERY == 0:
            await session.commit()
    await session.commit()
    return {"seen": len(companies), "created": created, "updated": updated, "skipped": skipped}


async def _step_import(session: AsyncSession, step: str, limit: int) -> dict[str, int]:
    module = wikidata if step == "wikidata" else osm
    async with httpx.AsyncClient() as client:
        payload = await module.fetch(client)
    return await _import(session, module.parse(payload)[:limit], step)


async def _step_yc(session: AsyncSession, limit: int) -> dict[str, int]:
    async with httpx.AsyncClient() as client:
        payload = await yc.fetch(client)
    companies, unusable = yc.scan(payload)
    return await _import(session, companies[:limit], "yc", skipped=unusable)


def _stamped_within(company: Company, key: str, window: timedelta) -> bool:
    """True when the row's metadata holds an ISO timestamp under ``key`` newer than ``window``."""
    stamp = (company.extra_metadata or {}).get(key)
    if not isinstance(stamp, str):
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when < window


async def _recently_checked(session: AsyncSession, logins: list[str]) -> set[str]:
    """The lowercased logins whose organisation this step looked up in the last 30 days."""
    if not logins:
        return set()
    rows = await session.execute(
        select(Company).where(func.lower(Company.github_org).in_([x.lower() for x in logins]))
    )
    return {
        (company.github_org or "").lower()
        for company in rows.scalars()
        if _stamped_within(company, "github_org_checked_at", GITHUB_ORG_REFRESH)
    }


async def _step_github_orgs(session: AsyncSession, limit: int) -> dict[str, int | str]:
    start = github_orgs.Cursor.decode(await CacheBackend.get_json(GITHUB_ORGS_CURSOR))
    seen = lookups = created = updated = skipped = failures = 0
    stopped: str | None = None
    position = start
    async with httpx.AsyncClient() as client:
        walk = await github_orgs.fetch_candidates(client, start, limit)
        resting = await _recently_checked(session, [c.login for c in walk.candidates])
        for candidate in walk.candidates:
            company: Company | None = None
            was_created = False
            if candidate.login.lower() not in resting:
                try:
                    org = await github_orgs.fetch_org(client, candidate.login)
                except github_orgs.RateLimited:
                    stopped = "rate_limited"
                    break
                except httpx.HTTPError as exc:
                    logger.info("GitHub org %s unavailable: %s", candidate.login, exc)
                    failures += 1
                    if failures >= _MAX_LOOKUP_FAILURES:
                        stopped = "errors"
                        break
                else:
                    failures = 0
                    lookups += 1
                    imported = github_orgs.parse_org(org)
                    if imported is not None:
                        company, was_created = await _upsert(session, imported, "github_org")
            if company is None:
                skipped += 1
            else:
                mark_tried(company, "github_org_checked_at")
                created += was_created
                updated += not was_created
                if (created + updated) % _COMMIT_EVERY == 0:
                    await session.commit()
            seen += 1
            position = candidate.next
        else:
            # Every candidate is done, so the walk moves on to where it ended
            # (or to the start of a new cycle) unless the search itself was cut off.
            if not walk.rate_limited:
                position = walk.cursor
    if walk.rate_limited and stopped is None:
        stopped = "rate_limited"
    await session.commit()
    await CacheBackend.set_json(GITHUB_ORGS_CURSOR, position.encode(), _CURSOR_TTL_SECONDS)
    counts: dict[str, int | str] = {
        "seen": seen,
        "lookups": lookups,
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }
    if stopped:
        counts["stopped"] = stopped
    return counts


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
    # A directory one-liner is a stopgap: a curated, homepage or Wikidata line replaces it.
    if not company.summary or company.summary_source in _DIRECTORY_SOURCES:
        chosen = choose_summary(
            curated=company.description,
            site=facts.description,
            wikidata=_wikidata_description(company),
            name=company.name,
            directory=_directory_description(company),
        )
        if chosen:
            _set_summary(company, *chosen)
    apply_tech(company)


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
            directory=_directory_description(company),
        )
        if chosen:
            _set_summary(company, *chosen)
            sourced += 1
        elif has_source_text(site, wikidata_desc):
            line = await llm_summary(company.name, site, wikidata_desc)
            if line:
                _set_summary(company, line, "llm")
                generated += 1
        apply_tech(company)
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


async def _step_tech(session: AsyncSession, limit: int) -> dict[str, int]:
    counts = await reclassify_tech(session, limit)
    await session.commit()
    return counts


async def run_step(step: str, limit: int) -> dict[str, int | str]:
    """Run one refresh step; never raises."""
    if step not in STEPS:
        return {"error": f"unknown step {step!r}; expected one of {', '.join(STEPS)}"}
    bounded = max(1, min(limit, MAX_LIMIT[step]))
    try:
        async with AsyncSessionLocal() as session:
            counts: Mapping[str, int | str]
            if step in ("wikidata", "osm"):
                counts = await _step_import(session, step, bounded)
            elif step == "yc":
                counts = await _step_yc(session, bounded)
            elif step == "hiring_list":
                counts = await _import(session, hiring_list.load()[:bounded], "hiring_list")
            elif step == "github_orgs":
                counts = await _step_github_orgs(session, bounded)
            elif step == "sites":
                counts = await _step_sites(session, bounded)
            elif step == "geocode":
                counts = await _step_geocode(session, bounded)
            elif step == "summaries":
                counts = await _step_summaries(session, bounded)
            elif step == "tech":
                counts = await _step_tech(session, bounded)
            else:
                counts = await _step_github(session, bounded)
    except Exception as exc:  # a scheduled step must never take the caller down
        logger.exception("company-map step %s failed", step)
        return {"step": step, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"step": step, **counts}
