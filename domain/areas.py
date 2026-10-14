"""
domain/areas.py
~~~~~~~~~~~~~~~
Bengaluru's neighbourhoods, as the company map uses them.

The single place that knows what "Koramangala" means: a canonical name, a
centroid, and the spellings an address or a careers page uses for it
("Koramangala 6th Block", "HSR Sector 3", "Kadubeesanahalli, ORR").

Centroids are OpenStreetMap ``place=suburb|neighbourhood|quarter`` nodes,
compiled once on 2026-10-14 and committed here; the node or way id is beside
each one. Where the area is a road or a tech park rather than a place, the
point is the stretch where its offices actually are, and says so. Nothing
here is fetched at runtime.

A company known only by its area is drawn at that area's centroid, nudged by
:func:`jitter` so a hundred HSR startups do not stack on one pixel. The map
marks those pins as approximate; see ``geo_precision`` on ``companies``.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

#: (south, west, north, east) — Bengaluru Urban with its tech corridors.
BENGALURU_BBOX: tuple[float, float, float, float] = (12.80, 77.40, 13.15, 77.80)

_EARTH_KM = 6371.0
_M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class Area:
    name: str
    lat: float
    lng: float
    aliases: tuple[str, ...] = ()


BENGALURU_AREAS: tuple[Area, ...] = (
    Area("HSR Layout", 12.91162, 77.63886, ("hsr", "hsr sector")),  # node 456016994
    Area("Koramangala", 12.93574, 77.62408, ("koramangla", "kormangala")),  # node 429918282
    Area(
        "Indiranagar",
        12.97329,
        77.64047,  # node 448991617
        ("indira nagar", "hal 2nd stage", "hal ii stage", "hal 3rd stage"),
    ),
    Area(
        "Bellandur",
        12.92921,
        77.67704,  # node 456016705 (Bellanduru)
        (
            "bellanduru",
            "outer ring road",
            "orr",
            "kadubeesanahalli",
            "kaadubeesanahalli",
            "devarabeesanahalli",
            "ecospace",
            "embassy tech village",
            "ambalipura",
        ),
    ),
    Area(
        "MG Road",
        12.97553,
        77.60679,  # node 6400857554, MG Road metro
        (
            "mahatma gandhi road",
            "m.g. road",
            "m g road",
            "brigade road",
            "residency road",
            "church street",
            "st. mark's road",
            "st marks road",
            "lavelle road",
            "ashok nagar",
            "cbd",
        ),
    ),
    Area(
        "Whitefield", 12.96964, 77.74974, ("itpl", "international tech park", "epip")
    ),  # node 377219489
    # Sarjapur Road is a road: the point is its Doddakannelli–Kaikondrahalli IT stretch.
    Area(
        "Sarjapur Road",
        12.90800,
        77.68700,
        ("sarjapura road", "sarjapur", "doddakannelli", "kaikondrahalli"),
    ),
    Area(
        "Electronic City", 12.84968, 77.66497, ("electronics city", "e-city", "ecity")
    ),  # node 459501900
    # Bannerghatta Road is a road: the point is its Arekere–Bilekahalli IT stretch.
    Area(
        "Bannerghatta Road",
        12.90000,
        77.60100,
        ("bannerghatta main road", "bg road", "arekere", "bilekahalli", "hulimavu"),
    ),
    Area(
        "Marathahalli",
        12.95526,
        77.69842,  # node 442068273
        ("marathalli", "munnekollal", "munnekolal", "aecs layout"),
    ),
    Area(
        "JP Nagar", 12.90969, 77.58661, ("j.p. nagar", "j p nagar", "puttenahalli")
    ),  # node 429853335
    Area("Jayanagar", 12.92927, 77.58242, ("jaya nagar",)),  # node 429853334
    Area("Domlur", 12.96247, 77.63820, ("domlur layout",)),  # node 347261886
    Area(
        "Mahadevapura",
        12.99124,
        77.68973,  # node 574853138
        ("doddanekkundi", "doddanakundi", "garudacharpalya"),
    ),
    Area("Manyata Tech Park", 13.04829, 77.62110, ("manyata", "nagawara")),  # way 34121900
    Area("Hebbal", 13.03822, 77.59190),  # node 529277252
    Area("Yeshwanthpur", 13.01769, 77.55550, ("yeshwantpur", "yesvantpur")),  # way 1238794971
    Area("BTM Layout", 12.91400, 77.61028, ("btm", "tavarekere")),  # node 447032046
    Area("Ulsoor", 12.97788, 77.62467, ("halasuru",)),  # node 1453825863
    Area("Malleshwaram", 13.00274, 77.57033, ("malleswaram",)),  # node 427595936
    Area("Yelahanka", 13.10070, 77.59635),  # node 430143744
    Area("Banashankari", 12.92687, 77.56372),  # node 429853336
    Area(
        "Basavanagudi", 12.94173, 77.57550, ("vani vilas road", "gandhi bazaar")
    ),  # node 429937956
    Area("Rajajinagar", 12.98823, 77.55488),  # node 429857523
    Area("Peenya", 13.03294, 77.52733),  # node 3305850521
    Area("Frazer Town", 12.99759, 77.61367, ("cox town",)),  # node 430051517
    Area("Kalyan Nagar", 13.02214, 77.64034, ("kalyananagar",)),  # node 567772575
    Area(
        "Bommanahalli",
        12.90895,
        77.62390,  # node 447032045
        ("garvebhavipalya", "singasandra", "roopena agrahara"),
    ),
    Area(
        "Old Airport Road",
        12.95973,
        77.64918,  # way 1418246132
        ("hal airport road", "murugeshpalya", "murugesh pallya", "kodihalli"),
    ),
    Area(
        "Richmond Town", 12.96355, 77.60159, ("richmond road", "langford town", "langford road")
    ),  # node 5164714917
    Area("Shivajinagar", 12.98553, 77.60545, ("infantry road",)),  # node 429303186
    Area("Vasanth Nagar", 12.99217, 77.59146, ("cunningham road",)),  # node 461755226
    Area("Sadashivanagar", 13.01102, 77.58086),  # node 461539035
    Area("RT Nagar", 13.02272, 77.59571, ("r.t. nagar", "r t nagar")),  # node 430043651
    Area("Banaswadi", 13.01416, 77.65185),  # node 443807287
    Area("HBR Layout", 13.03199, 77.62808, ("hbr",)),  # node 4531677090
    Area("Hoodi", 12.99190, 77.71620),  # node 1457440304
    Area("Brookefield", 12.96521, 77.71794, ("kundalahalli",)),  # node 378717492
    Area("Varthur", 12.94065, 77.74699, ("gunjur",)),  # node 833667967
    Area("Kudlu", 12.88891, 77.65557, ("kudlu gate",)),  # node 3305446529
    Area("Begur", 12.87475, 77.62273, ("beguru",)),  # node 3336377110
    Area("Hennur", 13.03769, 77.64329),  # node 595688375 (Hennur Gardens)
    Area("Ejipura", 12.94524, 77.62748),  # node 429921855
    Area("Wilson Garden", 12.94893, 77.59683),  # node 429921851
    Area("Kammanahalli", 13.01485, 77.63820),  # node 6330202097
    Area("Thippasandra", 12.97394, 77.65100, ("new thippasandra",)),  # node 3298378275
    Area("Uttarahalli", 12.90557, 77.54554),  # node 573534505
    Area("Jalahalli", 13.04645, 77.54838),  # node 433748247
)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")


# Every alias (the canonical name included), longest first, so the most
# specific spelling in an address decides: "kadubeesanahalli" beats "orr".
_ALIASES: tuple[tuple[re.Pattern[str], int, str], ...] = tuple(
    sorted(
        (
            (_alias_pattern(alias), len(alias), area.name)
            for area in BENGALURU_AREAS
            for alias in {area.name.lower(), *area.aliases}
        ),
        key=lambda item: -item[1],
    )
)


def in_bengaluru(lat: float, lng: float) -> bool:
    south, west, north, east = BENGALURU_BBOX
    return south <= lat <= north and west <= lng <= east


def area_for_text(text: str | None) -> str | None:
    """The canonical area an address or locality string names, if any."""
    if not text:
        return None
    lowered = text.lower()
    for pattern, _length, name in _ALIASES:
        if pattern.search(lowered):
            return name
    return None


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def area_for_point(lat: float, lng: float, max_km: float = 2.5) -> str | None:
    """The nearest area centroid within ``max_km``, or ``None``."""
    if not in_bengaluru(lat, lng):
        return None
    best = min(BENGALURU_AREAS, key=lambda a: _km(lat, lng, a.lat, a.lng))
    return best.name if _km(lat, lng, best.lat, best.lng) <= max_km else None


def centroid(name: str) -> tuple[float, float] | None:
    for area in BENGALURU_AREAS:
        if area.name == name:
            return area.lat, area.lng
    return None


def jitter(key: str, lat: float, lng: float) -> tuple[float, float]:
    """
    A point 150–450 m from ``(lat, lng)``, fixed by ``key``.

    Deterministic so a company's approximate pin does not wander between page
    loads, and offset so pins sharing a centroid separate at street zoom.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    bearing = math.radians(int.from_bytes(digest[:2], "big") / 65535 * 360)
    metres = 150 + int.from_bytes(digest[2:4], "big") / 65535 * 300
    dlat = metres * math.cos(bearing) / _M_PER_DEG_LAT
    dlng = metres * math.sin(bearing) / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return round(lat + dlat, 6), round(lng + dlng, 6)
