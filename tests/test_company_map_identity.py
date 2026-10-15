"""
tests/test_company_map_identity.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
How the company map decides two records are the same employer, and which
source's value wins when they disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.company_identity import (
    SOURCE_PRECEDENCE,
    merge_fields,
    normalize_domain,
    normalize_name,
    slugify,
)


class TestDomain:
    @pytest.mark.parametrize(
        ("value", "domain"),
        [
            ("https://www.Razorpay.com/careers", "razorpay.com"),
            ("razorpay.com", "razorpay.com"),
            ("http://careers.google.com:443/jobs", "google.com"),
            ("blog.zerodha.co.in", "zerodha.co.in"),
            # Indian second-level zones: each bank and college keeps its own name.
            ("https://www.canarabank.bank.in", "canarabank.bank.in"),
            ("jana.bank.in", "jana.bank.in"),
            ("iisc.ac.in", "iisc.ac.in"),
            ("www.karnataka.gov.in", "karnataka.gov.in"),
            ("pes.edu.in", "pes.edu.in"),
            ("https://acme.github.io/", "acme.github.io"),
            ("", None),
            (None, None),
            ("not a domain", None),
        ],
    )
    def test_registrable_domain(self, value, domain):
        assert normalize_domain(value) == domain

    @pytest.mark.parametrize(
        "value",
        [
            "https://www.linkedin.com/company/acme",
            "https://twitter.com/acme",
            "https://x.com/acme",
            "https://www.facebook.com/acme",
            "https://medium.com/@acme",
            "https://sites.google.com/view/acme",
        ],
    )
    def test_a_profile_on_someone_elses_site_is_not_a_company_domain(self, value):
        # Matching companies on "linkedin.com" would merge every company whose
        # only website is its LinkedIn page into one row.
        assert normalize_domain(value) is None


class TestName:
    def test_legal_suffixes_are_dropped(self):
        assert normalize_name("Acme Technologies Pvt. Ltd.") == "acme technologies"
        assert normalize_name("ACME Private Limited") == "acme"

    def test_punctuation_and_spacing_are_ignored(self):
        assert normalize_name("  Zeta  (Directi) ") == "zeta directi"

    def test_slug(self):
        assert slugify("Zeta (Directi)") == "zeta-directi"
        assert slugify("Café Coffee Day") == "cafe-coffee-day"
        assert slugify("!!!") == "company"
        assert len(slugify("x" * 300)) == 96


def _merge(current, provenance, incoming, source):
    return merge_fields(current, provenance, incoming, source)


class TestMerge:
    def test_precedence_order(self):
        assert SOURCE_PRECEDENCE == (
            "curated",
            "site",
            "wikidata",
            "yc",
            "osm",
            "github_org",
            "news",
            "llm",
        )

    def test_empty_field_takes_any_source(self):
        updates, prov = _merge({"address": None}, {}, {"address": "HSR Sector 2"}, "osm")
        assert updates == {"address": "HSR Sector 2"}
        assert prov == {"address": "osm"}

    def test_lower_source_does_not_overwrite_curated(self):
        updates, prov = _merge(
            {"description": "Curated words"},
            {"description": "curated"},
            {"description": "Wikidata words"},
            "wikidata",
        )
        assert updates == {}
        assert prov == {"description": "curated"}

    def test_value_with_unknown_provenance_counts_as_curated(self):
        # Rows seeded before provenance existed came from the catalogue.
        updates, _ = _merge({"founded_year": 2014}, {}, {"founded_year": 2015}, "wikidata")
        assert updates == {}

    def test_same_source_refreshes_its_own_value(self):
        updates, _ = _merge(
            {"employee_count": 900},
            {"employee_count": "wikidata"},
            {"employee_count": 1200},
            "wikidata",
        )
        assert updates == {"employee_count": 1200}

    def test_higher_source_overwrites_lower(self):
        updates, prov = _merge(
            {"address": "OSM address"}, {"address": "osm"}, {"address": "Site address"}, "site"
        )
        assert updates == {"address": "Site address"}
        assert prov["address"] == "site"

    def test_none_never_clears_a_field(self):
        updates, _ = _merge({"address": "Kept"}, {"address": "osm"}, {"address": None}, "osm")
        assert updates == {}

    def test_newer_funding_news_moves_the_stage_past_curated(self):
        updates, prov = _merge(
            {"stage": "series_a", "last_funding_at": datetime(2025, 1, 1, tzinfo=UTC)},
            {"stage": "curated"},
            {"stage": "series_b", "last_funding_at": datetime(2026, 9, 1, tzinfo=UTC)},
            "news",
        )
        assert updates == {"stage": "series_b", "last_funding_at": datetime(2026, 9, 1, tzinfo=UTC)}
        assert prov["stage"] == "news"

    def test_older_funding_news_does_not_move_the_stage(self):
        updates, _ = _merge(
            {"stage": "series_b", "last_funding_at": datetime(2026, 9, 1, tzinfo=UTC)},
            {"stage": "news"},
            {"stage": "series_a", "last_funding_at": datetime(2024, 1, 1, tzinfo=UTC)},
            "news",
        )
        assert updates == {}

    def test_an_undated_stage_does_not_undo_dated_funding(self):
        # Re-seeding the catalogue must not put back the stage news replaced.
        updates, _ = _merge(
            {"stage": "series_b", "last_funding_at": datetime(2026, 9, 1, tzinfo=UTC)},
            {"stage": "news"},
            {"stage": "series_a"},
            "curated",
        )
        assert updates == {}

    def test_unknown_source_is_rejected(self):
        with pytest.raises(ValueError):
            _merge({}, {}, {"address": "x"}, "rumour")
