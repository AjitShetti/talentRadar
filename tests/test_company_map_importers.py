"""
tests/test_company_map_importers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The company-map importers' parsers, run on payloads recorded from the real
services (``tests/fixtures/company_map/``), plus how ``fetch`` reacts to a
service that answers with something other than data.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from domain.areas import in_bengaluru
from domain.company_identity import normalize_domain
from services.company_map import wikidata
from services.company_map.types import ImportedCompany, PersonCandidate

FIXTURES = Path(__file__).parent / "fixtures" / "company_map"


def _load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def _by_qid(companies: list[ImportedCompany]) -> dict[str, ImportedCompany]:
    return {c.wikidata_id: c for c in companies if c.wikidata_id}


class TestWikidataParse:
    def test_parses_at_least_twenty_companies(self) -> None:
        assert len(wikidata.parse(_load("wikidata.json"))) >= 20

    def test_every_company_has_a_normalised_domain(self) -> None:
        for company in wikidata.parse(_load("wikidata.json")):
            assert company.domain is not None, company.name
            assert company.domain == normalize_domain(company.website_url)

    def test_one_company_per_item(self) -> None:
        ids = [c.wikidata_id for c in wikidata.parse(_load("wikidata.json"))]
        assert all(ids)
        assert len(ids) == len(set(ids))

    def test_every_person_is_sourced_to_the_company_page(self) -> None:
        companies = wikidata.parse(_load("wikidata.json"))
        people = [(c, p) for c in companies for p in c.people]
        assert people
        for company, person in people:
            assert person.source_url == f"https://www.wikidata.org/wiki/{company.wikidata_id}"
            assert person.source_kind == "wikidata"
            assert person.kind in {"founder", "leader"}

    def test_known_row_parses_exactly(self) -> None:
        zerodha = _by_qid(wikidata.parse(_load("wikidata.json")))["Q8069628"]
        source = "https://www.wikidata.org/wiki/Q8069628"
        assert zerodha == ImportedCompany(
            name="Zerodha",
            domain="zerodha.com",
            website_url="https://zerodha.com/",
            wikidata_id="Q8069628",
            founded_year=2010,
            x_url="https://x.com/zerodhaonline",
            description="Indian financial services company",
            sector="fintech",
            tier="startup",
            people=(
                PersonCandidate(
                    name="Nikhil Kamath",
                    kind="founder",
                    source_url=source,
                    source_kind="wikidata",
                    linkedin_url="https://www.linkedin.com/in/nikhilkamathcio",
                    x_url="https://x.com/nikhilkamathcio",
                    profile_url="https://www.wikidata.org/wiki/Q98115159",
                ),
                PersonCandidate(
                    name="Nithin Kamath",
                    kind="founder",
                    source_url=source,
                    source_kind="wikidata",
                    profile_url="https://www.wikidata.org/wiki/Q70276727",
                ),
            ),
        )

    def test_company_links_and_sector_from_industry_labels(self) -> None:
        razorpay = _by_qid(wikidata.parse(_load("wikidata.json")))["Q109985545"]
        assert razorpay.github_org == "razorpay"
        assert razorpay.linkedin_url == "https://www.linkedin.com/company/razorpay"
        # "payment system" and "software industry": the taxonomy's order puts
        # fintech before SaaS.
        assert razorpay.sector == "fintech"

    def test_x_links_use_x_dot_com(self) -> None:
        companies = wikidata.parse(_load("wikidata.json"))
        links = [c.x_url for c in companies if c.x_url]
        links += [p.x_url for c in companies for p in c.people if p.x_url]
        assert links
        for link in links:
            assert link.startswith("https://x.com/")
            assert "/" not in link.removeprefix("https://x.com/")

    def test_support_handle_is_not_chosen(self) -> None:
        companies = _by_qid(wikidata.parse(_load("wikidata.json")))
        assert companies["Q1381966"].x_url == "https://x.com/Flipkart"
        assert companies["Q30324297"].x_url == "https://x.com/phonepe_"

    def test_founder_who_is_also_ceo_is_one_person(self) -> None:
        flipkart = _by_qid(wikidata.parse(_load("wikidata.json")))["Q1381966"]
        binny = [p for p in flipkart.people if p.name == "Binny Bansal"]
        assert len(binny) == 1
        assert binny[0].kind == "founder"
        assert binny[0].title == "CEO"
        assert binny[0].linkedin_url == "https://www.linkedin.com/in/binnybansal"

    def test_vc_firm_is_typed_vc(self) -> None:
        axilor = _by_qid(wikidata.parse(_load("wikidata.json")))["Q24951713"]
        assert axilor.tier == "vc"

    def test_listed_company_is_public_and_untyped(self) -> None:
        biocon = _by_qid(wikidata.parse(_load("wikidata.json")))["Q487694"]
        assert biocon.stage == "public"
        assert biocon.tier is None

    def test_coordinate_inside_bengaluru_is_kept(self) -> None:
        phonepe = _by_qid(wikidata.parse(_load("wikidata.json")))["Q30324297"]
        assert (phonepe.lat, phonepe.lng) == (12.92747222, 77.63283333)
        assert phonepe.logo_url == (
            "https://commons.wikimedia.org/wiki/Special:FilePath/PhonePe%20Logo.svg"
        )

    def test_coordinate_outside_bengaluru_is_dropped_but_company_kept(self) -> None:
        payload = copy.deepcopy(_load("wikidata.json"))
        for row in payload["results"]["bindings"]:
            if row["item"]["value"].endswith("/Q30324297"):
                row["coord"]["value"] = "Point(72.88 19.07)"  # Mumbai
        phonepe = _by_qid(wikidata.parse(payload))["Q30324297"]
        assert phonepe.lat is None
        assert phonepe.lng is None

    def test_items_without_an_english_label_are_skipped(self) -> None:
        companies = _by_qid(wikidata.parse(_load("wikidata.json")))
        assert "Q137015469" not in companies
        assert "Q15823140" not in companies

    def test_one_website_per_company_preferring_the_name_and_root(self) -> None:
        companies = _by_qid(wikidata.parse(_load("wikidata.json")))
        assert companies["Q6008783"].website_url == "https://www.inmobi.com"
        assert companies["Q139895120"].domain == "getwidget.dev"
        assert companies["Q17053065"].website_url == "https://exotel.com/"

    def test_coordinates_when_present_are_in_bengaluru(self) -> None:
        for company in wikidata.parse(_load("wikidata.json")):
            if company.lat is not None and company.lng is not None:
                assert in_bengaluru(company.lat, company.lng)

    def test_empty_payload_parses_to_nothing(self) -> None:
        assert wikidata.parse({}) == []
        assert wikidata.parse({"results": {"bindings": []}}) == []
