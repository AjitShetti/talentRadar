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

import httpx
import pytest

from domain.areas import in_bengaluru
from domain.company_identity import normalize_domain
from services.company_map import osm, wikidata
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
            industry_labels=("financial services",),
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

    def test_industry_labels_are_kept_for_the_tech_verdict(self) -> None:
        razorpay = _by_qid(wikidata.parse(_load("wikidata.json")))["Q109985545"]
        assert razorpay.industry_labels == ("payment system", "software industry")

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


def _elements() -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = _load("overpass.json")["elements"]
    return elements


class TestOsmParse:
    def test_parses_at_least_fifty_offices(self) -> None:
        assert len(osm.parse(_load("overpass.json"))) >= 50

    def test_every_office_is_pinned_in_bengaluru_with_a_domain(self) -> None:
        for company in osm.parse(_load("overpass.json")):
            assert company.lat is not None and company.lng is not None, company.name
            assert in_bengaluru(company.lat, company.lng), company.name
            assert company.domain is not None, company.name
            assert company.domain == normalize_domain(company.website_url)

    def test_a_way_is_pinned_at_its_center(self) -> None:
        way = next(e for e in _elements() if e["type"] == "way" and "website" in e["tags"])
        parsed = osm.parse({"elements": [way]})
        assert len(parsed) == 1
        assert (parsed[0].lat, parsed[0].lng) == (way["center"]["lat"], way["center"]["lon"])
        assert parsed[0].name == way["tags"]["name"]

    def test_an_office_without_a_website_is_skipped(self) -> None:
        bare = [e for e in _elements() if "website" not in e.get("tags", {})]
        assert bare, "the fixture keeps at least one office without a website"
        assert osm.parse({"elements": bare}) == []
        names = {c.name for c in osm.parse(_load("overpass.json"))}
        assert not names & {e["tags"]["name"] for e in bare}

    def test_a_point_outside_bengaluru_is_dropped(self) -> None:
        node = copy.deepcopy(next(e for e in _elements() if e["type"] == "node"))
        node["lat"], node["lon"] = 19.07, 72.88  # Mumbai
        assert osm.parse({"elements": [node]}) == []

    def test_address_comes_from_addr_tags(self) -> None:
        node = {
            "type": "node",
            "id": 1,
            "lat": 12.9116,
            "lon": 77.6389,
            "tags": {
                "office": "company",
                "name": "Acme",
                "website": "https://www.acme-labs.in/about",
                "addr:housenumber": "42",
                "addr:street": "27th Main Road",
                "addr:suburb": "HSR Layout",
                "addr:postcode": "560102",
            },
        }
        (acme,) = osm.parse({"elements": [node]})
        assert acme.address == "42, 27th Main Road, HSR Layout, 560102"
        assert acme.domain == "acme-labs.in"
        assert acme.website_url == "https://www.acme-labs.in/about"

    def test_an_it_office_says_so_and_a_plain_company_office_does_not(self) -> None:
        def node(office: str) -> dict[str, Any]:
            return {
                "type": "node",
                "id": 1,
                "lat": 12.9116,
                "lon": 77.6389,
                "tags": {"office": office, "name": "Acme", "website": "https://acme.example"},
            }

        (it_office,) = osm.parse({"elements": [node("it")]})
        (company,) = osm.parse({"elements": [node("company")]})
        assert it_office.industry_labels == ("information technology",)
        assert company.industry_labels == ()

    def test_payload_without_elements_parses_to_nothing(self) -> None:
        assert osm.parse({}) == []
        assert osm.parse({"remark": "runtime error: timeout"}) == []


_OVERPASS_BUSY = (
    "<?xml version='1.0' encoding='UTF-8'?>\n<!DOCTYPE html>\n<html><head>"
    "<title>OSM3S Response</title></head><body><p><strong>Error</strong>: "
    "runtime error: open64: 0 Success /osm3s_osm_base Dispatcher_Client::"
    "request_read_and_idx::timeout. The server is probably too busy to handle "
    "your request.</p></body></html>"
)


class TestOsmFetch:
    async def test_html_error_page_with_http_200_raises(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_OVERPASS_BUSY, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(osm.OverpassError, match="non-JSON"):
                await osm.fetch(client)
        assert seen == list(osm.ENDPOINTS)

    async def test_runtime_error_remark_raises(self) -> None:
        body = {"version": 0.6, "elements": [], "remark": "runtime error: Query timed out"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(osm.OverpassError, match="runtime error"):
                await osm.fetch(client)

    async def test_falls_through_to_the_next_instance(self) -> None:
        good = _load("overpass.json")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "overpass-api.de":
                return httpx.Response(200, text=_OVERPASS_BUSY)
            assert request.headers["user-agent"].startswith("TalentRadar/1.0")
            return httpx.Response(200, json=good)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await osm.fetch(client) == good
