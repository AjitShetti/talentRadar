"""
scripts/capture_board_fixtures.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Record one real board per ATS so the board-reader tests can replay it offline.

    python scripts/capture_board_fixtures.py --ats greenhouse,ashby,lever
    python scripts/capture_board_fixtures.py --ats workable,recruitee,keka

Each ATS is asked for exactly what ``ingestion/boards/<ats>.py`` asks for: one
request (Keka's reader makes a second, for the company's name), with the project's
User-Agent, no redirects followed, and at least the ATS's pace
(``domain.job_boards.PACE_SECONDS``) between two requests to it. The response is
stored as it came back, in
``tests/fixtures/boards/<ats>.json.gz``::

    {"ats": ..., "board": ..., "requests": {<url>: {"status": 200,
                                                     "headers": {...}, "body": "<text>"}}}

A request's key is its full URL, query string included, built the way
``scripts/capture_fixtures.py::_request_key`` builds it (sorted ``k=v`` pairs
after a ``?``). Tests look a request up by ``str(request.url)``, which is the
same string for the single-parameter requests here.

The one edit is for privacy. A Greenhouse job's custom ``metadata`` fields can hold
employees' names, so ``scripts/fixture_privacy.py::scrub_greenhouse_metadata`` blanks
every field not known to be about the role. Workable, Recruitee and Keka are cut
down to the fields listed there as being about the role (default-deny: a recruiter,
a hiring manager or a per-job mailbox a board adds later is dropped), and any
address that is not a team inbox becomes a placeholder. No reader looks at what is
dropped.

The captures are committed. Re-record when an ATS legitimately changes shape and
review the diff: a fixture that suddenly parses to nothing is the change you
wanted to catch. Nothing is written for a board that answered anything but a
200 JSON body, or that answered with no postings, since such a fixture proves
nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# Run as a file the script's own folder is sys.path[0], so the repository root
# is added here, as scripts/capture_fixtures.py does.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.job_boards import PACE_SECONDS
from scripts.fixture_privacy import (
    scrub_greenhouse_metadata,
    scrub_keka_jobs,
    scrub_keka_portal,
    scrub_recruitee_board,
    scrub_workable_board,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "boards"

USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
TIMEOUT_SECONDS = 30.0
#: Same ceiling the readers enforce; a board over it would be an ``error`` read.
MAX_BODY_BYTES = 2 * 1024 * 1024


#: Cuts a response down to what a recording may hold (see ``scripts/fixture_privacy.py``).
Scrubber = Callable[[Any], Any]


@dataclass(frozen=True)
class Capture:
    """One board to record and the request its reader makes.

    ``also`` is every further GET the reader makes for the same board, each with the
    scrubber its response goes through; they are recorded beside the first request.
    """

    board: str
    url: str
    params: dict[str, str] | None = None
    also: tuple[tuple[str, Scrubber], ...] = ()


#: What each ATS's main response goes through before it is written.
SCRUBBERS: dict[str, Scrubber] = {
    "greenhouse": scrub_greenhouse_metadata,
    "workable": scrub_workable_board,
    "recruitee": scrub_recruitee_board,
    "keka": scrub_keka_jobs,
}

#: One real Indian employer per ATS (2026-11-23: Sigmoid 47 roles, Sarvam 42,
#: Meesho 47, Apna 156, Lenskart 88, Spyne 9, almost all in India).
CAPTURES: dict[str, Capture] = {
    "greenhouse": Capture(
        "sigmoid",
        "https://boards-api.greenhouse.io/v1/boards/sigmoid/jobs",
        {"content": "true"},
    ),
    "ashby": Capture("sarvam", "https://api.ashbyhq.com/posting-api/job-board/sarvam"),
    # The reader's first page. (The committed recording predates paging and was made
    # without skip and limit; Meesho has under 50 roles, so it is the same body.)
    "lever": Capture(
        "meesho",
        "https://api.lever.co/v0/postings/meesho",
        {"mode": "json", "skip": "0", "limit": "50"},
    ),
    "workable": Capture("apna", "https://apply.workable.com/api/v1/widget/accounts/apna"),
    "recruitee": Capture("lenskartcareers", "https://lenskartcareers.recruitee.com/api/offers/"),
    # The jobs list, then the company's name: the reader asks for both, only under /careers/.
    "keka": Capture(
        "spyneai",
        "https://spyneai.keka.com/careers/api/jobs/default/active",
        also=(
            (
                "https://spyneai.keka.com/careers/api/organization/default/careerportalinfo",
                scrub_keka_portal,
            ),
        ),
    ),
}


def request_key(url: str, params: dict[str, str] | None) -> str:
    """The same key ``scripts/capture_fixtures.py::_request_key`` gives a request."""
    if not params:
        return url
    return f"{url}?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def fixture_path(ats: str) -> Path:
    return FIXTURE_DIR / f"{ats}.json.gz"


#: Seconds to wait between two requests to one ATS (``domain.job_boards.PACE_SECONDS``).
_PACE: dict[str, float] = {str(ats): seconds for ats, seconds in PACE_SECONDS.items()}

Pause = Callable[[float], Awaitable[None]]


def _postings(payload: Any) -> object:
    """The list of postings in a board's response: Workable's and Greenhouse's ``jobs``, Recruitee's ``offers``, or a bare list."""
    if isinstance(payload, dict):
        return payload.get("jobs", payload.get("offers"))
    return payload


async def _get(
    ats: str, url: str, params: dict[str, str] | None, client: httpx.AsyncClient
) -> httpx.Response | None:
    """One GET with the project's User-Agent, or ``None`` (and why, printed) when it is not a 200 within the cap."""
    try:
        response = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        print(f"  {ats:12} FAILED: {type(exc).__name__}: {exc}")
        return None
    if response.status_code != 200:
        print(f"  {ats:12} answered {response.status_code}, not recording it")
        return None
    if len(response.content) > MAX_BODY_BYTES:
        print(f"  {ats:12} body is {len(response.content)} bytes, over the {MAX_BODY_BYTES} cap")
        return None
    return response


