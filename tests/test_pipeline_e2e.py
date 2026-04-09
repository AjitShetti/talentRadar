"""
tests/test_pipeline_e2e.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
End-to-end test for the TalentRadar ingestion pipeline.

Tests are organised in 5 layers — each can be run independently:

  Layer 1 — Unit:    Pydantic schema validation (no external services)
  Layer 2 — Scraper: Tavily API connectivity + raw file save
  Layer 3 — Parser:  Groq LLM JD extraction
  Layer 4 — Postgres: DB connectivity + job upsert
  Layer 5 — ChromaDB: embedding upsert + semantic search
  Layer 6 — Full E2E: Tavily → LLM → Postgres → ChromaDB (real API keys)

Run options
-----------
  # Full suite (requires real API keys in .env):
  python tests/test_pipeline_e2e.py

  # Skip slow/external steps (just schemas + connectivity):
  python tests/test_pipeline_e2e.py --quick

  # Single layer:
  python tests/test_pipeline_e2e.py --layer 4
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Make project root importable ─────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline_e2e")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PASS = "✅"
_FAIL = "❌"
_SKIP = "⏭️ "

results: list[tuple[str, str, str]] = []   # (layer, name, status)


def ok(layer: str, name: str, detail: str = "") -> None:
    msg = f"{_PASS} [{layer}] {name}"
    if detail:
        msg += f"  →  {detail}"
    print(msg)
    results.append((layer, name, "PASS"))


def fail(layer: str, name: str, err: str) -> None:
    print(f"{_FAIL} [{layer}] {name}  →  {err}")
    results.append((layer, name, "FAIL"))


def skip(layer: str, name: str, reason: str) -> None:
    print(f"{_SKIP} [{layer}] {name}  →  {reason}")
    results.append((layer, name, "SKIP"))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — Schema unit tests (no external services)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer1_schemas() -> None:
    print("\n── Layer 1: Pydantic Schemas ─────────────────────────────────────")
    from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult

    # 1a: RawJobResult construction
    try:
        r = RawJobResult(
            title="SWE at Stripe",
            url="https://stripe.com/jobs/123",
            content="We are hiring...",
            score=0.92,
        )
        assert r.best_content == "We are hiring..."
        ok("L1", "RawJobResult construction + best_content")
    except Exception as e:
        fail("L1", "RawJobResult construction", str(e))

    # 1b: ParsedJobDescription — deduplication
    try:
        jd = ParsedJobDescription(
            title="Senior Engineer",
            company="Stripe",
            skills=["Python", "Go", "Python", "go", "SQL"],
            raw_text="raw jd text",
        )
        assert jd.skills == ["Python", "Go", "SQL"], f"Got {jd.skills}"
        ok("L1", "Skill deduplication (case-insensitive)", f"skills={jd.skills}")
    except Exception as e:
        fail("L1", "Skill deduplication", str(e))

    # 1c: Salary auto-swap
    try:
        jd2 = ParsedJobDescription(
            title="DS",
            company="Acme",
            salary_min=200000,
            salary_max=80000,  # inverted
            raw_text="text",
        )
        assert jd2.salary_min == 80000 and jd2.salary_max == 200000
        ok("L1", "Salary range auto-swap", f"{jd2.salary_min} → {jd2.salary_max}")
    except Exception as e:
        fail("L1", "Salary auto-swap", str(e))

    # 1d: Enum normalisation
    try:
        jd3 = ParsedJobDescription(
            title="Intern",
            company="X",
            employment_type="Full-Time",
            seniority="Senior",
            raw_text="text",
        )
        assert jd3.employment_type == "full_time"
        assert jd3.seniority == "senior"
        ok("L1", "Enum normalisation", f"emp={jd3.employment_type} sen={jd3.seniority}")
    except Exception as e:
        fail("L1", "Enum normalisation", str(e))

    # 1e: to_job_kwargs mapping
    try:
        jd4 = ParsedJobDescription(
            title="ML Eng",
            company="DeepMind",
            skills=["PyTorch", "Python"],
            raw_text="text",
        )
        kw = jd4.to_job_kwargs()
        assert all(k in kw for k in ["title", "skills", "is_remote", "salary_min"])
        ok("L1", "to_job_kwargs()", f"keys={list(kw.keys())[:5]}…")
    except Exception as e:
        fail("L1", "to_job_kwargs()", str(e))

    # 1f: JSON extraction strategies
    try:
        from ingestion.parsers.jd_parser import JDParser
        # Strategy 1: clean JSON
        d = JDParser._extract_json('{"title": "SWE", "company": "Google"}')
        assert d["title"] == "SWE"
        # Strategy 2: prose + JSON
        d2 = JDParser._extract_json('Here it is: {"title": "PM", "company": "Meta"}')
        assert d2["company"] == "Meta"
        # Strategy 3: markdown fence
        d3 = JDParser._extract_json("```json\n{\"title\": \"DS\"}\n```")
        assert d3["title"] == "DS"
        ok("L1", "_extract_json (3 strategies)", "clean / prose / markdown all work")
    except Exception as e:
        fail("L1", "_extract_json", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — Tavily scraper (requires TAVILY_API_KEY)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer2_tavily(tmp_dir: Path) -> None:
    print("\n── Layer 2: Tavily Scraper ───────────────────────────────────────")
    from config.settings import get_settings
    settings = get_settings()

    if not settings.tavily_api_key or settings.tavily_api_key.startswith("your_"):
        skip("L2", "Tavily search", "TAVILY_API_KEY not set in .env")
        skip("L2", "Raw file save", "skipped — no API key")
        return

    try:
        from ingestion.scrapers.tavily_client import TavilyJobScraper
        with TavilyJobScraper(raw_data_dir=tmp_dir) as scraper:
            results_list = scraper.search_jobs(
                "Software Engineer", location="Remote", count=2
            )
        assert len(results_list) > 0, "Tavily returned 0 results"
        ok("L2", "search_jobs()", f"got {len(results_list)} results | first: {results_list[0].title[:40]!r}")
    except Exception as e:
        fail("L2", "search_jobs()", str(e))
        return

    try:
        paths = scraper.save_raw(
            results_list,
            run_id="test-run-001",
            role="Software Engineer",
            location="Remote",
        )
        assert len(paths) > 0
        assert paths[0].exists()
        first_data = json.loads(paths[0].read_text())
        assert "url" in first_data and "content" in first_data
        ok(
            "L2", "save_raw() → /data/raw/",
            f"saved {len(paths)} files | path={paths[0]}"
        )
    except Exception as e:
        fail("L2", "save_raw()", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — LLM Parser (requires GROQ_API_KEY)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer3_parser() -> None:
    print("\n── Layer 3: LLM JD Parser ────────────────────────────────────────")
    from config.settings import get_settings
    settings = get_settings()

    if not settings.groq_api_key or settings.groq_api_key.startswith("your_"):
        skip("L3", "parse_jd()", "GROQ_API_KEY not set in .env")
        return

    sample_jd = """
    Senior Python Engineer — Payments Platform
    Stripe | New York, NY (Remote OK)

    We're looking for a Python engineer to join our Payments team.

    Requirements:
    - 5+ years Python, Django, FastAPI
    - PostgreSQL, Redis, Kafka experience
    - REST API design expertise
    - AWS deployment (ECS, Lambda)

    Compensation: $160,000 – $200,000 / year + equity
    Full-time permanent role.
    """

    try:
        from ingestion.parsers.jd_parser import JDParser
        parser = JDParser()
        jd = parser.parse_jd(sample_jd, source_url="https://stripe.com/jobs/test-001")

        assert jd.title, "title is empty"
        assert jd.company, "company is empty"
        assert len(jd.skills) > 0, "no skills extracted"

        ok("L3", "parse_jd() returned ParsedJobDescription",
           f"title={jd.title!r} company={jd.company!r}")
        ok("L3", f"Skills extracted ({len(jd.skills)})", f"{jd.skills}")
        ok("L3", f"Salary: {jd.salary!r}", f"min={jd.salary_min} max={jd.salary_max} {jd.salary_currency}")
        ok("L3", f"Seniority: {jd.seniority!r}", f"type={jd.employment_type} remote={jd.is_remote}")
    except Exception as e:
        fail("L3", "parse_jd()", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4 — PostgreSQL (requires running postgres container)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer4_postgres() -> None:
    print("\n── Layer 4: PostgreSQL ───────────────────────────────────────────")

    async def _run() -> None:
        # ── 4a: Basic connectivity ───────────────────────────────────────
        try:
            from storage.database import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                from sqlalchemy import text
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1
            ok("L4", "DB connection", "SELECT 1 → OK")
        except Exception as e:
            fail("L4", "DB connection", str(e))
            return

        # ── 4b: Company + IngestionRun + Job upsert ──────────────────────
        try:
            from storage.database import AsyncSessionLocal
            from storage.models import EmploymentType, IngestionStatus, SeniorityLevel
            from storage.repository import UnitOfWork

            async with AsyncSessionLocal() as session:
                uow = UnitOfWork(session)

                company, created = await uow.companies.upsert_by_domain(
                    domain="test-e2e-company.talentradar.internal",
                    defaults={"name": "E2E Test Company"},
                )
                await session.commit()

                # IngestionRun.create() — use only real column names
                # Note: avoid 'metadata' kwarg — it's a reserved SQLAlchemy attr
                run = await uow.ingestion_runs.create(
                    source="test_e2e",
                    status=IngestionStatus.RUNNING,
                    started_at=datetime.now(tz=timezone.utc),
                    run_config={"airflow_run_id": "test-run-e2e"},
                )
                await session.commit()

                job, inserted = await uow.jobs.upsert_by_external_id(
                    external_id="test-e2e-job-001",
                    source="test_e2e",
                    defaults={
                        "title": "E2E Test Engineer",
                        "company_id": company.id,
                        "ingestion_run_id": run.id,
                        "description_raw": "Sample JD for e2e testing",
                        "description_clean": "Sample JD for e2e testing",
                        "skills": ["Python", "FastAPI", "Docker"],
                        "is_remote": True,
                        "location_raw": "Remote",
                        "salary_raw": "$100k-$150k",
                        "salary_min": 100000.0,
                        "salary_max": 150000.0,
                        "salary_currency": "USD",
                        # Cast to ORM enums to avoid type mismatch
                        "employment_type": EmploymentType.FULL_TIME,
                        "seniority": SeniorityLevel.MID,
                    },
                )
                await session.commit()

            ok("L4", "Company upsert", f"id={company.id} created={created}")
            ok("L4", "IngestionRun created", f"id={run.id} source={run.source!r}")
            ok("L4", "Job upsert", f"id={job.id} inserted={inserted} title={job.title!r}")

        except Exception as e:
            import traceback
            fail("L4", "Company + Job upsert", f"{e}\n{traceback.format_exc()[-300:]}")
            return

        # ── 4c: Read-back by external_id ─────────────────────────────────
        try:
            from storage.database import AsyncSessionLocal
            from storage.repository import JobRepository

            async with AsyncSessionLocal() as session:
                repo = JobRepository(session)
                fetched = await repo.get_by_external_id("test-e2e-job-001", "test_e2e")
                assert fetched is not None
                assert fetched.title == "E2E Test Engineer"
                assert fetched.skills == ["Python", "FastAPI", "Docker"]
                assert fetched.salary_min == 100000.0

            ok("L4", "Read-back by external_id",
               f"title={fetched.title!r} salary_min={fetched.salary_min} skills={fetched.skills}")
        except Exception as e:
            fail("L4", "Read-back query", str(e))

        # ── 4d: SQL verification query (direct psycopg) ──────────────────
        try:
            from storage.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                rows = (await session.execute(
                    text("SELECT title, skills, salary_min, salary_currency "
                         "FROM jobs WHERE external_id = 'test-e2e-job-001' AND source = 'test_e2e'")
                )).fetchall()
                assert len(rows) == 1
                row = rows[0]
                ok("L4", "Raw SQL read-back",
                   f"title={row[0]!r} skills={row[1]} salary={row[2]} {row[3]}")
        except Exception as e:
            fail("L4", "Raw SQL read-back", str(e))

        # ── 4e: Job count ────────────────────────────────────────────────
        try:
            from storage.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                total = (await session.execute(text("SELECT COUNT(*) FROM jobs"))).scalar()
                runs = (await session.execute(text("SELECT COUNT(*) FROM ingestion_runs"))).scalar()
                companies = (await session.execute(text("SELECT COUNT(*) FROM companies"))).scalar()
            ok("L4", "DB table counts",
               f"jobs={total} | ingestion_runs={runs} | companies={companies}")
        except Exception as e:
            fail("L4", "DB table counts", str(e))

    asyncio.run(_run())


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 5 — ChromaDB (requires running chromadb container)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer5_chromadb() -> None:
    print("\n── Layer 5: ChromaDB ─────────────────────────────────────────────")

    try:
        from ingestion.embeddings.chroma_store import ChromaJobStore
        store = ChromaJobStore()
        count_before = store.count()
        ok("L5", "ChromaDB connection", f"collection has {count_before} documents")
    except Exception as e:
        fail("L5", "ChromaDB connection", str(e))
        return

    try:
        test_id = "test-e2e-chroma-001"
        store.add(
            job_id=test_id,
            text="Senior Python Engineer at Stripe, San Francisco. "
                 "Requirements: Python, FastAPI, PostgreSQL, Kubernetes, AWS. "
                 "5+ years experience. Remote OK. Salary $160k-$200k.",
            metadata={
                "title": "Senior Python Engineer",
                "company": "Stripe",
                "location": "San Francisco",
                "is_remote": True,
                "skills_str": "Python, FastAPI, PostgreSQL, Kubernetes, AWS",
                "seniority": "senior",
                "employment_type": "full_time",
            },
        )
        count_after = store.count()
        assert count_after >= count_before, "Count didn't increase after upsert"
        ok("L5", "Upsert document", f"count: {count_before} → {count_after}")
    except Exception as e:
        fail("L5", "Upsert document", str(e))
        return

    try:
        fetched = store.get(test_id)
        assert fetched is not None
        assert fetched["metadata"]["company"] == "Stripe"
        ok("L5", "Get by ID", f"company={fetched['metadata']['company']!r}")
    except Exception as e:
        fail("L5", "Get by ID", str(e))

    try:
        search_results = store.search("python backend engineer remote", n_results=3)
        assert len(search_results) > 0
        top = search_results[0]
        ok(
            "L5", "Semantic search",
            f"top result: {top['metadata'].get('title')!r} "
            f"@ {top['metadata'].get('company')!r} "
            f"dist={top['distance']:.4f}"
        )
    except Exception as e:
        fail("L5", "Semantic search", str(e))

    try:
        count_final = store.count()
        ok("L5", "Final ChromaDB document count", f"{count_final} total documents")
    except Exception as e:
        fail("L5", "Document count", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 6 — Full E2E mini-pipeline (Tavily → Parser → Postgres → ChromaDB)
# ─────────────────────────────────────────────────────────────────────────────

def test_layer6_full_pipeline(tmp_dir: Path) -> None:
    print("\n── Layer 6: Full Pipeline (Tavily → LLM → Postgres → ChromaDB) ──")
    from config.settings import get_settings
    settings = get_settings()

    if not settings.tavily_api_key or settings.tavily_api_key.startswith("your_"):
        skip("L6", "Full pipeline", "TAVILY_API_KEY not set — skipping end-to-end run")
        return
    if not settings.groq_api_key or settings.groq_api_key.startswith("your_"):
        skip("L6", "Full pipeline", "GROQ_API_KEY not set — skipping end-to-end run")
        return

    # Step 1 — Fetch
    try:
        from ingestion.scrapers.tavily_client import TavilyJobScraper
        with TavilyJobScraper(raw_data_dir=tmp_dir) as scraper:
            raw_results = scraper.search_jobs("Data Scientist", "Remote", count=2)
            scraper.save_raw(
                raw_results,
                run_id="e2e-full-001",
                role="Data Scientist",
                location="Remote",
            )
        ok("L6", "Step 1 — Tavily fetch", f"{len(raw_results)} results fetched")
    except Exception as e:
        fail("L6", "Step 1 — Tavily fetch", str(e))
        return

    # Step 2 — Parse
    try:
        from ingestion.parsers.jd_parser import JDParser
        parser = JDParser()
        parsed_jds = parser.batch_parse(raw_results[:1])  # limit to 1 for speed
        assert len(parsed_jds) > 0
        ok("L6", "Step 2 — LLM parse",
           f"title={parsed_jds[0].title!r} skills={parsed_jds[0].skills[:3]}")
    except Exception as e:
        fail("L6", "Step 2 — LLM parse", str(e))
        return

    # Step 3 — Save to Postgres
    async def _pg_save() -> str | None:
        from storage.database import AsyncSessionLocal
        from storage.models import IngestionStatus
        from storage.repository import UnitOfWork

        pjd = parsed_jds[0]
        async with AsyncSessionLocal() as session:
            uow = UnitOfWork(session)
            company, _ = await uow.companies.upsert_by_domain(
                domain=f"{pjd.company.lower().replace(' ', '-')}.talentradar.internal",
                defaults={"name": pjd.company},
            )
            run = await uow.ingestion_runs.create(
                source="tavily",
                status=IngestionStatus.RUNNING,
                started_at=datetime.now(tz=timezone.utc),
            )
            ext_id = hashlib.md5((pjd.source_url or pjd.title + pjd.company).encode()).hexdigest()
            job_kw = pjd.to_job_kwargs()
            job_kw.update({
                "company_id": company.id,
                "ingestion_run_id": run.id,
                "source": "tavily",
            })
            job, created = await uow.jobs.upsert_by_external_id(
                external_id=ext_id, source="tavily", defaults=job_kw
            )
            await uow.ingestion_runs.finish(
                run.id,
                status=IngestionStatus.SUCCESS,
                jobs_inserted=1 if created else 0,
                jobs_updated=0 if created else 1,
            )
            await session.commit()
            return str(ext_id)

    try:
        ext_id = asyncio.run(_pg_save())
        ok("L6", "Step 3 — Saved to Postgres",
           f"external_id={ext_id[:12]}… title={parsed_jds[0].title!r}")
    except Exception as e:
        fail("L6", "Step 3 — Save to Postgres", str(e))
        ext_id = None

    # Step 4 — Embed to ChromaDB
    try:
        from ingestion.embeddings.chroma_store import ChromaJobStore
        pjd = parsed_jds[0]
        job_id = hashlib.md5(
            (pjd.source_url or pjd.title + pjd.company).encode()
        ).hexdigest()
        store = ChromaJobStore()
        store.add(
            job_id=job_id,
            text=pjd.raw_text[:4096],
            metadata={
                "title": pjd.title,
                "company": pjd.company,
                "location": pjd.location or "",
                "is_remote": pjd.is_remote,
                "skills_str": ", ".join(pjd.skills),
                "seniority": pjd.seniority or "",
                "employment_type": pjd.employment_type or "",
            },
        )

        # Verify it's searchable
        search_res = store.search(pjd.title, n_results=1)
        ok("L6", "Step 4 — Embedded to ChromaDB",
           f"count={store.count()} | search returned {len(search_res)} result(s)")
    except Exception as e:
        fail("L6", "Step 4 — Embed to ChromaDB", str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary() -> int:
    """Print test result table and return exit code (0=all pass, 1=any fail)."""
    print("\n" + "═" * 60)
    print("  TEST SUMMARY")
    print("═" * 60)
    passed = sum(1 for _, _, s in results if s == "PASS")
    failed = sum(1 for _, _, s in results if s == "FAIL")
    skipped = sum(1 for _, _, s in results if s == "SKIP")
    for layer, name, status in results:
        icon = _PASS if status == "PASS" else (_FAIL if status == "FAIL" else _SKIP)
        print(f"  {icon}  [{layer}] {name}")
    print("─" * 60)
    print(f"  {_PASS} {passed} passed  |  {_FAIL} {failed} failed  |  {_SKIP} {skipped} skipped")
    print("═" * 60)
    return 1 if failed else 0


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TalentRadar pipeline e2e tests")
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip layers that require real API keys (L2, L3, L6)"
    )
    parser.add_argument(
        "--layer", type=int, choices=[1, 2, 3, 4, 5, 6],
        help="Run only the specified layer"
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        run_all = args.layer is None

        if run_all or args.layer == 1:
            test_layer1_schemas()

        if (run_all or args.layer == 2) and not args.quick:
            test_layer2_tavily(tmp_dir)
        elif args.quick and (run_all or args.layer == 2):
            skip("L2", "Tavily scraper", "--quick mode")

        if (run_all or args.layer == 3) and not args.quick:
            test_layer3_parser()
        elif args.quick and (run_all or args.layer == 3):
            skip("L3", "LLM parser", "--quick mode")

        if run_all or args.layer == 4:
            test_layer4_postgres()

        if run_all or args.layer == 5:
            test_layer5_chromadb()

        if (run_all or args.layer == 6) and not args.quick:
            test_layer6_full_pipeline(tmp_dir)
        elif args.quick and (run_all or args.layer == 6):
            skip("L6", "Full pipeline", "--quick mode")

    exit_code = print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
