"""
scripts/refresh_events.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Run the Events refresh steps locally, the same ones the ``events`` workflow runs against
the API (``POST /api/v1/ingest/events/refresh``).

    python scripts/refresh_events.py --step all
    python scripts/refresh_events.py --step gdg --limit 10
    python scripts/refresh_events.py --step purge --source eventbrite

``all`` runs, in order: luma, gdg, eventbrite, link, relevance, prune. ``purge`` deletes
stored Eventbrite rows, so it never runs as part of ``all`` and needs ``--source eventbrite``.
Without ``--limit`` each step runs with its own ceiling (the limits the workflow uses). Each
step prints its counts as one line of JSON; a failing step prints its error and the next one
still runs. The exit status is 1 if any step reported an ``error``.

The repository root is put on ``sys.path`` first, so the script reads this checkout and not
whichever copy is installed, from any working directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.events.refresh import MAX_LIMIT, STEPS, run_step
from storage.database import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

#: What ``--step all`` runs: everything the workflow runs. ``purge`` is deliberate, never routine.
ROUTINE_STEPS = tuple(step for step in STEPS if step != "purge")


async def run(step: str, limit: int | None, source: str | None) -> int:
    """Run ``step`` (or every routine step); the exit status."""
    names = ROUTINE_STEPS if step == "all" else (step,)
    failed = 0
    for name in names:
        result = await run_step(name, MAX_LIMIT[name] if limit is None else limit, source=source)
        failed += "error" in result
        print(json.dumps(result))
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh the Events tables.")
    parser.add_argument("--step", default="all", choices=["all", *STEPS])
    parser.add_argument(
        "--limit", type=int, default=None, help="cap per step; default: each step's own ceiling"
    )
    parser.add_argument("--source", default=None, help="purge only: eventbrite")
    return parser


async def _main(step: str, limit: int | None, source: str | None) -> int:
    try:
        return await run(step, limit, source)
    finally:
        await engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(_main(args.step, args.limit, args.source))


if __name__ == "__main__":
    raise SystemExit(main())
