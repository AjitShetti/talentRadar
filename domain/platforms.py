"""
domain/platforms.py
~~~~~~~~~~~~~~~~~~~
The job platforms a user can filter search results by.

``jobs.source`` is written by several producers that never agreed on a
vocabulary: the Indeed scraper stores ``indeed_india`` while URL detection
stores ``indeed``, and the ATS scraper stores ``greenhouse:<company>``. A
posting found through Tavily is labelled ``tavily_search`` even when it links
to LinkedIn. So a platform is matched on *either* its stored source values
*or* the host of the posting's URL — the URL is the one thing every row,
including semantic-search results, reliably carries.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class JobPlatform:
    """One selectable platform in the job-search filter."""

    key: str
    label: str
    # ``jobs.source`` values. A value also matches its ``<value>:<suffix>``
    # form, which is how ATS rows record the company they came from.
    sources: tuple[str, ...]
    # Hostname suffixes of the posting's URL (``in.indeed.com`` ends in
    # ``indeed.com``).
    hosts: tuple[str, ...]


JOB_PLATFORMS: tuple[JobPlatform, ...] = (
    JobPlatform("linkedin", "LinkedIn", ("linkedin",), ("linkedin.com",)),
    JobPlatform("naukri", "Naukri", ("naukri",), ("naukri.com",)),
    JobPlatform("indeed", "Indeed", ("indeed", "indeed_india"), ("indeed.com",)),
    JobPlatform("foundit", "Foundit", ("foundit",), ("foundit.in",)),
    JobPlatform("instahyre", "Instahyre", ("instahyre",), ("instahyre.com",)),
    JobPlatform("cutshort", "Cutshort", ("cutshort",), ("cutshort.io",)),
    JobPlatform("freshersworld", "Freshersworld", ("freshersworld",), ("freshersworld.com",)),
    # Greenhouse, Lever and Ashby host company career pages; users think of
    # these as "the company's own site", not as three separate boards.
    JobPlatform(
        "company_sites",
        "Company career sites",
        ("greenhouse", "lever", "ashby", "ats_platforms"),
        ("greenhouse.io", "lever.co", "ashbyhq.com"),
    ),
)

_PLATFORMS_BY_KEY: dict[str, JobPlatform] = {p.key: p for p in JOB_PLATFORMS}


def get_platform(key: str | None) -> JobPlatform | None:
    """Look up a platform by its filter key (``"linkedin"``, ``"company_sites"``…)."""
    if not key:
        return None
    return _PLATFORMS_BY_KEY.get(key.strip().lower())


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def platform_of(source: str | None, source_url: str | None) -> JobPlatform | None:
    """The platform a posting was listed on, or ``None`` when it is unrecognised.

    The URL wins over the stored source: it names where the posting actually
    lives, whereas the source names whichever crawler happened to find it.
    """
    host = ""
    if source_url:
        try:
            host = (urlparse(source_url).hostname or "").lower()
        except ValueError:
            host = ""
    if host:
        for platform in JOB_PLATFORMS:
            if any(_host_matches(host, suffix) for suffix in platform.hosts):
                return platform

    base = (source or "").strip().lower().split(":", 1)[0]
    if base:
        for platform in JOB_PLATFORMS:
            if base in platform.sources:
                return platform
    return None
