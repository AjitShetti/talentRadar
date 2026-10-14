"""
tests/test_company_map_geocode.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Where a company is, from Nominatim or from an area name, and how much of
that position to believe. No network: payloads are hand-written in the shape
Nominatim's ``format=jsonv2`` returns (lat/lon as strings), plus one recorded
postcode response, and HTTP goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from domain.areas import centroid, in_bengaluru, jitter
from services.company_map import geocode as geocode_module
from services.company_map.geocode import geocode, parse_nominatim, resolve_location
from services.company_map.types import GeoHit

FIXTURES = Path(__file__).parent / "fixtures" / "company_map"


def _hit(
    lat: float,
    lon: float,
    *,
    category: str = "place",
    type_: str = "house",
    name: str = "",
) -> dict[str, Any]:
    return {
        "lat": str(lat),
        "lon": str(lon),
        "category": category,
        "type": type_,
        "name": name,
        "display_name": f"{name}, Bengaluru, Karnataka, India",
    }


# ---------------------------------------------------------------- parse_nominatim


def test_point_outside_bbox_is_rejected() -> None:
    # A same-named office in Mumbai: well-formed, matching, and not Bengaluru.
    mumbai = _hit(19.0760, 72.8777, category="office", type_="company", name="Razorpay")
    for mode in ("address", "postcode", "name"):
        assert parse_nominatim([mumbai], mode=mode, name="Razorpay") is None


def test_outside_hit_is_skipped_for_a_later_inside_one() -> None:
    mumbai = _hit(19.0760, 72.8777)
    koramangala = _hit(12.9352, 77.6245)
    hit = parse_nominatim([mumbai, koramangala], mode="address")
    assert hit is not None
    assert in_bengaluru(hit.lat, hit.lng)


def test_address_mode_gives_street_precision() -> None:
    hit = parse_nominatim([_hit(12.9352, 77.6245, type_="residential")], mode="address")
    assert hit == GeoHit(
        lat=12.9352, lng=77.6245, precision="street", source="nominatim", area="Koramangala"
    )


def test_address_mode_far_from_any_area_has_no_area() -> None:
    # Inside the bbox but nowhere near a known neighbourhood.
    hit = parse_nominatim([_hit(12.82, 77.42)], mode="address")
    assert hit is not None
    assert hit.precision == "street"
    assert hit.area is None


def test_empty_or_malformed_payload_is_none() -> None:
    assert parse_nominatim([], mode="address") is None
    assert parse_nominatim([{"lat": "n/a", "lon": "77.6"}], mode="address") is None
    assert parse_nominatim([{"display_name": "no coordinates"}], mode="address") is None


def test_name_mode_accepts_an_office_with_a_matching_name() -> None:
    office = _hit(12.9279, 77.6271, category="office", type_="company", name="Razorpay")
    hit = parse_nominatim([office], mode="name", name="razorpay")
    assert hit is not None
    assert hit.precision == "exact"
    assert hit.source == "nominatim"
    assert hit.area == "Koramangala"


def test_name_mode_rejects_a_shop() -> None:
    shop = _hit(12.9279, 77.6271, category="shop", type_="clothes", name="Razorpay")
    assert parse_nominatim([shop], mode="name", name="Razorpay") is None


def test_name_mode_rejects_a_low_name_similarity() -> None:
    office = _hit(12.9279, 77.6271, category="office", type_="company", name="Razor Pay Café")
    assert parse_nominatim([office], mode="name", name="Razorpay") is None


def test_name_mode_reads_the_class_key_of_plain_json() -> None:
    # format=json says "class" where jsonv2 says "category".
    office = _hit(12.9279, 77.6271, name="Razorpay")
    del office["category"]
    office["class"] = "building"
    hit = parse_nominatim([office], mode="name", name="Razorpay")
    assert hit is not None
    assert hit.precision == "exact"


def test_name_mode_without_a_query_name_is_none() -> None:
    office = _hit(12.9279, 77.6271, category="office", type_="company", name="Razorpay")
    assert parse_nominatim([office], mode="name") is None


def test_postcode_mode_maps_to_an_area_name() -> None:
    payload = json.loads((FIXTURES / "nominatim_postcode_560102.json").read_text("utf-8"))
    hit = parse_nominatim(payload, mode="postcode")
    assert hit is not None
    assert hit.area == "HSR Layout"
    assert hit.precision == "area"
    assert hit.source == "pincode"
    # The area's centroid itself; jitter is the caller's job.
    assert (hit.lat, hit.lng) == centroid("HSR Layout")


def test_postcode_mode_with_no_area_nearby_is_none() -> None:
    assert parse_nominatim([_hit(12.82, 77.42)], mode="postcode") is None


def test_unknown_mode_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        parse_nominatim([_hit(12.9352, 77.6245)], mode="city")


# ---------------------------------------------------------------- geocode


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(geocode_module.asyncio, "sleep", fake_sleep)
    return calls


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_geocode_sends_an_identifying_bounded_request_and_sleeps(
    sleeps: list[float],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_hit(12.9352, 77.6245)])

    async with _client(handler) as client:
        hit = await geocode(client, address="80 Feet Road, Koramangala, Bengaluru")

    assert hit is not None and hit.precision == "street"
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "nominatim.openstreetmap.org"
    assert request.url.path == "/search"
    assert request.headers["User-Agent"] == (
        "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
    )
    params = request.url.params
    assert params["q"] == "80 Feet Road, Koramangala, Bengaluru"
    assert params["format"] == "jsonv2"
    assert params["bounded"] == "1"
    assert params["countrycodes"] == "in"
    assert params["limit"] == "5"
    assert params["viewbox"] == "77.4,13.15,77.8,12.8"
    assert sleeps == [1.0]


async def test_geocode_uses_only_the_first_input_given(sleeps: list[float]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_hit(12.9151454, 77.6505131, category="boundary")])

    async with _client(handler) as client:
        hit = await geocode(client, postcode="560102", name="Razorpay")

    assert len(seen) == 1
    assert seen[0].url.params["postalcode"] == "560102"
    assert "q" not in seen[0].url.params
    assert hit is not None and hit.area == "HSR Layout"
    assert sleeps == [1.0]


async def test_geocode_name_search(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "Razorpay"
        office = _hit(12.9279, 77.6271, category="office", type_="company", name="Razorpay")
        return httpx.Response(200, json=[office])

    async with _client(handler) as client:
        hit = await geocode(client, name="Razorpay")

    assert hit is not None and hit.precision == "exact"


async def test_geocode_http_error_is_none_and_still_sleeps(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    async with _client(handler) as client:
        assert await geocode(client, address="Somewhere, Bengaluru") is None
    assert sleeps == [1.0]


async def test_geocode_transport_error_or_bad_json_is_none(sleeps: list[float]) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    def not_a_list(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "Unable to geocode"})

    for handler in (broken, not_json, not_a_list):
        async with _client(handler) as client:
            assert await geocode(client, address="Somewhere, Bengaluru") is None
    assert sleeps == [1.0, 1.0, 1.0]


async def test_geocode_with_nothing_to_look_up_makes_no_request(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with _client(handler) as client:
        assert await geocode(client) is None
        assert await geocode(client, address="  ") is None
    assert sleeps == []


# ---------------------------------------------------------------- resolve_location


def test_resolve_location_returns_a_known_hit() -> None:
    known = GeoHit(lat=12.97, lng=77.64, precision="exact", source="wikidata")
    assert resolve_location("c-1", known=known, area_hint="HSR Layout") is known


def test_resolve_location_from_an_area_hint() -> None:
    hit = resolve_location("c-1", known=None, area_hint="HSR Layout")
    assert hit is not None
    assert hit.precision == "area"
    assert hit.source == "area"
    assert hit.area == "HSR Layout"
    hsr = centroid("HSR Layout")
    assert hsr is not None
    assert (hit.lat, hit.lng) == jitter("c-1", *hsr)
    # Deterministic per company, distinct between companies.
    assert resolve_location("c-1", known=None, area_hint="HSR Layout") == hit
    other = resolve_location("c-2", known=None, area_hint="HSR Layout")
    assert other is not None and (other.lat, other.lng) != (hit.lat, hit.lng)


def test_resolve_location_reads_free_text_hints() -> None:
    hit = resolve_location("c-1", known=None, area_hint="5th Floor, Kadubeesanahalli, ORR")
    assert hit is not None
    assert hit.area == "Bellandur"


def test_resolve_location_with_nothing_is_none() -> None:
    assert resolve_location("c-1", known=None, area_hint=None) is None
    assert resolve_location("c-1", known=None, area_hint="Pune") is None
