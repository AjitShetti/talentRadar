"""
tests/test_company_map_domain.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pure rules behind the company map: where a company is, and how it is
classified. No database, no network.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from domain.areas import (
    BENGALURU_AREAS,
    area_for_point,
    area_for_text,
    in_bengaluru,
    jitter,
)
from domain.company_taxonomy import (
    SECTORS,
    STAGES,
    is_bigger_company,
    sector_from_text,
    size_bucket,
    stage_from_round,
    tier_for_import,
)

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "companies" / "bengaluru.json"


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _centroid(name: str) -> tuple[float, float]:
    area = next(a for a in BENGALURU_AREAS if a.name == name)
    return area.lat, area.lng


class TestAreas:
    def test_sector_suffix_resolves_to_its_layout(self):
        assert area_for_text("HSR Sector 3, Bengaluru") == "HSR Layout"

    def test_block_suffix_resolves_to_its_area(self):
        assert area_for_text("Koramangala 6th Block") == "Koramangala"

    def test_longest_alias_wins(self):
        # The address names the tech park's locality and the corridor; the
        # locality is the more specific of the two.
        assert (
            area_for_text("Prestige Tech Park, Kadubeesanahalli, Marathahalli-Sarjapur ORR")
            == "Bellandur"
        )

    def test_city_only_or_foreign_text_has_no_area(self):
        assert area_for_text("Bengaluru, Karnataka") is None
        assert area_for_text("Mumbai") is None
        assert area_for_text(None) is None

    def test_point_at_a_centroid_is_that_area(self):
        assert area_for_point(*_centroid("HSR Layout")) == "HSR Layout"

    def test_point_far_from_every_centroid_has_no_area(self):
        assert area_for_point(19.07, 72.88) is None

    def test_bbox(self):
        assert in_bengaluru(12.97, 77.59)
        assert not in_bengaluru(19.07, 72.88)

    def test_every_centroid_is_inside_the_bbox(self):
        for area in BENGALURU_AREAS:
            assert in_bengaluru(area.lat, area.lng), area.name

    def test_jitter_is_deterministic_and_bounded(self):
        first = jitter("company-abc", 12.9, 77.6)
        assert first == jitter("company-abc", 12.9, 77.6)
        assert first != jitter("company-xyz", 12.9, 77.6)
        distance_m = _km((12.9, 77.6), first) * 1000
        assert 150 <= distance_m <= 450


class TestTaxonomy:
    @pytest.mark.parametrize(
        ("text", "stage"),
        [
            ("Series B", "series_b"),
            ("Series D", "series_c_plus"),
            ("pre-seed", "pre_seed"),
            ("Pre-Series A", "seed"),
            ("seed round", "seed"),
            ("IPO", "public"),
            ("debt", None),
            (None, None),
        ],
    )
    def test_stage_from_round(self, text, stage):
        assert stage_from_round(text) == stage

    @pytest.mark.parametrize(
        ("text", "sector"),
        [
            ("Fintech & Payments", "fintech"),
            ("Developer tools", "devtools"),
            ("Edtech", "edtech"),
            ("IT Services & Consulting", "it_services"),
            ("Health Tech & Medical AI", "healthtech"),
            ("Conversational AI", "ai"),
            ("Food Delivery & Quick Commerce", "consumer"),
            ("Semiconductors & AI", "deeptech"),
            ("Automotive Retail SaaS", "saas"),
            ("Underwater basket weaving", None),
            (None, None),
        ],
    )
    def test_sector_from_text(self, text, sector):
        assert sector_from_text(text) == sector

    def test_every_catalogue_industry_maps_to_a_known_sector(self):
        records = json.loads(CATALOGUE.read_text(encoding="utf-8"))["companies"]
        unmapped = sorted(
            {r["industry"] for r in records if sector_from_text(r["industry"]) is None}
        )
        assert not unmapped, f"industries with no sector: {unmapped}"
        assert {sector_from_text(r["industry"]) for r in records} <= set(SECTORS)

    @pytest.mark.parametrize(
        ("count", "range_", "bucket"),
        [
            (None, "201-500", "201-1000"),
            (40, None, "1-50"),
            (None, "10000+", "1000+"),
            (None, "51-200", "51-200"),
            (1500, "11-50", "1000+"),  # a counted headcount beats a range
            (None, None, None),
        ],
    )
    def test_size_bucket(self, count, range_, bucket):
        assert size_bucket(count, range_) == bucket

    def test_tier_for_import(self):
        assert tier_for_import(is_vc=True, founded_year=1990, is_public=False) == "vc"
        assert tier_for_import(is_vc=False, founded_year=2015, is_public=False) == "startup"
        assert tier_for_import(is_vc=False, founded_year=2015, is_public=True) is None
        assert tier_for_import(is_vc=False, founded_year=None, is_public=False) is None
        assert tier_for_import(is_vc=False, founded_year=1995, is_public=False) is None

    def test_bigger_company(self):
        assert is_bigger_company("gcc", None, None)
        assert is_bigger_company(None, 250, None)
        assert is_bigger_company(None, None, "501-1000")
        assert not is_bigger_company("startup", None, "11-50")
        assert not is_bigger_company(None, None, None)

    def test_vocabularies_have_labels(self):
        assert STAGES["series_c_plus"] == "Series C+"
        assert SECTORS["it_services"] == "IT services"
