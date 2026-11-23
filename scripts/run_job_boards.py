"""
scripts/run_job_boards.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Run one job-board roster step locally, the same one the ``job-boards`` workflow runs against
the API (``POST /api/v1/ingest/job-boards``).

    python scripts/run_job_boards.py --step sweep
    python scripts/run_job_boards.py --step prune --limit 100
    make job-boards step=probe

Without ``--limit`` the step takes the limit its scheduled run uses (``DEFAULT_LIMIT``, the same
numbers as ``.github/workflows/job-boards.yml``). ``run_step`` caps any limit at the step's own
ceiling. The step prints its counts as one line of JSON; the exit status is 1 if it reported an
``error``.

This makes real requests to the ATSes and writes to whatever database the environment names,
so run it on purpose. The repository root is put on ``sys.path`` first, so the script reads this
checkout and not an installed copy, from any working directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.job_boards.steps import STEPS, run_step
from storage.database import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

#: The limit each scheduled run uses, when ``--limit`` is not given.
DEFAULT_LIMIT: dict[str, int] = {
    "open_list": 20000,
    "yc": 60,
    "probe": 40,
    "sweep": 300,
    "prune": 5000,
}


async def run(step: str, limit: int | None) -> int:
    """Run ``step`` once and print its counts; the exit status."""
    result = await run_step(step, DEFAULT_LIMIT[step] if limit is None else limit)
    print(json.dumps(result))
    return 1 if "error" in result else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one job-board roster step.")
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="boards or rows to take; default: the scheduled run's limit for the step",
    )
    return parser


async def _main(step: str, limit: int | None) -> int:
    try:
        return await run(step, limit)
    finally:
        await engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(_main(args.step, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
