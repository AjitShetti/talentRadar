"""
scripts/seed_companies.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Load the curated company catalogue in ``data/companies/*.json`` into Postgres.

    python scripts/seed_companies.py                    # every city file
    python scripts/seed_companies.py --city bengaluru   # one file
    python scripts/seed_companies.py --dry-run          # report, change nothing

Upserts on ``domain``, so re-running after editing the JSON updates the existing
rows rather than duplicating them. Ingestion also creates ``companies`` rows as
a side effect of parsing job postings — those are thin (a name, maybe a domain).
When the catalogue matches one, this fills it in instead of creating a second
row for the same employer, which also links the seeded profile to whatever jobs
have already been ingested for it.

Curated contacts (``user_id IS NULL``) are reconciled by (kind, source_url), so
a re-run does not stack duplicates. Contacts a *user* saved are never touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from storage.database import AsyncSessionLocal
from storage.models import Company, CompanyContact, CompanyProfile

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_companies")

CATALOGUE_DIR = Path(__file__).resolve().parent.parent / "data" / "companies"

# Fields copied straight from the JSON record onto the Company row.
_COMPANY_FIELDS = (
    "name", "domain", "website_url", "linkedin_url", "logo_url", "industry",
    "hq_city", "hq_country", "employee_count_range", "founded_year",
    "description", "tier", "github_org", "careers_url", "office_cities",
)


def load_catalogue(city: str | None = None) -> list[dict[str, Any]]:
    """Read one or all city files. Raises if a requested file is missing."""
    if not CATALOGUE_DIR.exists():
        raise FileNotFoundError(f"No catalogue directory at {CATALOGUE_DIR}")

    if city:
        paths = [CATALOGUE_DIR / f"{city.lower()}.json"]
        if not paths[0].exists():
            available = ", ".join(sorted(p.stem for p in CATALOGUE_DIR.glob("*.json"))) or "none"
            raise FileNotFoundError(f"No catalogue for {city!r}. Available: {available}")
    else:
        paths = sorted(CATALOGUE_DIR.glob("*.json"))

    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("companies", payload) if isinstance(payload, dict) else payload
        logger.info("Loaded %d companies from %s", len(entries), path.name)
        records.extend(entries)
    return records


async def _find_existing(session, record: dict[str, Any]) -> Company | None:
    """
    Match the catalogue record to a row already in the table.

    Domain first (it is the unique key), then a case-insensitive name match so
    a thin row created by job ingestion gets enriched rather than duplicated.
    """
    domain = record.get("domain")
    if domain:
        found = (
            await session.execute(select(Company).where(Company.domain == domain))
        ).scalar_one_or_none()
        if found is not None:
            return found
    return (
        await session.execute(
            select(Company).where(func.lower(Company.name) == record["name"].lower())
        )
    ).scalars().first()


async def _sync_contacts(session, company: Company, record: dict[str, Any]) -> int:
    """
    Reconcile the curated (``user_id IS NULL``) contacts for this company.

    Matches on (kind, source_url) so re-running updates in place. Only curated
    rows are considered — anything a user saved is left strictly alone.
    """
    wanted = record.get("contacts") or []
    existing = (
        await session.execute(
            select(CompanyContact).where(
                CompanyContact.company_id == company.id,
                CompanyContact.user_id.is_(None),
            )
        )
    ).scalars().all()
    by_key = {(c.kind, c.source_url): c for c in existing}

    touched = 0
    for entry in wanted:
        key = (entry.get("kind", "careers_page"), entry.get("source_url"))
        contact = by_key.pop(key, None)
        if contact is None:
            contact = CompanyContact(company_id=company.id, user_id=None, kind=key[0])
            session.add(contact)
        contact.name = entry.get("name")
        contact.title = entry.get("title")
        contact.email = entry.get("email")
        contact.linkedin_url = entry.get("linkedin_url")
        contact.notes = entry.get("notes")
        contact.source_url = entry.get("source_url")
        # Curated entries are published channels, not confirmed-working ones.
        contact.verified = bool(entry.get("verified", False))
        touched += 1

    # Curated rows the catalogue no longer lists (a careers URL that moved).
    for stale in by_key.values():
        await session.delete(stale)

    return touched


async def seed(city: str | None = None, *, dry_run: bool = False) -> dict[str, int]:
    """Upsert the catalogue. Returns counts of created/updated rows."""
    records = load_catalogue(city)
    stats = {"created": 0, "updated": 0, "profiles": 0, "contacts": 0}

    session = AsyncSessionLocal()
    try:
        for record in records:
            company = await _find_existing(session, record)
            if company is None:
                company = Company(name=record["name"])
                session.add(company)
                stats["created"] += 1
                action = "create"
            else:
                stats["updated"] += 1
                action = "update"

            for field in _COMPANY_FIELDS:
                if field in record and record[field] is not None:
                    setattr(company, field, record[field])

            await session.flush()  # need company.id for the profile and contacts

            tech_stack = record.get("tech_stack") or []
            if tech_stack:
                profile = (
                    await session.execute(
                        select(CompanyProfile).where(CompanyProfile.company_id == company.id)
                    )
                ).scalar_one_or_none()
                if profile is None:
                    profile = CompanyProfile(company_id=company.id)
                    session.add(profile)
                profile.tech_stack = tech_stack
                profile.source = "curated"
                stats["profiles"] += 1

            stats["contacts"] += await _sync_contacts(session, company, record)
            logger.debug("%s %s", action, record["name"])

        if dry_run:
            await session.rollback()
            logger.info("Dry run — rolled back, nothing written.")
        else:
            await session.commit()
    finally:
        await session.close()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--city", help="Seed a single city file, e.g. 'bengaluru'")
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and report without writing"
    )
    args = parser.parse_args()

    try:
        stats = asyncio.run(seed(args.city, dry_run=args.dry_run))
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    logger.info(
        "Done — %d created, %d updated, %d tech-stack profiles, %d curated contacts.",
        stats["created"], stats["updated"], stats["profiles"], stats["contacts"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
