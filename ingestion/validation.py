"""
ingestion/validation.py
~~~~~~~~~~~~~~~~~~~~~~~
Shared URL and content validation for job postings.

Ensures that only valid, individual job posting URLs are ingested into
PostgreSQL and ChromaDB — filtering out non-job domains (Wikipedia, Reddit,
YouTube, Medium, Quora, etc.) and search/category/listing pages on valid job boards.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Known non-job domains to block unconditionally (defense-in-depth)
BLOCKED_DOMAINS = [
    "wikipedia.org",
    "reddit.com",
    "youtube.com",
    "youtu.be",
    "medium.com",
    "quora.com",
    "github.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "stackoverflow.com",
    "pinterest.com",
    "tiktok.com",
    "vimeo.com",
]

# Standard allowed job platforms and ATS platforms
ALLOWED_JOB_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "cutshort.io",
    "wellfound.com",
    "angel.co",
    "workday.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "jobvite.com",
    "bamboohr.com",
    "talentradar.internal",
]

# Blacklisted path tokens that identify search, category, or listing aggregations
LISTING_PATH_PATTERNS = [
    r"/q-",
    r"-jobs\.html",
    r"/jobs/search",
    r"/jobs/collections",
    r"/jobs/role/",
    r"/browse-jobs",
    r"/salaries",
    r"/companies",
    r"/search\b",
    r"/find-jobs",
    r"/job-search",
    r"/jobs-in-",
]


def _validate_url_format(url: str) -> bool:
    """Return True only for well-formed http/https URLs with a valid netloc."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _url_matches_domain(url: str, domain: str) -> bool:
    """
    Check whether *url* belongs to *domain* using proper URL parsing.

    Matches both bare domains and subdomains:
        _url_matches_domain("https://in.indeed.com/jobs", "indeed.com")  → True
        _url_matches_domain("https://www.linkedin.com/jobs", "linkedin.com")  → True
    """
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    domain = domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def _matches_any_domain(url: str, domains: list[str]) -> bool:
    """Return True if *url* matches any entry in *domains*."""
    return any(_url_matches_domain(url, d) for d in domains)


def validate_job_url(url: str) -> tuple[bool, str]:
    """
    Validate whether a URL represents a genuine, individual job posting.

    Returns:
        (is_valid, reason_str)
    """
    if not url or not _validate_url_format(url):
        return False, "Malformed or empty URL"

    url_clean = url.strip()
    try:
        parsed = urlparse(url_clean)
        host = (parsed.hostname or "").lower()
        path = parsed.path
        query = parsed.query
    except Exception as exc:
        return False, f"Failed parsing URL: {exc}"

    # 1. Check host blocklist
    for blocked in BLOCKED_DOMAINS:
        if _url_matches_domain(url_clean, blocked):
            return False, f"Blocked non-job domain: {blocked}"

    # 2. Check for generic listing/search path signatures
    has_explicit_job_param = bool(re.search(r"[?&](gh_jid|job_id|posting_id|jid|jobId)=\w+", url_clean))
    if not has_explicit_job_param:
        for pattern in LISTING_PATH_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return False, f"URL path matches search/listing pattern: {pattern}"

    # 3. Domain-specific structural checks
    if _url_matches_domain(url_clean, "linkedin.com"):
        # LinkedIn individual posting MUST be /jobs/view/<id>
        if not re.search(r"^/jobs/view/[^/?#]+", path):
            return False, "LinkedIn URL is not an individual job posting (/jobs/view/<id> required)"
        return True, "Valid LinkedIn posting URL"

    if _url_matches_domain(url_clean, "indeed.com"):
        # Indeed individual posting has /viewjob?jk=... or /rc/clk?jk=... or /viewjob/<id>
        params = parse_qs(query)
        if "/viewjob" in path or "/rc/clk" in path or "jk" in params:
            if "jk" in params and params["jk"]:
                return True, "Valid Indeed posting URL (with jk parameter)"
            if re.search(r"/viewjob/[^/?#]+", path):
                return True, "Valid Indeed posting URL (with viewjob path id)"
            # If path is /viewjob without jk query or path id, check if query contains jk
            if "/viewjob" in path and ("jk" in params or "jk=" in query):
                return True, "Valid Indeed posting URL"
            return False, "Indeed URL missing job key parameter (jk=)"
        return False, "Indeed URL is not an individual job posting (/viewjob?jk= required)"

    if _url_matches_domain(url_clean, "greenhouse.io"):
        # boards.greenhouse.io/<company>/jobs/<id> or /<company>/jobs/<id>
        if not re.search(r"/jobs/\d+", path) and not re.search(r"/jobs/[^/?#]+", path):
            return False, "Greenhouse URL missing job ID (/jobs/<id> required)"
        # Reject if path is only /<company> or /<company>/
        segments = [s for s in path.strip("/").split("/") if s]
        if len(segments) < 2 or (len(segments) == 2 and segments[1] != "jobs"):
            if "jobs" not in segments:
                return False, "Greenhouse URL points to company board index, not an individual job"
        return True, "Valid Greenhouse posting URL"

    if _url_matches_domain(url_clean, "lever.co"):
        # jobs.lever.co/<company>/<posting_id>
        segments = [s for s in path.strip("/").split("/") if s]
        if len(segments) < 2:
            return False, "Lever URL points to company index, missing posting ID"
        return True, "Valid Lever posting URL"

    if _url_matches_domain(url_clean, "ashbyhq.com"):
        # jobs.ashbyhq.com/<company>/<job_id>
        segments = [s for s in path.strip("/").split("/") if s]
        if len(segments) < 2:
            return False, "Ashby URL points to company index, missing posting ID"
        return True, "Valid Ashby posting URL"

    if _url_matches_domain(url_clean, "naukri.com"):
        # naukri.com/job-listings-<slug>-<id> or naukri.com/job-detail/...
        if not re.search(r"job-listings-", path) and not re.search(r"job-detail", path):
            return False, "Naukri URL is a search or category page (job-listings-* required)"
        return True, "Valid Naukri posting URL"

    if _url_matches_domain(url_clean, "cutshort.io"):
        # cutshort.io/job/<id_or_slug>
        if not re.search(r"^/job/[^/?#]+", path):
            return False, "Cutshort URL is not an individual job posting (/job/<id> required)"
        return True, "Valid Cutshort posting URL"

    if _url_matches_domain(url_clean, "talentradar.internal"):
        # Internal test domains: must have non-empty path
        if not path or path == "/":
            return False, "Internal test URL points to root"
        return True, "Valid internal test job URL"

    # 4. Check whether domain is in recognized job platform list
    is_known_job_domain = _matches_any_domain(url_clean, ALLOWED_JOB_DOMAINS)
    if not is_known_job_domain:
        # Check if it looks like a company career/job page (e.g. careers.*, jobs.*, .careers, or has job ID param)
        if has_explicit_job_param or host.startswith("careers.") or host.startswith("jobs.") or ".careers" in host or "/job" in path or "/position" in path or "/career" in path:
            segments = [s for s in path.strip("/").split("/") if s]
            if len(segments) >= 1 or has_explicit_job_param:
                return True, "Valid company career posting URL"
        return False, f"Domain '{host}' is not a recognized job board or ATS platform"

    # For other allowed platforms, ensure non-root path with at least 1 segment
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        return False, "URL points to domain root, not an individual job posting"

    return True, "Valid job posting URL"


def is_valid_job_url(url: str) -> bool:
    """Return True if url is a valid individual job posting URL."""
    valid, _ = validate_job_url(url)
    return valid
