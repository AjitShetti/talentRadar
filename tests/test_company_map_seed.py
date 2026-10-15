"""
tests/test_company_map_seed.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The catalogue seeder fills the company map's fields: a sector for every
curated industry, and a one-line summary from the curated description.
"""

from __future__ import annotations

from domain.company_taxonomy import SECTORS
from scripts.seed_companies import catalogue_fields, load_catalogue


def test_every_catalogue_record_gets_a_known_sector():
    for record in load_catalogue():
        assert catalogue_fields(record)["sector"] in SECTORS, record["name"]


def test_a_curated_description_becomes_a_curated_summary():
    fields = catalogue_fields(
        {
            "name": "Acme Pay",
            "industry": "Payments",
            "description": "Acme Pay runs payment rails for Indian SMEs. It was founded in 2019.",
        }
    )
    assert fields["summary"] == "Acme Pay runs payment rails for Indian SMEs."
    assert fields["summary_source"] == "curated"
    assert fields["sector"] == "fintech"


def test_an_explicit_sector_and_the_new_fields_are_kept():
    fields = catalogue_fields(
        {
            "name": "Acme",
            "industry": "Payments",
            "sector": "saas",
            "area": "HSR Layout",
            "address": "27th Main, HSR Layout",
            "stage": "seed",
        }
    )
    assert fields["sector"] == "saas"
    assert (fields["area"], fields["stage"]) == ("HSR Layout", "seed")
    assert fields["address"] == "27th Main, HSR Layout"


def test_no_description_means_no_summary_key():
    assert "summary" not in catalogue_fields({"name": "Acme", "industry": "Payments"})


def test_every_catalogue_record_is_a_tech_employer():
    # The curated catalogue is the list of tech employers; the classifier
    # decides only the rows an importer found.
    for record in load_catalogue():
        assert catalogue_fields(record)["is_tech"] is True, record["name"]
