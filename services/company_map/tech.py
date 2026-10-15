"""
services/company_map/tech.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The stored side of the tech verdict (``domain.company_taxonomy.is_tech``).

The map lists tech employers only. A row's verdict is decided from everything
we have stored about it, so the same answer comes out whichever step learned
the most recent fact:

* a catalogue row (a tier no importer wrote) is tech;
* a row a tech-only directory (YC, GitHub) created is tech, unless its name or
  a source's text names a non-tech kind of organisation;
* otherwise what curators, Wikidata, OSM, news and the directories said is
  read with the industry labels, name and sector; the company's own homepage
  (and a summary made from it) is weak evidence (``is_tech(site_text=…)``).

The verdict is recomputed from everything stored each time, so it can go
back: stored text only accumulates, so a later sparse source cannot unlist a
row, and a homepage that says "book online" cannot list a hospital for good
(review C1, 2026-10-15).

Callers own the transaction; nothing here commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from domain.company_taxonomy import is_tech, non_tech_named
from storage.models import Company

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: ``metadata`` keys holding a source's one-line description of the company.
_DESCRIPTION_KEYS = (
    "wikidata_description",
    "osm_description",
    "news_description",
    "yc_description",
    "github_org_description",
)
#: Sources that only list technology companies: a row one of them created
#: (its ``name`` came from there) is tech unless something names it otherwise.
_TECH_SOURCES = frozenset({"yc", "github_org"})
_INDUSTRY_KEYS = ("wikidata_industries", "osm_industries")


#: Summaries made from the homepage are as weak as the homepage itself.
_SITE_SUMMARY_SOURCES = frozenset({"site", "llm"})


def has_curated_tier(company: Company) -> bool:
    """A catalogue tier: set, and not written by an importer (which records its source)."""
    if not company.tier:
        return False
    provenance = (company.extra_metadata or {}).get("provenance")
    source = provenance.get("tier") if isinstance(provenance, dict) else None
    return source in (None, "curated")


def _created_by_tech_source(company: Company) -> bool:
    provenance = (company.extra_metadata or {}).get("provenance")
    return isinstance(provenance, dict) and provenance.get("name") in _TECH_SOURCES


def _said(company: Company) -> str:
    """What curators, encyclopedias, directories and news say about the company."""
    metadata: dict[str, Any] = company.extra_metadata or {}
    texts = [company.description, company.industry]
    if company.summary_source not in _SITE_SUMMARY_SOURCES:
        texts.append(company.summary)
    texts.extend(metadata.get(key) for key in _DESCRIPTION_KEYS)
    return " ; ".join(t for t in texts if isinstance(t, str) and t.strip())


def _site_text(company: Company) -> str:
    """What the company's own homepage says, and a summary made from it."""
    texts: list[object] = []
    site = (company.extra_metadata or {}).get("site")
    if isinstance(site, dict):
        texts.append(site.get("description"))
    if company.summary_source in _SITE_SUMMARY_SOURCES:
        texts.append(company.summary)
    return " ; ".join(t for t in texts if isinstance(t, str) and t.strip())


def _labels(company: Company) -> list[str]:
    metadata: dict[str, Any] = company.extra_metadata or {}
    labels: list[str] = []
    for key in _INDUSTRY_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            labels.extend(v for v in value if isinstance(v, str))
    return labels


def classify(company: Company) -> bool:
    """The verdict for ``company`` from what is stored on it right now."""
    if has_curated_tier(company):
        return True
    said = _said(company)
    if _created_by_tech_source(company) and not non_tech_named(name=company.name, text=said):
        return True
    return is_tech(
        name=company.name,
        description=said,
        industry_labels=_labels(company),
        sector=company.sector,
        site_text=_site_text(company),
    )


def apply_tech(company: Company) -> bool:
    """Set ``company.is_tech`` from :func:`classify` and return it."""
    company.is_tech = classify(company)
    return company.is_tech


async def reclassify_tech(session: AsyncSession, limit: int | None = None) -> dict[str, int]:
    """
    Recompute the verdict of up to ``limit`` rows, NULL first (the ones
    migration 016 left), then the rest by name.

    Returns ``{seen, tech, not_tech}``. The caller commits.
    """
    query = select(Company).order_by(Company.is_tech.is_not(None), Company.name)
    if limit is not None:
        query = query.limit(limit)
    rows = list((await session.execute(query)).scalars())
    tech = sum(apply_tech(company) for company in rows)
    return {"seen": len(rows), "tech": tech, "not_tech": len(rows) - tech}
