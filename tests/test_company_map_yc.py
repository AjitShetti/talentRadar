"""
tests/test_company_map_yc.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Y Combinator directory importer, run on a trimmed recording of the real
mirror (``tests/fixtures/company_map/yc.json``, 15 of its 6,300 entries).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from domain.company_identity import normalize_domain
from services.company_map import yc
from services.company_map.sourced import SourcedCompany

FIXTURES = Path(__file__).parent / "fixtures" / "company_map"


def _payload() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / "yc.json").read_text(encoding="utf-8"))
    return data


def _entry(slug: str) -> dict[str, Any]:
    return copy.deepcopy(next(e for e in _payload() if e["slug"] == slug))


def _by_name(companies: list[SourcedCompany]) -> dict[str, SourcedCompany]:
    return {c.name: c for c in companies}


class TestFixture:
    def test_is_the_recorded_shape(self) -> None:
        payload = _payload()
        assert len(payload) == 15
        statuses = {e["status"] for e in payload}
        assert {"Active", "Inactive", "Acquired", "Public"} <= statuses
        assert sum("Bengaluru" in (e["all_locations"] or "") for e in payload) == 12
        assert any(";" in e["all_locations"] and "Bengaluru" in e["all_locations"] for e in payload)


class TestParse:
    def test_keeps_only_active_bengaluru_companies_with_a_site_of_their_own(self) -> None:
        names = {c.name for c in yc.parse_yc(_payload())}
        assert names == {
            "Razorpay",
            "Meesho",
            "Scribe",
            "Supr Daily",
            "OneFin",
            "100x",
            "Statiq.",
            "Dockup",
        }

    def test_other_cities_are_dropped(self) -> None:
        names = {c.name for c in yc.parse_yc(_payload())}
        assert not names & {"CircuitHub", "Craze", "Medplum"}

    def test_inactive_companies_are_dropped(self) -> None:
        names = {c.name for c in yc.parse_yc(_payload())}
        assert not names & {"Markupwand", "Drivezy", "Chutney Inc"}

    def test_a_company_whose_only_site_is_somebody_elses_page_has_no_identity(self) -> None:
        # RecordBook is Bengaluru and Active, but its "website" is a Play Store listing.
        assert "RecordBook" not in {c.name for c in yc.parse_yc(_payload())}

    def test_a_missing_website_is_skipped(self) -> None:
        entry = _entry("onefin")
        entry["website"] = ""
        assert yc.parse_yc([entry]) == []
        entry["website"] = None
        assert yc.parse_yc([entry]) == []
        del entry["website"]
        assert yc.parse_yc([entry]) == []

    def test_dead_is_dropped_like_inactive(self) -> None:
        entry = _entry("onefin")
        entry["status"] = "Dead"
        assert yc.parse_yc([entry]) == []
        entry["status"] = "inactive"
        assert yc.parse_yc([entry]) == []

    def test_the_old_spelling_of_the_city_counts(self) -> None:
        entry = _entry("onefin")
        entry["all_locations"] = "Bangalore, Karnataka, India"
        assert [c.name for c in yc.parse_yc([entry])] == ["OneFin"]

    def test_a_company_with_several_offices_is_kept_once(self) -> None:
        companies = yc.parse_yc(_payload())
        assert [c.name for c in companies].count("Statiq.") == 1
        assert [c.name for c in companies].count("Dockup") == 1

    def test_a_foreign_place_that_shares_a_locality_name_is_not_bengaluru(self) -> None:
        entry = _entry("onefin")
        entry["all_locations"] = "Whitefield, NH, USA"
        assert yc.parse_yc([entry]) == []

    @pytest.mark.parametrize("junk", [None, {}, "x", 3, [None, "x", 1, []]])
    def test_junk_payloads_are_empty_not_errors(self, junk: Any) -> None:
        assert yc.parse_yc(junk) == []

    def test_a_missing_locations_field_is_not_an_error(self) -> None:
        entry = _entry("onefin")
        entry["all_locations"] = None
        assert yc.parse_yc([entry]) == []

    def test_every_company_has_the_domain_of_its_site(self) -> None:
        for company in yc.parse_yc(_payload()):
            assert company.domain is not None
            assert company.domain == normalize_domain(company.website_url)


class TestFields:
    def test_a_known_row_maps_exactly(self) -> None:
        razorpay = _by_name(yc.parse_yc(_payload()))["Razorpay"]
        assert razorpay.domain == "razorpay.com"
        assert razorpay.website_url == "https://razorpay.com"
        assert razorpay.sector == "fintech"
        assert razorpay.employee_count == 2700
        assert razorpay.description == (
            "India's only full-stack financial solutions company for businesses."
        )  # the one-liner
        assert razorpay.source_metadata == {"yc_batch": "Winter 2015"}
        assert razorpay.wikidata_id is None
        assert razorpay.lat is None and razorpay.lng is None
        assert razorpay.people == ()

    def test_stage_is_seed_for_an_early_company(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["OneFin"].stage == "seed"
        assert companies["Dockup"].stage == "seed"

    def test_stage_follows_a_public_or_acquired_status(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["Meesho"].stage == "public"
        assert companies["Scribe"].stage == "acquired"

    def test_a_growth_company_is_not_called_seed(self) -> None:
        # YC files Razorpay under "Growth": claiming "Seed" would be wrong, and
        # saying nothing leaves the stage to funding news.
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["Razorpay"].stage is None
        assert companies["Statiq."].stage is None

    def test_sector_comes_from_industries_and_tags(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["Meesho"].sector == "consumer"
        assert companies["Razorpay"].sector == "fintech"

    def test_size_is_the_team_size_and_unknown_stays_unknown(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["OneFin"].employee_count == 50
        assert companies["Supr Daily"].employee_count is None

    @pytest.mark.parametrize("size", [0, -3, "many", None])
    def test_an_unusable_team_size_is_ignored(self, size: Any) -> None:
        entry = _entry("onefin")
        entry["team_size"] = size
        assert yc.parse_yc([entry])[0].employee_count is None

    def test_tracking_parameters_are_cut_from_the_website(self) -> None:
        hundred_x = _by_name(yc.parse_yc(_payload()))["100x"]
        assert hundred_x.website_url == "https://100x.bot"
        assert hundred_x.domain == "100x.bot"

    def test_a_site_without_a_scheme_gets_one(self) -> None:
        entry = _entry("onefin")
        entry["website"] = "www.onefin.in"
        company = yc.parse_yc([entry])[0]
        assert company.website_url == "https://www.onefin.in"
        assert company.domain == "onefin.in"

    def test_the_batch_is_kept_for_the_row_to_show(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["OneFin"].source_metadata == {"yc_batch": "Winter 2017"}
        assert companies["100x"].source_metadata == {"yc_batch": "Summer 2022"}
        assert all("yc_batch" in c.source_metadata for c in companies.values())

    def test_a_company_with_no_one_liner_falls_back_to_the_long_description(self) -> None:
        entry = _entry("onefin")
        entry["one_liner"] = ""
        assert yc.parse_yc([entry])[0].description == entry["long_description"]
        entry["long_description"] = ""
        assert yc.parse_yc([entry])[0].description is None

    def test_a_startup_batch_reads_as_a_startup(self) -> None:
        companies = _by_name(yc.parse_yc(_payload()))
        assert companies["OneFin"].tier == "startup"
        assert companies["Meesho"].tier is None  # listed


class TestScan:
    def test_counts_the_bengaluru_rows_it_could_not_use(self) -> None:
        companies, skipped = yc.scan(_payload())
        assert len(companies) == 8
        # RecordBook (a Play Store link) is the only active Bengaluru row without a domain.
        assert skipped == 1

    def test_inactive_and_other_cities_are_filtered_not_skipped(self) -> None:
        entry = _entry("markupwand")
        assert yc.scan([entry]) == ([], 0)
        assert yc.scan([_entry("circuithub")]) == ([], 0)


class TestFetch:
    async def test_returns_the_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == yc.URL
            return httpx.Response(200, json=_payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert len(await yc.fetch(client)) == 15

    async def test_an_http_error_raises(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await yc.fetch(client)

    async def test_a_body_that_is_not_a_list_raises(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "x"}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="not a list"):
                await yc.fetch(client)

    async def test_a_moved_mirror_is_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "yc-oss.github.io":
                return httpx.Response(301, headers={"Location": "https://example.test/all.json"})
            return httpx.Response(200, json=_payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert len(await yc.fetch(client)) == 15
