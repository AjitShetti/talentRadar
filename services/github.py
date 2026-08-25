"""
services/github.py
~~~~~~~~~~~~~~~~~~
Read-only GitHub lookups for the Company Intelligence panel.

The catalogue in ``data/companies/*.json`` stores a *slug* (``"razorpay"``),
never a URL, and that slug is resolved live here. A slug that is wrong or stale
simply 404s and the caller gets ``None`` — the UI then hides the open-source
block rather than linking somewhere broken.

Unauthenticated GitHub allows 60 requests/hour per IP, which a single browsing
session can exhaust, so results are cached in-process for a day and every
failure degrades to ``None``. Set ``GITHUB_TOKEN`` to lift the limit to 5000/h.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
_TOP_REPOS = 6

# slug -> (expires_at, payload|None). A cached ``None`` is meaningful: it stops
# a bad slug from re-querying GitHub on every page view.
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "TalentRadar/1.0",
    }
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _repo_summary(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": repo.get("name"),
        "full_name": repo.get("full_name"),
        "description": repo.get("description"),
        "html_url": repo.get("html_url"),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "topics": list(repo.get("topics") or [])[:5],
        "pushed_at": repo.get("pushed_at"),
    }


async def _fetch(org: str) -> dict[str, Any] | None:
    """Fetch org metadata + its most-starred public repos. ``None`` on any miss."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
            org_resp = await client.get(f"{_API}/orgs/{org}")
            if org_resp.status_code == 404:
                logger.info("GitHub org %r not found — skipping open-source panel", org)
                return None
            if org_resp.status_code == 403:
                logger.warning("GitHub rate limit hit while resolving %r", org)
                return None
            org_resp.raise_for_status()
            org_data = org_resp.json()

            # GitHub cannot sort org repos by stars, so pull a page ordered by
            # recent pushes and rank locally — enough to surface the flagships.
            repos_resp = await client.get(
                f"{_API}/orgs/{org}/repos",
                params={"per_page": 100, "sort": "pushed", "type": "public"},
            )
            repos = repos_resp.json() if repos_resp.status_code == 200 else []
            if not isinstance(repos, list):
                repos = []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GitHub lookup failed for %r: %s", org, exc)
        return None

    ranked = sorted(
        (r for r in repos if isinstance(r, dict) and not r.get("archived")),
        key=lambda r: r.get("stargazers_count", 0),
        reverse=True,
    )[:_TOP_REPOS]

    languages: dict[str, int] = {}
    for repo in repos:
        lang = repo.get("language") if isinstance(repo, dict) else None
        if lang:
            languages[lang] = languages.get(lang, 0) + 1

    return {
        "org": org,
        "name": org_data.get("name") or org,
        "html_url": org_data.get("html_url") or f"https://github.com/{org}",
        "avatar_url": org_data.get("avatar_url"),
        "description": org_data.get("description"),
        "blog": org_data.get("blog"),
        "location": org_data.get("location"),
        "public_repos": org_data.get("public_repos", 0),
        "followers": org_data.get("followers", 0),
        "top_repos": [_repo_summary(r) for r in ranked],
        "top_languages": [
            lang for lang, _ in sorted(languages.items(), key=lambda kv: kv[1], reverse=True)[:8]
        ],
    }


async def org_snapshot(org: str | None) -> dict[str, Any] | None:
    """
    Public-GitHub snapshot for ``org``, or ``None`` when there is nothing to show.

    Never raises: a missing org, a rate limit and a network failure all return
    ``None`` so the company detail endpoint stays available either way.
    """
    if not org:
        return None
    slug = org.strip().strip("/").split("/")[-1]
    if not slug:
        return None

    cached = _cache.get(slug)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    # One in-flight request per org: opening a directory of 80 companies
    # should not fan out into 80 duplicate calls for the same slug.
    lock = _locks.setdefault(slug, asyncio.Lock())
    async with lock:
        cached = _cache.get(slug)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        data = await _fetch(slug)
        _cache[slug] = (time.monotonic() + _CACHE_TTL_SECONDS, data)
        return data


def clear_cache() -> None:
    """Drop the in-process cache (used by tests)."""
    _cache.clear()
    _locks.clear()
