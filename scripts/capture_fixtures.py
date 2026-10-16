"""
scripts/capture_fixtures.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Record real source responses so the contract tests can replay them offline.

    python scripts/capture_fixtures.py                  # every source
    python scripts/capture_fixtures.py --source foundit # just one
    python scripts/capture_fixtures.py --company-map    # company-map importers
    python scripts/capture_fixtures.py --events         # Events: the Luma iCal feeds

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
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ingestion.scrapling_manager import ScraplingManager
from ingestion.sources.registry import LiveSource, live_source_registry
from scripts.fixture_privacy import pseudonymise_ics

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


# ── Events ───────────────────────────────────────────────────────────────────

EVENTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "events"
EVENT_SOURCES = Path(__file__).resolve().parent.parent / "data" / "events" / "sources.json"
_LUMA_CALENDARS_RECORDED = 2  # the first calendars in sources.json; the rest are not replayed
_LUMA_GAP_S = 1.0  # one request a second


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


async def capture_events_luma(redact: Sequence[str] = ()) -> int:
    """
    Record the Luma iCal feeds the tests replay: the Bengaluru Discover feed and
    the first two curated calendars in ``data/events/sources.json``. Each is
    fetched once through the same ``fetch_feed`` the app uses (one request a
    second), checked to parse, pseudonymised, and written. Returns a process
    exit code: 0 when every planned file was written.

    People are pseudonymised on the way in (see :func:`pseudonymise_ics`), so a
    real host's name never reaches the repository. Review the diff for people
    the rules cannot see, such as a speaker named in a description, and re-run
    with ``--redact "Full Name"`` for each.
    """
    import httpx

    from services.events import luma

    sources = json.loads(EVENT_SOURCES.read_text(encoding="utf-8"))["luma"]
    plan: list[tuple[str, Literal["discover", "calendar"], str]] = [
        (f"luma_discover_{_slug(entry['name'])}.ics", "discover", entry["id"])
        for entry in sources["discover"][:1]
    ] + [
        (f"luma_calendar_{_slug(entry['name'])}.ics", "calendar", entry["id"])
        for entry in sources["calendars"][:_LUMA_CALENDARS_RECORDED]
    ]
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    async with httpx.AsyncClient() as client:
        for index, (filename, kind, ident) in enumerate(plan):
            if index:
                await asyncio.sleep(_LUMA_GAP_S)
            try:
                data = await luma.fetch_feed(client, luma.feed_url(kind, ident))
            except (httpx.HTTPError, ValueError) as exc:
                print(f"  luma {kind:8} {ident} FAILED: {type(exc).__name__}: {exc}")
                continue
            events = await asyncio.to_thread(luma.parse_feed, data)
            if not events:
                print(f"  luma {kind:8} {ident} parsed to 0 events - not writing a fixture")
                continue
            text = pseudonymise_ics(data.decode("utf-8-sig", errors="replace"), extra_names=redact)
            (EVENTS_DIR / filename).write_text(text, encoding="utf-8", newline="\n")
            print(f"  luma {kind:8} {len(events):>3} events, {len(text) // 1024} KB -> {filename}")
            written += 1
    print(f"\nWrote {written} of {len(plan)} Luma fixture(s).")
    return 0 if written == len(plan) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Record live source responses as test fixtures.")
    parser.add_argument("--source", action="append", help="Capture only this source (repeatable)")
    parser.add_argument(
        "--company-map", action="store_true", help="Record the company-map importer fixtures"
    )
    parser.add_argument(
        "--events", action="store_true", help="Record the Events fixtures (Luma iCal feeds)"
    )
    parser.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="NAME",
        help="With --events: also rename this person wherever a recording names them (repeatable)",
    )
    args = parser.parse_args()
    if args.company_map:
        return asyncio.run(capture_company_map())
    if args.events:
        return asyncio.run(capture_events_luma(args.redact))
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
