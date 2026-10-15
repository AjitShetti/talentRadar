"""
services/company_map/hiring_list.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Bengaluru employers from a shared India recruiter list, checked into
``data/company_sources/bengaluru_hiring_list.json`` (provenance in its
``note``).

The list says only that an employer exists and where its website is, so the
source ranks below everything that says more (``hiring_list`` sits just above
``news`` in ``SOURCE_PRECEDENCE``). Its people and emails were never copied
into the data file. A row becomes listed once the ``sites`` step has read the
employer's own homepage, and ``is_tech`` is decided from that text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from services.company_map.sourced import clean_website
from services.company_map.types import ImportedCompany

DATA_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "company_sources" / "bengaluru_hiring_list.json"
)


def parse(payload: Mapping[str, Any]) -> list[ImportedCompany]:
    """One :class:`ImportedCompany` per record with a name and a website of its own."""
    companies: list[ImportedCompany] = []
    for record in payload.get("companies") or []:
        if not isinstance(record, Mapping):
            continue
        name = str(record.get("name") or "").strip()
        site = clean_website(record.get("website_url"), allow_hosted=False)
        if not name or site is None:
            continue
        website_url, domain = site
        companies.append(ImportedCompany(name=name, domain=domain, website_url=website_url))
    return companies


def load(path: Path = DATA_FILE) -> list[ImportedCompany]:
    """The checked-in list, parsed."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return parse(payload if isinstance(payload, Mapping) else {})
