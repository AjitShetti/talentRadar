"""
scripts/clean_query_skills.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Clear job skills that were the user's search words, not the posting's.

Until 2026-10-26 the live scrapers (``ingestion/scrapers/``) filled ``jobs.skills`` with
``[w for w in query.split() if len(w) > 2]`` whenever the board gave none, so a search for
"What should I focus on today?" stored ``["What", "should", "focus", "today?"]`` on every
posting it returned. Enrichment never replaced them: it fills skills only when they are empty.

No query is stored, so a row is picked by one of two signals:

* ``certain``: the source never supplies skills (the live ATS, LinkedIn, Freshersworld,
  Indeed and Instahyre scrapers) and the row is still ``raw``: nothing but the query can have
  put skills on it.
* ``shared``: the exact same skills list sits on rows of two or more companies from the live
  scrapers. One search writes one list onto every posting it returns; two employers' own
  postings do not repeat each other's list word for word.

    python scripts/clean_query_skills.py                     # report only, changes nothing
    python scripts/clean_query_skills.py --apply             # back up, then clear
    python scripts/clean_query_skills.py --restore FILE      # put a backup back

``--apply`` writes every changed row's old skills to a JSON backup before it touches
anything, then clears ``jobs.skills`` and the ``skills_str`` copy in ``job_embeddings``
metadata in one transaction. It reads whatever database ``DATABASE_URL`` (or the
``POSTGRES_*`` settings) names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from storage.database import engine

#: Live scrapers whose rows only ever got skills from the query.
NEVER_SUPPLIES = ("linkedin", "freshersworld", "indeed_india", "instahyre")
NEVER_SUPPLIES_PREFIXES = ("greenhouse:", "ashby:", "lever:")
#: Live scrapers that can carry the board's own skills (Foundit's skills, Naukri's tags).
MAY_SUPPLY = ("foundit", "naukri")


@dataclass(frozen=True)
class Row:
    id: str
    #: The row's ``job_embeddings.id``: ``jobs.embedding_id``, or ``external_id`` without one.
    embedding_id: str | None
    source: str
    company_id: str | None
    enrichment_status: str | None
    skills: tuple[str, ...]


def never_supplies(source: str) -> bool:
    return source in NEVER_SUPPLIES or source.startswith(NEVER_SUPPLIES_PREFIXES)


def is_live_scraper(source: str) -> bool:
    return never_supplies(source) or source in MAY_SUPPLY


def pick(rows: Iterable[Row]) -> dict[str, str]:
    """Row id -> the signal that picked it, for rows whose skills were the search words."""
    rows = [r for r in rows if r.skills and is_live_scraper(r.source)]
    companies: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for r in rows:
        companies[r.skills].add(r.company_id or f"row:{r.id}")
    picked: dict[str, str] = {}
    for r in rows:
        if never_supplies(r.source) and r.enrichment_status == "raw":
            picked[r.id] = "certain"
        elif len(companies[r.skills]) >= 2:
            picked[r.id] = "shared"
    return picked


_SELECT = sa.text(
    "SELECT id::text, coalesce(embedding_id, external_id), source, company_id::text, "
    "enrichment_status, skills "
    "FROM jobs WHERE cardinality(skills) > 0"
)


async def _load() -> list[Row]:
    async with engine.connect() as conn:
        result = await conn.execute(_SELECT)
        return [
            Row(r[0], r[1], r[2] or "", r[3], r[4], tuple(r[5] or ()))
            for r in result
        ]


def _report(rows: list[Row], picked: dict[str, str]) -> None:
    live = [r for r in rows if is_live_scraper(r.source)]
    print(f"rows with skills: {len(rows)}  from the live scrapers: {len(live)}")
    by_signal: dict[str, int] = defaultdict(int)
    for signal in picked.values():
        by_signal[signal] += 1
    print(f"picked: {len(picked)}  {dict(by_signal)}")
    print(f"left alone among the live scrapers: {len(live) - len(picked)}")
    groups: dict[tuple[str, ...], list[Row]] = defaultdict(list)
    for r in rows:
        if r.id in picked:
            groups[r.skills].append(r)
    print("most common picked lists (rows, companies, sources):")
    for skills, members in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:25]:
        sources = sorted({m.source.split(":")[0] for m in members})
        firms = len({m.company_id for m in members})
        print(f"  {len(members):5d} {firms:4d}  {','.join(sources):30s} {list(skills)}")
    kept: dict[tuple[str, ...], int] = defaultdict(int)
    for r in live:
        if r.id not in picked:
            kept[r.skills] += 1
    print("sample of lists left alone:")
    for skills, n in sorted(kept.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:5d}  {list(skills)}")


def _write_backup(chosen: list[Row], picked: dict[str, str], backup: Path) -> None:
    backup.write_text(
        json.dumps(
            [
                {"id": r.id, "embedding_id": r.embedding_id, "skills": list(r.skills),
                 "signal": picked[r.id]}
                for r in chosen
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"backup: {backup} ({len(chosen)} rows)")


async def _apply(rows: list[Row], picked: dict[str, str], backup: Path) -> None:
    chosen = [r for r in rows if r.id in picked]
    _write_backup(chosen, picked, backup)
    ids = [r.id for r in chosen]
    embedding_ids = [r.embedding_id for r in chosen if r.embedding_id]
    async with engine.begin() as conn:
        has_embeddings = await conn.scalar(sa.text("SELECT to_regclass('job_embeddings') IS NOT NULL"))
        jobs = await conn.execute(
            sa.text("UPDATE jobs SET skills = '{}' WHERE id::text = ANY(:ids)"), {"ids": ids}
        )
        embedded = 0
        if has_embeddings and embedding_ids:
            result = await conn.execute(
                sa.text(
                    "UPDATE job_embeddings SET metadata = jsonb_set(metadata, '{skills_str}', '\"\"') "
                    "WHERE id = ANY(:ids) AND metadata ? 'skills_str'"
                ),
                {"ids": embedding_ids},
            )
            embedded = result.rowcount
    print(f"cleared: jobs {jobs.rowcount}, job_embeddings metadata {embedded}")


def _read_backup(backup: Path) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = json.loads(backup.read_text(encoding="utf-8"))
    return saved


async def _restore(backup: Path) -> None:
    saved = _read_backup(backup)
    async with engine.begin() as conn:
        has_embeddings = await conn.scalar(sa.text("SELECT to_regclass('job_embeddings') IS NOT NULL"))
        for item in saved:
            await conn.execute(
                sa.text("UPDATE jobs SET skills = :skills WHERE id::text = :id"),
                {"skills": item["skills"], "id": item["id"]},
            )
            if has_embeddings and item.get("embedding_id"):
                await conn.execute(
                    sa.text(
                        "UPDATE job_embeddings SET metadata = jsonb_set(metadata, '{skills_str}', "
                        "to_jsonb(CAST(:s AS text))) WHERE id = :id AND metadata ? 'skills_str'"
                    ),
                    {"s": ", ".join(item["skills"]), "id": item["embedding_id"]},
                )
    print(f"restored {len(saved)} rows from {backup}")


async def run(apply: bool, backup: Path | None, restore: Path | None) -> None:
    try:
        if restore:
            await _restore(restore)
            return
        rows = await _load()
        picked = pick(rows)
        _report(rows, picked)
        if apply and picked:
            stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
            await _apply(rows, picked, backup or Path(f"query-skills-backup-{stamp}.json"))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear job skills that were search words.")
    parser.add_argument("--apply", action="store_true", help="back up, then clear the picked rows")
    parser.add_argument("--backup", type=Path, default=None, help="where --apply writes the backup")
    parser.add_argument("--restore", type=Path, default=None, help="put a backup file back")
    args = parser.parse_args()
    asyncio.run(run(args.apply, args.backup, args.restore))


if __name__ == "__main__":
    main()
