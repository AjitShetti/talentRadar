"""
scripts/capture_board_fixtures.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Record one real board per ATS so the board-reader tests can replay it offline.

    python scripts/capture_board_fixtures.py --ats greenhouse,ashby,lever
    python scripts/capture_board_fixtures.py --ats lever

Each ATS is asked for exactly what ``ingestion/boards/<ats>.py`` asks for: one
request, with the project's User-Agent, no redirects followed. The response is
stored as it came back, in
``tests/fixtures/boards/<ats>.json.gz``::

    {"ats": ..., "board": ..., "requests": {<url>: {"status": 200,
                                                     "headers": {...}, "body": "<text>"}}}

A request's key is its full URL, query string included, built the way
``scripts/capture_fixtures.py::_request_key`` builds it (sorted ``k=v`` pairs
after a ``?``). Tests look a request up by ``str(request.url)``, which is the
same string for the single-parameter requests here.

The one edit is for privacy: a Greenhouse job's custom ``metadata`` fields can hold
employees' names, so ``scripts/fixture_privacy.py::scrub_greenhouse_metadata`` blanks
every field not known to be about the role. No reader looks at ``metadata``.

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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# Run as a file the script's own folder is sys.path[0], so the repository root
# is added here, as scripts/capture_fixtures.py does.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fixture_privacy import scrub_greenhouse_metadata

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "boards"

USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
TIMEOUT_SECONDS = 30.0
#: Same ceiling the readers enforce; a board over it would be an ``error`` read.
MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Capture:
    """One board to record and the request its reader makes."""

    board: str
    url: str
    params: dict[str, str] | None = None


#: One real Indian employer per ATS (2026-11-23: Sigmoid 47 roles, Sarvam 42,
#: Meesho 47, almost all in India). Later readers add their own entries.
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
}


def request_key(url: str, params: dict[str, str] | None) -> str:
    """The same key ``scripts/capture_fixtures.py::_request_key`` gives a request."""
    if not params:
        return url
    return f"{url}?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def fixture_path(ats: str) -> Path:
    return FIXTURE_DIR / f"{ats}.json.gz"


async def record(ats: str, capture: Capture, client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Ask the board once and return the fixture, or ``None`` when it proves nothing."""
    try:
        response = await client.get(
            capture.url, params=capture.params, headers={"User-Agent": USER_AGENT}
        )
    except httpx.HTTPError as exc:
        print(f"  {ats:12} FAILED: {type(exc).__name__}: {exc}")
        return None
    if response.status_code != 200:
        print(f"  {ats:12} answered {response.status_code}, not recording it")
        return None
    if len(response.content) > MAX_BODY_BYTES:
        print(f"  {ats:12} body is {len(response.content)} bytes, over the {MAX_BODY_BYTES} cap")
        return None
    try:
        payload = response.json()
    except ValueError:
        print(f"  {ats:12} body is not JSON, not recording it")
        return None
    postings = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(postings, list) or not postings:
        print(f"  {ats:12} returned no postings, not recording a fixture that proves nothing")
        return None

    body = response.text
    if ats == "greenhouse":
        # Custom job fields can hold employees' names; nothing else is edited.
        body = json.dumps(scrub_greenhouse_metadata(payload), ensure_ascii=False)

    key = request_key(capture.url, capture.params)
    print(f"  {ats:12} {len(postings):>3} postings, {len(response.content) // 1024} KB")
    return {
        "ats": ats,
        "board": capture.board,
        "requests": {
            key: {
                "status": response.status_code,
                "headers": {"content-type": response.headers.get("content-type", "")},
                "body": body,
            }
        },
    }


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
