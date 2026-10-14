"""
tests/test_company_map_schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Migration 015: the company map's columns, the people kinds that join
``company_contacts``, and the ``company_news`` table.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from api.schemas.company_schemas import ContactSchema
from services.company_contacts import VALID_KINDS, _serialise
from storage.models import Company, CompanyContact, CompanyNews

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "storage"
    / "migrations"
    / "versions"
    / "015_company_map.py"
)


def test_company_has_the_map_columns():
    columns = set(Company.__table__.c.keys())
    assert {
        "slug",
        "summary",
        "summary_source",
        "stage",
        "sector",
        "area",
        "address",
        "pincode",
        "lat",
        "lng",
        "geo_precision",
        "geo_source",
        "wikidata_id",
        "employee_count",
        "x_url",
        "last_funding_at",
        "enriched_at",
    } <= columns
    assert Company.__table__.c.slug.unique
    assert Company.__table__.c.wikidata_id.unique


def test_contact_has_the_people_columns():
    assert {"x_url", "github_url", "profile_url", "source_kind"} <= set(
        CompanyContact.__table__.c.keys()
    )


def test_company_news_table():
    assert CompanyNews.__tablename__ == "company_news"
    assert {
        "id",
        "url",
        "source",
        "title",
        "snippet",
        "published_at",
        "category",
        "company_id",
        "company_name_raw",
        "round",
        "amount_text",
        "amount_usd",
        "investors",
        "extracted_by",
        "created_at",
    } <= set(CompanyNews.__table__.c.keys())
    assert CompanyNews.__table__.c.url.unique
    assert CompanyNews.__table__.c.company_id.nullable


def test_people_kinds_are_valid_contact_kinds():
    assert {"founder", "leader", "engineer"} <= VALID_KINDS


def test_serialised_contact_carries_the_people_links():
    contact = CompanyContact(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        kind="founder",
        name="Asha Rao",
        x_url="https://x.com/asharao",
        github_url="https://github.com/asharao",
        profile_url="https://asharao.dev",
        source_url="https://www.wikidata.org/wiki/Q1",
        source_kind="wikidata",
        verified=False,
    )
    payload = _serialise(contact)
    assert payload["x_url"] == "https://x.com/asharao"
    assert payload["github_url"] == "https://github.com/asharao"
    assert payload["profile_url"] == "https://asharao.dev"
    assert payload["source_kind"] == "wikidata"
    assert ContactSchema(**payload).x_url == "https://x.com/asharao"


def test_migration_follows_014():
    spec = importlib.util.spec_from_file_location("migration_015", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "015_company_map"
    assert module.down_revision == "014_interview_role"
