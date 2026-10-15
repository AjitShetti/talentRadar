"""
tests/test_company_map_sources_domain.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The pure rules the Y Combinator and GitHub-organisation sources lean on:
where they sit in the source precedence, which hostnames are not a company's
own, and how a free-text location is matched against Bengaluru.
"""

from __future__ import annotations

import pytest

from domain.company_identity import SOURCE_PRECEDENCE, is_tenant_domain, merge_fields
from domain.geo import mentions_city


class TestPrecedence:
    def test_order(self) -> None:
        # YC is curated by YC but self-reported: under Wikidata, over OSM.
        # A GitHub profile is the weakest company-describing source: under OSM,
        # over the news and the LLM.
        assert SOURCE_PRECEDENCE == (
            "curated",
            "site",
            "wikidata",
            "yc",
            "osm",
            "github_org",
            "hiring_list",
            "news",
            "llm",
        )

    def test_source_names_fit_the_columns_that_record_them(self) -> None:
        # geo_source / summary_source / company_contacts.source_kind are String(16).
        assert all(len(name) <= 16 for name in SOURCE_PRECEDENCE)

    @pytest.mark.parametrize(
        ("holder", "challenger", "wins"),
        [
            ("wikidata", "yc", False),
            ("yc", "wikidata", True),
            ("osm", "yc", True),
            ("yc", "osm", False),
            ("osm", "github_org", False),
            ("github_org", "osm", True),
            ("news", "github_org", True),
            ("github_org", "news", False),
            ("curated", "yc", False),
            ("site", "github_org", False),
        ],
    )
    def test_a_lower_source_never_overwrites_a_higher_one(
        self, holder: str, challenger: str, wins: bool
    ) -> None:
        updates, provenance = merge_fields(
            {"sector": "saas"}, {"sector": holder}, {"sector": "fintech"}, challenger
        )
        assert (updates == {"sector": "fintech"}) is wins
        assert provenance["sector"] == (challenger if wins else holder)

    def test_a_source_refreshes_its_own_value(self) -> None:
        updates, _ = merge_fields({"sector": "saas"}, {"sector": "yc"}, {"sector": "ai"}, "yc")
        assert updates == {"sector": "ai"}

    def test_a_new_source_fills_an_empty_field(self) -> None:
        updates, provenance = merge_fields({"sector": None}, {}, {"sector": "ai"}, "github_org")
        assert updates == {"sector": "ai"}
        assert provenance == {"sector": "github_org"}


class TestTenantDomain:
    @pytest.mark.parametrize(
        "domain",
        ["acme.github.io", "acme.vercel.app", "acme.netlify.app", "x.pages.dev", "a.substack.com"],
    )
    def test_a_hosting_platform_subdomain_is_not_the_companys_own(self, domain: str) -> None:
        assert is_tenant_domain(domain)

    @pytest.mark.parametrize("domain", ["razorpay.com", "zerodha.co.in", "signoz.io", "iisc.ac.in"])
    def test_an_own_domain_is(self, domain: str) -> None:
        assert not is_tenant_domain(domain)


class TestMentionsCity:
    @pytest.mark.parametrize(
        "location",
        [
            "Bengaluru, KA, India",
            "Bangalore",
            "bangalore, karnataka",
            "SF/Bengaluru",
            "Mumbai | Bengaluru | Gurugram | Beijing ",
            "Gurugram, HR, India; Bengaluru, KA, India",
            "Delaware & Bengaluru",
            "Bengaluru, India; Mountain View, CA, USA",
        ],
    )
    def test_names_bengaluru_under_any_spelling_in_any_position(self, location: str) -> None:
        assert mentions_city(location, "Bengaluru")

    @pytest.mark.parametrize(
        "location",
        [
            None,
            "",
            "Remote",
            "London, England, United Kingdom",
            "Mumbai, MH, India",
            "San Francisco, CA, USA; Remote",
            # A place that shares a name with a Bengaluru locality.
            "Whitefield, NH, USA",
        ],
    )
    def test_does_not_for_other_places(self, location: str | None) -> None:
        assert not mentions_city(location, "Bengaluru")

    def test_works_for_any_canonical_city(self) -> None:
        assert mentions_city("Gurgaon, India", "Gurugram")
        assert not mentions_city("Gurgaon, India", "Bengaluru")