def _entry(response: httpx.Response, body: str) -> dict[str, Any]:
    return {
        "status": response.status_code,
        "headers": {"content-type": response.headers.get("content-type", "")},
        "body": body,
    }


async def record(
    ats: str, capture: Capture, client: httpx.AsyncClient, *, pause: Pause = asyncio.sleep
) -> dict[str, Any] | None:
    """Ask the board and return the fixture, or ``None`` when it proves nothing.

    The first request is the one the reader's list comes from; each of ``capture.also``
    follows it, ``pause`` apart by the ATS's pace.
    """
    response = await _get(ats, capture.url, capture.params, client)
    if response is None:
        return None
    try:
        payload = response.json()
    except ValueError:
        print(f"  {ats:12} body is not JSON, not recording it")
        return None

    # Only what is known to be about the role is kept (greenhouse: only its custom
    # fields are blanked); see scripts/fixture_privacy.py.
    scrub = SCRUBBERS.get(ats)
    if scrub is not None:
        payload = scrub(payload)
    postings = _postings(payload)
    if not isinstance(postings, list) or not postings:
        print(f"  {ats:12} returned no postings, not recording a fixture that proves nothing")
        return None

    body = response.text if scrub is None else json.dumps(payload, ensure_ascii=False)
    requests = {request_key(capture.url, capture.params): _entry(response, body)}
    for url, scrub_also in capture.also:
        await pause(_PACE.get(ats, 1.0))
        extra = await _get(ats, url, None, client)
        if extra is None:
            return None
        try:
            kept = scrub_also(extra.json())
        except ValueError:
            print(f"  {ats:12} {url} is not JSON, not recording it")
            return None
        if not kept:
            print(f"  {ats:12} {url} had nothing to keep, not recording it")
            return None
        requests[request_key(url, None)] = _entry(extra, json.dumps(kept, ensure_ascii=False))

    print(f"  {ats:12} {len(postings):>3} postings, {len(response.content) // 1024} KB")
    return {"ats": ats, "board": capture.board, "requests": requests}


def write_fixture(ats: str, fixture: dict[str, Any]) -> Path:
    """Gzip with no timestamp in the header, so recording the same body twice gives the same file."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = fixture_path(ats)
    path.write_bytes(gzip.compress(json.dumps(fixture).encode("utf-8"), compresslevel=9, mtime=0))
    return path


async def main_async(names: Sequence[str]) -> int:
    unknown = [name for name in names if name not in CAPTURES]
    if unknown:
        print(f"No capture defined for {unknown}; known: {sorted(CAPTURES)}", file=sys.stderr)
        return 2

    print(f"Capturing board fixtures into {FIXTURE_DIR}")
    written = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for ats in names:
            fixture = await record(ats, CAPTURES[ats], client)
            if fixture is None:
                continue
            path = write_fixture(ats, fixture)
            print(f"  {ats:12} -> {path.name} ({path.stat().st_size // 1024} KB gzipped)")
            written += 1
    print(f"\nWrote {written} of {len(names)} fixture(s).")
    return 0 if written == len(names) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one real board per ATS as test fixtures.")
    parser.add_argument(
        "--ats",
        default=",".join(CAPTURES),
        help="Comma-separated ATSes to record (default: every one with a capture defined)",
    )
    args = parser.parse_args()
    names = [name.strip() for name in args.ats.split(",") if name.strip()]
    return asyncio.run(main_async(names))


if __name__ == "__main__":
    raise SystemExit(main())
