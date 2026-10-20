"""
scripts/refresh_company_map.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Run the company-map refresh steps locally, the same ones the
``company-map`` workflow runs against the API.

    python scripts/refresh_company_map.py --step all --limit 50
    python scripts/refresh_company_map.py --step wikidata
    python scripts/refresh_company_map.py --step news

``all`` runs, in order: wikidata, osm, yc, hiring_list, github_orgs, sites, geocode,
summaries, github, then the news feeds. Each step prints its counts; a failing
step prints its error and the next one still runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from services.company_map.news_ingest import ingest_news
from services.company_map.refresh import STEPS, run_step

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


async def run(step: str, limit: int) -> int:
    steps = [*STEPS, "news"] if step == "all" else [step]
    failed = 0
    for name in steps:
        result = await (ingest_news() if name == "news" else run_step(name, limit))
        failed += "error" in result
        print(json.dumps(result))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the company map.")
    parser.add_argument("--step", default="all", choices=["all", *STEPS, "news"])
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    return asyncio.run(run(args.step, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
