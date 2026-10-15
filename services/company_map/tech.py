"""
services/company_map/tech.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The stored side of the tech verdict (``domain.company_taxonomy.is_tech``).

The map lists tech employers only. A row's verdict is decided from everything
we have stored about it, so the same answer comes out whichever step learned
the most recent fact:

* a catalogue row (a tier no importer wrote) is tech;
* otherwise the one-liners any source left (curated description, summary,
  Wikidata/OSM/news descriptions, the company's own homepage description) and
  the industry labels are read together with the name and sector.

The verdict only ever moves towards ``True``. More text can turn a ``False``
into a ``True`` (a homepage was read, a summary written), but a company the
map already lists is never unlisted by a later source that says less.

Callers own the transaction; nothing here commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from domain.company_taxonomy import is_tech
from storage.models import Company

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: ``metadata`` keys holding a source's one-line description of the company.
_DESCRIPTION_KEYS = ("wikidata_description", "osm_description", "news_description")
_INDUSTRY_KEYS = ("wikidata_industries", "osm_industries")


def has_curated_tier(company: Company) -> bool:
    """A catalogue tier: set, and not written by an importer (which records its source)."""
    if not company.tier:
        return False
    provenance = (company.extra_metadata or {}).get("provenance")
    source = provenance.get("tier") if isinstance(provenance, dict) else None
    return source in (None, "curated")


def _texts(company: Company) -> list[str]:
    metadata: dict[str, Any] = company.extra_metadata or {}
    texts = [company.description, company.summary, company.industry]
    texts.extend(metadata.get(key) for key in _DESCRIPTION_KEYS)
    site = metadata.get("site")
    if isinstance(site, dict):
        texts.append(site.get("description"))
    return [t for t in texts if isinstance(t, str) and t.strip()]


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
    return is_tech(
        name=company.name,
        description=" ; ".join(_texts(company)),
        industry_labels=_labels(company),
        sector=company.sector,
    )


def apply_tech(company: Company) -> bool:
    """
    Set ``company.is_tech`` from :func:`classify`, never moving a ``True`` back.

    Returns the stored verdict.
    """
    if company.is_tech is not True:
        company.is_tech = classify(company)
    return bool(company.is_tech)


async def reclassify_tech(session: AsyncSession, limit: int | None = None) -> dict[str, int]:
    """
    Classify every row that is not yet a ``True``: the ones the migration left
    NULL, and the ``False`` ones that may have gained text since.

    Returns ``{seen, tech, not_tech}`` where ``tech`` counts rows that are now
    ``True`` (newly or already) among those looked at. The caller commits.
    """
    query = (
        select(Company)
        .where(or_(Company.is_tech.is_(None), Company.is_tech.is_(False)))
        .order_by(Company.name)
    )
    if limit is not None:
        query = query.limit(limit)
    rows = list((await session.execute(query)).scalars())
    tech = sum(apply_tech(company) for company in rows)
    return {"seen": len(rows), "tech": tech, "not_tech": len(rows) - tech}
