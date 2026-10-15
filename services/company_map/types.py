"""
services/company_map/types.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The values the company-map importers hand to the store. Plain frozen
dataclasses: an importer says what a source claims, the store decides what
to keep (``domain.company_identity.merge_fields``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class PersonCandidate:
    """A named person a public source ties to a company. Never inferred."""

    name: str
    kind: str  # founder | leader | engineer
    source_url: str
    source_kind: str  # curated | wikidata | site | news | github | tavily
    title: str | None = None
    linkedin_url: str | None = None
    x_url: str | None = None
    github_url: str | None = None
    profile_url: str | None = None


@dataclass(frozen=True, kw_only=True)
class ImportedCompany:
    """One company as an import source describes it."""

    name: str
    domain: str | None
    website_url: str | None
    wikidata_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    address: str | None = None
    founded_year: int | None = None
    employee_count: int | None = None
    x_url: str | None = None
    linkedin_url: str | None = None
    github_org: str | None = None
    logo_url: str | None = None
    description: str | None = None
    #: Industry labels the source gave ("software industry"); kept for the tech verdict.
    industry_labels: tuple[str, ...] = ()
    sector: str | None = None
    tier: str | None = None
    stage: str | None = None
    people: tuple[PersonCandidate, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SiteFacts:
    """What a company's own homepage (and one contact page) says."""

    title: str | None
    description: str | None
    text: str
    address: str | None
    pincode: str | None
    people: tuple[PersonCandidate, ...] = ()
    links: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class GeoHit:
    """A resolved position and how much to trust it."""

    lat: float
    lng: float
    precision: str  # exact | street | area | city
    source: str  # curated | wikidata | osm | nominatim | pincode | area
    area: str | None = None


@dataclass(frozen=True, kw_only=True)
class FeedItem:
    """One entry of a news feed, before classification."""

    source: str
    url: str
    title: str
    snippet: str | None
    published_at: datetime | None
