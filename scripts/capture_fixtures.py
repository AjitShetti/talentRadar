"""
scripts/capture_fixtures.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Record real source responses so the contract tests can replay them offline.

    python scripts/capture_fixtures.py                  # every source
    python scripts/capture_fixtures.py --source foundit # just one
    python scripts/capture_fixtures.py --company-map    # company-map importers
    python scripts/capture_fixtures.py --practice       # practice GitHub responses

Rather than guessing each scraper's request URLs, this wraps
``ScraplingManager.fetch_html_or_json`` and records every call the scraper
actually makes, keyed by URL. That matters for sources like ``ats_platforms``
which fan out across many company boards in one call: the fixture is a map of
URL to response, and the replay harness serves each request from it.

The captures are committed. Refresh them when a source legitimately changes
shape, and review the diff - a fixture that suddenly parses to zero jobs is
the change you wanted to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ingestion.scrapling_manager import ScraplingManager
from ingestion.sources.registry import LiveSource, live_source_registry

if TYPE_CHECKING:
    import httpx

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sources"

# Board search pages run to 1.5 MB. Committing them raw would add tens of
# megabytes to the repository, so fixtures are stored gzipped.
CAPTURE_QUERY = "Python Developer"
CAPTURE_LOCATION = "Bengaluru"


async def capture_one(source: LiveSource) -> dict[str, Any] | None:
    """Run *source* live, recording every HTTP response it consumed."""
    recorded: dict[str, Any] = {}
    original = ScraplingManager.fetch_html_or_json

    async def recording_fetch(
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 6.0,  # noqa: ASYNC109 - mirrors ScraplingManager.fetch
        impersonate: str | None = None,
        **kwargs: Any,
    ) -> tuple[int, str | dict[str, Any]]:
        kw: dict[str, Any] = {"headers": headers, "params": params, "timeout": timeout}
        if impersonate is not None:
            kw["impersonate"] = impersonate
        status, payload = await original(url, **kw, **kwargs)
        key = _request_key(url, params)
        recorded[key] = {"status": status, "payload": payload}
        return status, payload

    ScraplingManager.fetch_html_or_json = recording_fetch  # type: ignore[method-assign]
    try:
        jobs = await asyncio.wait_for(
            source.fetch(CAPTURE_QUERY, CAPTURE_LOCATION, None),
            timeout=max(source.timeout_seconds * 3, 20.0),
        )
    except Exception as exc:
        print(f"  {source.name:18} capture FAILED: {type(exc).__name__}: {exc}")
        return None
    finally:
        ScraplingManager.fetch_html_or_json = original  # type: ignore[method-assign]

    if not jobs:
        print(f"  {source.name:18} capture produced 0 jobs - not writing a fixture that proves nothing")
        return None

    return {
        "source": source.name,
        "query": CAPTURE_QUERY,
        "location": CAPTURE_LOCATION,
        "expected_min_jobs": max(1, len(jobs) // 2),
        "captured_job_count": len(jobs),
        "requests": recorded,
    }


def _request_key(url: str, params: dict[str, Any] | None) -> str:
    """A stable key for one request, so replay can match it back."""
    if not params:
        return url
    encoded = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{url}?{encoded}"


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json.gz"


async def main_async(args: argparse.Namespace) -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    sources = live_source_registry()
    if args.source:
        wanted = set(args.source)
        sources = tuple(s for s in sources if s.name in wanted)
        if not sources:
            print(f"No source matches {args.source}", file=sys.stderr)
            return 2

    print(f"Capturing fixtures into {FIXTURE_DIR}")
    written = 0
    for source in sources:
        if source.requires_browser:
            print(f"  {source.name:18} skipped (needs a browser; not installed)")
            continue
        captured = await capture_one(source)
        if captured is None:
            continue
        path = fixture_path(source.name)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(captured, handle)
        size_kb = path.stat().st_size / 1024
        print(
            f"  {source.name:18} {captured['captured_job_count']:>3} jobs, "
            f"{len(captured['requests'])} request(s), {size_kb:.0f} KB -> {path.name}"
        )
        written += 1

    print(f"\nWrote {written} fixture(s).")
    return 0 if written else 1


COMPANY_MAP_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "company_map"
_WIKIDATA_KEEP = 40  # companies
_OVERPASS_KEEP = 80  # elements
#: The YC entries the tests pin: every case the parser has a rule for (active,
#: inactive, acquired, listed, no website, a Play Store "website", a tracking
#: query, several offices, other cities). A slug that has left the directory is
#: reported, not silently replaced.
_YC_KEEP_SLUGS = (
    "razorpay", "meesho", "markupwand", "drivezy", "scribe", "supr-daily", "chutney-inc",
    "recordbook", "onefin", "100x", "statiq", "dockup", "circuithub", "craze", "medplum",
)  # fmt: skip
_YC_DESCRIPTION_KEEP = 400  # characters of long_description
_GITHUB_SEARCH_KEEP = 14  # search results
#: Organisations the tests pin: a real company, one with no website and no
#: repository, one in several cities, one whose link has no scheme.
_GITHUB_ORGS = ("SigNoz", "swiggy-private", "hotstar", "Auquan")


def _write_fixture(name: str, data: object) -> None:
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    (COMPANY_MAP_DIR / name).write_text(text, encoding="utf-8", newline="\n")


async def capture_yc(client: httpx.AsyncClient) -> None:
    """Record ``yc.json``: the pinned entries of the directory, descriptions cut short."""
    from services.company_map import yc

    directory = await yc.fetch(client)
    by_slug = {entry.get("slug"): entry for entry in directory}
    missing = [slug for slug in _YC_KEEP_SLUGS if slug not in by_slug]
    kept = [by_slug[slug] for slug in _YC_KEEP_SLUGS if slug in by_slug]
    for entry in kept:
        entry["long_description"] = (entry.get("long_description") or "")[:_YC_DESCRIPTION_KEEP]
    _write_fixture("yc.json", kept)
    note = f" (gone from the directory: {missing})" if missing else ""
    print(f"  yc        {len(kept)} companies{note}")


async def capture_github_orgs(client: httpx.AsyncClient) -> None:
    """Record ``github_org_search.json`` and ``github_orgs.json`` (contact email blanked)."""
    from services.company_map import github_orgs

    page = await github_orgs.search_page(client, github_orgs.LOCATIONS[0], 1)
    page["items"] = page.get("items", [])[:_GITHUB_SEARCH_KEEP]
    _write_fixture("github_org_search.json", page)
    print(f"  github    {len(page['items'])} search results")

    organisations: dict[str, object] = {}
    for login in _GITHUB_ORGS:
        org = await github_orgs.fetch_org(client, login)
        if org is not None:
            org["email"] = None
            organisations[login] = org
    _write_fixture("github_orgs.json", organisations)
    print(f"  github    {len(organisations)} organisations")


async def capture_company_map() -> int:
    """
    Re-record the company-map importer fixtures: Wikidata, Overpass, the
    Y Combinator directory, one GitHub organisation search and four
    organisation records, and the four news feeds, trimmed so they stay small.
    The people fixtures (GitHub members, Tavily) are synthetic by design and
    are not recorded: real responses would put real individuals' details in
    the repository. The GitHub organisation records are organisations, and
    their contact email is blanked.
    """
    import re

    import httpx

    from services.company_map import osm, wikidata
    from services.company_map.news_feeds import FEEDS, fetch_feed

    COMPANY_MAP_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = await wikidata.fetch(client)
        rows = payload.get("results", {}).get("bindings", [])
        keep = list(dict.fromkeys(r["item"]["value"] for r in rows if "item" in r))[:_WIKIDATA_KEEP]
        payload["results"]["bindings"] = [r for r in rows if r.get("item", {}).get("value") in keep]
        (COMPANY_MAP_DIR / "wikidata.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"  wikidata  {len(keep)} companies")

        overpass = await osm.fetch(client)
        overpass["elements"] = overpass.get("elements", [])[:_OVERPASS_KEEP]
        (COMPANY_MAP_DIR / "overpass.json").write_text(json.dumps(overpass, indent=1), encoding="utf-8")
        print(f"  overpass  {len(overpass['elements'])} elements")

        await capture_yc(client)
        await capture_github_orgs(client)

        for source in FEEDS:
            xml = await fetch_feed(client, source)
            # Full article bodies are copyrighted and large; the parser never reads them.
            xml = re.sub(rb"<content:encoded>.*?</content:encoded>", b"", xml, flags=re.S)
            (COMPANY_MAP_DIR / f"rss_{source}.xml").write_bytes(xml)
            print(f"  rss_{source:9} {len(xml) // 1024} KB")
    return 0


PRACTICE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "practice"
#: Practice reads GitHub only. Its LeetCode fixtures are written by hand; no script fetches the LeetCode site.
_PRACTICE_HOSTS = ("api.github.com",)
_GITHUB_JSON = "application/vnd.github+json"
_GITHUB_RAW = "application/vnd.github.raw"


def _blank_commit_emails(commits: list[dict[str, Any]]) -> None:
    """Blank the author and committer emails: a commit's contact address is a real person's, and the fixture never keeps one."""
    for entry in commits:
        for role in ("author", "committer"):
            entry["commit"][role]["email"] = None


#: Fictional stand-ins for the people GitHub reports. The GitHub mascot account is
#: not a person, so it is kept as published.
FAKE_LOGIN = "demo-builder"
FAKE_NAME = "Demo Builder"
FAKE_USER_ID = 90000001
_MASCOT = frozenset({"octocat", "The Octocat"})


def _fictional_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """A fictional GitHub account in place of a real one. None and the mascot pass through."""
    if user is None or user.get("login") in _MASCOT:
        return user
    return {
        "login": FAKE_LOGIN,
        "id": FAKE_USER_ID,
        "avatar_url": f"https://avatars.githubusercontent.com/u/{FAKE_USER_ID}?v=4",
        "html_url": f"https://github.com/{FAKE_LOGIN}",
        "url": f"https://api.github.com/users/{FAKE_LOGIN}",
        "type": "User",
    }


def _pseudonymise(commits: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, str]:
    """
    Replace every real person in the commit history and the activity feed with a
    fictional one, in place. Returns the real names and logins replaced, mapped to
    their stand-ins, so the same renames also reach commit messages and URLs.
    """
    renames: dict[str, str] = {}
    _blank_commit_emails(commits)
    for entry in commits:
        for role in ("author", "committer"):
            person = entry["commit"][role]
            account = entry.get(role)
            login = account.get("login") if account else None
            if login in _MASCOT or person.get("name") in _MASCOT:
                continue
            if person.get("name"):
                renames[person["name"]] = FAKE_NAME
            if login:
                renames[login] = FAKE_LOGIN
            person["name"] = FAKE_NAME
            entry[role] = _fictional_user(account)
    for event in events:
        actor = event.get("actor")
        login = actor.get("login") if actor else None
        if actor and login not in _MASCOT:
            if login:
                renames[login] = FAKE_LOGIN
            event["actor"] = _fictional_user(actor)
    return renames


def _apply_renames(text: str, renames: dict[str, str]) -> str:
    """
    Rename real identities in serialised JSON, in any casing. Longest first, so a
    full name is replaced before a prefix of it could be.
    """
    for real in sorted(renames, key=len, reverse=True):
        # Doubled backslashes keep the stand-in literal in re.sub's replacement syntax.
        literal = renames[real].replace("\\", "\\\\")
        text = re.sub(re.escape(real), literal, text, flags=re.IGNORECASE)
    return text


async def capture_practice(client: httpx.AsyncClient) -> None:
    """
    Record the GitHub responses the Practice reader replays: the octocat sample
    repository (repo, commits, tree, README) and the activity feed of the repository
    named by PRACTICE_ACTIVITY_REPO (owner/name). Unauthenticated unless GITHUB_TOKEN is set.
    """
    activity_repo = os.environ.get("PRACTICE_ACTIVITY_REPO", "")
    if not activity_repo:
        raise SystemExit(
            "set PRACTICE_ACTIVITY_REPO=owner/name to record that repository's activity"
        )
    PRACTICE_DIR.mkdir(parents=True, exist_ok=True)
    auth = (
        {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
        if os.environ.get("GITHUB_TOKEN")
        else {}
    )

    async def get(path: str, accept: str) -> httpx.Response:
        url = f"https://api.github.com{path}"
        assert urlsplit(url).hostname in _PRACTICE_HOSTS, url
        response = await client.get(url, headers={**auth, "Accept": accept})
        response.raise_for_status()
        return response

    json_responses = {
        "github_repo.json": await get("/repos/octocat/Hello-World", _GITHUB_JSON),
        "github_activity.json": await get(
            f"/repos/{activity_repo}/activity?per_page=20", _GITHUB_JSON
        ),
        "github_commits.json": await get(
            "/repos/octocat/Hello-World/commits?per_page=5", _GITHUB_JSON
        ),
        "github_tree.json": await get(
            "/repos/octocat/Hello-World/git/trees/master?recursive=1", _GITHUB_JSON
        ),
    }
    payloads = {name: response.json() for name, response in json_responses.items()}
    renames = _pseudonymise(payloads["github_commits.json"], payloads["github_activity.json"])
    for name, payload in payloads.items():
        text = _apply_renames(json.dumps(payload, indent=2, ensure_ascii=False), renames)
        (PRACTICE_DIR / name).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"  practice  {name}")

    readme = await get("/repos/octocat/Hello-World/readme", _GITHUB_RAW)
    (PRACTICE_DIR / "github_readme.md").write_bytes(readme.content)
    print("  practice  github_readme.md")


async def _capture_practice_main() -> int:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        await capture_practice(client)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record live source responses as test fixtures.")
    parser.add_argument("--source", action="append", help="Capture only this source (repeatable)")
    parser.add_argument(
        "--company-map", action="store_true", help="Record the company-map importer fixtures"
    )
    parser.add_argument(
        "--practice", action="store_true", help="Record the practice GitHub fixtures"
    )
    args = parser.parse_args()
    if args.company_map:
        return asyncio.run(capture_company_map())
    if args.practice:
        return asyncio.run(_capture_practice_main())
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
