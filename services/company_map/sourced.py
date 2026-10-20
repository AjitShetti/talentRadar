"""
services/company_map/sourced.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
What the directory-style importers (Y Combinator, GitHub organisations) share:
an :class:`~services.company_map.types.ImportedCompany` that also carries the
source's own facts for ``companies.metadata``, and the one place a self-reported
website is cleaned into a URL and a domain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from domain.company_identity import is_tenant_domain, normalize_domain
from services.company_map.types import ImportedCompany

_WEBSITE_MAX = 512  # companies.website_url is String(512)


@dataclass(frozen=True, kw_only=True)
class SourcedCompany(ImportedCompany):
    """
    An imported company plus facts only its source states (a YC batch, a
    GitHub follower count). The refresh step merges ``source_metadata`` into
    ``companies.metadata``; the store itself knows nothing about it.
    """

    source_metadata: Mapping[str, Any] = field(default_factory=dict)


def clean_website(raw: object, *, allow_hosted: bool = True) -> tuple[str, str] | None:
    """
    ``(website_url, domain)`` for a self-reported link, or ``None``.

    The link gets ``https://`` when it has no scheme and loses its query and
    fragment (``?utm_source=yc`` is tracking, not part of the address). ``None``
    for anything that is not an http(s) URL with a domain of its own: a
    profile page, a bare word, an unparsable value. A hosting-platform
    subdomain (``acme.github.io``) is refused unless ``allow_hosted``.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    candidate = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    domain = normalize_domain(url)
    if domain is None or len(url) > _WEBSITE_MAX:
        return None
    if not allow_hosted and is_tenant_domain(domain):
        return None
    return url, domain
