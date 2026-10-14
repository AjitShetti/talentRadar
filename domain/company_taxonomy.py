"""
domain/company_taxonomy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The vocabularies the company map filters on, and the rules that map what a
source says onto them.

* **Stage** — how far a company has raised. Moves with dated funding news.
* **Sector** — what it does. Derived from the curated ``industry`` string, a
  Wikidata industry *label*, or failing both the summary, by ordered keyword
  rules. Labels rather than item ids, so no id table has to be kept right.
* **Size** — a headcount bucket.
* **Type** is the existing ``companies.tier`` (``services/companies.py``),
  plus ``vc``.

Everything here is pure; a wrong mapping is a one-line table change.
"""

from __future__ import annotations

import re

STAGES: dict[str, str] = {
    "bootstrapped": "Bootstrapped",
    "pre_seed": "Pre-seed",
    "seed": "Seed",
    "series_a": "Series A",
    "series_b": "Series B",
    "series_c_plus": "Series C+",
    "public": "Public",
    "acquired": "Acquired",
}

SECTORS: dict[str, str] = {
    "ai": "AI",
    "saas": "SaaS",
    "fintech": "Fintech",
    "consumer": "Consumer",
    "d2c": "D2C",
    "deeptech": "Deeptech",
    "edtech": "Edtech",
    "healthtech": "Healthtech",
    "logistics": "Logistics & mobility",
    "gaming": "Gaming",
    "devtools": "Developer tools",
    "it_services": "IT services",
    "other": "Other",
}

SIZES: dict[str, str] = {
    "1-50": "1–50",
    "51-200": "51–200",
    "201-1000": "201–1,000",
    "1000+": "1,000+",
}

#: Types whose companies are big enough for the drawer to lead with
#: headcount, leadership and LinkedIn searches rather than founders.
_BIG_TIERS = frozenset({"big_tech", "gcc", "unicorn", "services"})

#: A company founded this year or later, not listed, reads as a startup.
_STARTUP_SINCE = 2008


def _rule(words: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z])(?:{words})", re.IGNORECASE)


# Ordered: the first matching rule wins. Domain-specific sectors come before
# the generic ones, so "Health Tech & Medical AI" is healthtech, not AI, and
# "Fintech & Tax SaaS" is fintech, not SaaS. An explicit "SaaS" beats the
# hardware and consumer words ("Automotive Retail SaaS" sells software).
_SECTOR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "fintech",
        _rule(
            r"fintech|payment|banking|bank\b|lending|neobank|insurtech|insurance|reinsurance|"
            r"wealth|investing|investment|stockbrok|credit|bnpl|financial|crypto|blockchain|"
            r"capital markets|spend management|expense|accounting|corporate cards|savings"
        ),
    ),
    ("it_services", _rule(r"services|consult|agency|studio|outsourc|product engineering")),
    ("edtech", _rule(r"edtech|education|learning|study abroad")),
    ("healthtech", _rule(r"health|medical|biotech|diagnostic|wellness|fitness|pharma|cardiac")),
    ("gaming", _rule(r"gaming|games?\b")),
    ("d2c", _rule(r"d2c|direct[- ]to[- ]consumer")),
    ("saas", _rule(r"saas\b")),
    ("logistics", _rule(r"logistics|shipping|supply[- ]chain|freight|fleet|mobility")),
    (
        "deeptech",
        _rule(
            r"semiconductor|aerospace|space tech|battery|robotic|hardware|automotive|industrial|"
            r"\beda\b|energy|ev charging|electric vehicle|telecom equipment|telecom infrastructure|"
            r"drone|manufactur|materials|electronics|aviation|agritech|deeptech"
        ),
    ),
    ("ai", _rule(r"ai\b|artificial intelligence|machine learning|generative|llm|computer vision")),
    (
        "devtools",
        _rule(
            r"developer|devops|observability|database|open source|api\b|apis\b|incident|"
            r"artifact|data streaming"
        ),
    ),
    (
        "consumer",
        _rule(
            r"e-?commerce|consumer|quick commerce|grocery|food|travel|social|media|streaming|"
            r"entertainment|rental|fashion|beauty|interiors|furniture|super app|proptech|jobs|"
            r"professional network|internet|audio|marketplace|commerce"
        ),
    ),
    (
        "saas",
        _rule(
            r"software|platform|cloud|crm|enterprise|resource planning|security|cyber|data|"
            r"analytics|marketing|adtech|\bhr\b|hr tech|identity|networking|telecom|"
            r"communications|messaging|intelligence|talent|storage|infrastructure|automation|"
            r"collaboration|design|tech"
        ),
    ),
)

_ROUND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pre_seed", _rule(r"pre[- ]?seed")),
    ("seed", _rule(r"pre[- ]series[- ]a|seed|angel")),
    ("series_a", _rule(r"series[- ]a\b")),
    ("series_b", _rule(r"series[- ]b\b")),
    ("series_c_plus", _rule(r"series[- ][c-k]\b")),
    ("public", _rule(r"ipo\b|listed|public")),
    ("acquired", _rule(r"acqui")),
    ("bootstrapped", _rule(r"bootstrap")),
)


def stage_from_round(text: str | None) -> str | None:
    """The stage a funding round or status string implies; ``None`` for debt etc."""
    if not text:
        return None
    for stage, pattern in _ROUND_RULES:
        if pattern.search(text):
            return stage
    return None


def sector_from_text(text: str | None) -> str | None:
    """The sector an industry or summary string names, or ``None``."""
    if not text:
        return None
    for sector, pattern in _SECTOR_RULES:
        if pattern.search(text):
            return sector
    return None


def _range_floor(employee_count_range: str | None) -> int | None:
    if not employee_count_range:
        return None
    match = re.match(r"\s*(\d[\d,]*)", employee_count_range)
    return int(match.group(1).replace(",", "")) if match else None


def size_bucket(employee_count: int | None, employee_count_range: str | None) -> str | None:
    """A size bucket from a counted headcount, else the lower bound of a range."""
    size = employee_count if employee_count is not None else _range_floor(employee_count_range)
    if size is None:
        return None
    if size <= 50:
        return "1-50"
    if size <= 200:
        return "51-200"
    if size <= 1000:
        return "201-1000"
    return "1000+"


def tier_for_import(*, is_vc: bool, founded_year: int | None, is_public: bool) -> str | None:
    """
    The type for a company that did not come from the curated catalogue.

    Only the certain cases: a VC, or a young unlisted company. Big tech versus
    IT services cannot be told apart from an import, so it is left unset.
    """
    if is_vc:
        return "vc"
    if is_public or founded_year is None:
        return None
    return "startup" if founded_year >= _STARTUP_SINCE else None


def is_bigger_company(
    tier: str | None, employee_count: int | None, employee_count_range: str | None
) -> bool:
    if tier in _BIG_TIERS:
        return True
    if employee_count is not None:
        return employee_count >= 200
    floor = _range_floor(employee_count_range)
    return floor is not None and floor >= 201
