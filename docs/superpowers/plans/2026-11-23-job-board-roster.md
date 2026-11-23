# Job-board roster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 23 hard-coded ATS slugs with a `job_boards` table filled by four discovery steps and read by a paced, scheduled sweep that stores India roles from nine ATSes.

**Architecture:** Pure rules in `domain/job_boards.py` (link detection, slug guesses, scheduling). One adapter per ATS in `ingestion/boards/`. I/O in `services/job_boards/` (store, writer, sweep, discovery, steps). Scheduled by `.github/workflows/job-boards.yml` through `POST /api/v1/ingest/job-boards`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, httpx, pytest (+ pytest-asyncio), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-23-job-board-roster-design.md` (read it first; this plan argues from it).

## Global Constraints

- The nine ATSes, by these exact names: `greenhouse`, `ashby`, `lever`, `workable`, `recruitee`, `keka`, `rippling`, `personio`, `breezy`. No Workday, SmartRecruiters, Teamtailor or Lever EU host.
- Every outbound request: header `User-Agent: TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)`, `follow_redirects=False`, body capped at 2 MB (over the cap is an `error`, never a truncated parse).
- Per-host pace in seconds: greenhouse 0.5, ashby 0.5, lever 1, rippling 1, recruitee 1, personio 1, breezy 1, keka 2, workable 10.
- Keka: only paths under `/careers/` are requested.
- A role is kept only when `domain.geo.is_india(location_text)` is true. The `country` column is never trusted.
- `posted_at` is set only from a date the ATS states. Never `datetime.now()`.
- A board-sourced job's `external_id` is `job_persistence._stable_external_id(job)` (MD5 of the posting URL), never the ATS's own id.
- `jobs.source` for a board row is `f"{ats}:{board}"`.
- No database session is open across a network call.
- Scheduled steps never raise: failures return `{"step": ..., "error": "<ClassName>"}` inside a 200.
- mypy strict, ruff clean, full annotations. Pure code in `domain/` has no I/O imports.
- Run Python tools with the main checkout's interpreter: `/d/projects/talentRadar/.venv/Scripts/python.exe -m pytest <path> -v --no-cov` (written below as `pytest`). Tests that need Postgres are skipped locally without `talentradar_test`; CI runs them.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A board read that returns 200 with an HTML error page or a non-JSON body** (Workable's 429 page, a captive portal): it must be an `error` outcome, never "zero India roles" (which would demote a live board). Test in Task 4.
2. **A role that moves from Bengaluru to "Remote"** between reads: the row must stay (same `external_id`), and must not be closed as absent while it is still on the board. Test in Task 7.
3. **The same posting found by live search and by the sweep** must be one row. Test in Task 7.
4. **A 429 mid-sweep** must stop that ATS's queue for the rest of the run, leave the other ATS queues running, and not advance the unread boards' `next_read_at`. Test in Task 9.
5. **A careers page linking the same board twice, or linking `boards.greenhouse.io.evil.example`**: deduplicated, and the look-alike host ignored. Test in Task 1.

---

### Task 1: Pure rules for boards [sonnet]

**Files:**
- Create: `domain/job_boards.py`
- Test: `tests/test_job_boards_domain.py`

**Interfaces:**
- Produces:
  - `AtsName = Literal["greenhouse","ashby","lever","workable","recruitee","keka","rippling","personio","breezy"]`; `ATS: tuple[AtsName, ...]` in that order.
  - `PACE_SECONDS: Mapping[AtsName, float]` (values in Global Constraints).
  - `LIVE_SEARCH_ATS: frozenset[AtsName] = {"greenhouse","ashby"}`; `PROBE_ATS: tuple[AtsName, ...] = ("greenhouse","ashby","lever","workable","recruitee","keka")`.
  - `BoardStatus = Literal["candidate","active","no_india","gone","blocked"]`; `SWEPT_STATUSES: frozenset[BoardStatus] = {"candidate","active","no_india"}`.
  - `ReadOutcome = Literal["ok","not_found","rate_limited","error"]`.
  - `@dataclass(frozen=True) class BoardRef: ats: AtsName; board: str; region: str | None = None`, property `source -> str` (`f"{ats}:{board}"`).
  - `@dataclass(frozen=True) class Schedule: status: BoardStatus; failures: int; zero_india_reads: int; not_found_reads: int; next_read_at: datetime`.
  - `def boards_in_html(html: str) -> list[BoardRef]`
  - `def board_from_url(url: str) -> BoardRef | None` (one URL; `boards_in_html` uses it on every `href`/`src` and on bare URLs in text)
  - `def slug_candidates(name: str, domain: str | None) -> list[str]`
  - `def next_read(prev: Schedule, outcome: ReadOutcome, india_roles: int, *, now: datetime) -> Schedule`
  - `def is_india_role(location_text: str | None) -> bool` (delegates to `domain.geo.is_india`)
  - `def names_match(a: str, b: str) -> bool` (equal under `domain.company_identity.normalize_name`)

- [ ] **Step 1: Write the failing tests**

```python
def test_boards_in_html_finds_each_ats():
    html = '''<a href="https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited">x</a>
      <a href="https://boards.greenhouse.io/embed/job_board?for=sigmoid">x</a>
      <a href="https://jobs.lever.co/meesho/">x</a><a href="https://jobs.ashbyhq.com/sarvam">x</a>
      <a href="https://apply.workable.com/apna/">x</a><a href="https://lenskartcareers.recruitee.com/">x</a>
      <a href="https://spyneai.keka.com/careers">x</a><a href="https://ats.rippling.com/rippling/jobs">x</a>
      <a href="https://acme.breezy.hr/">x</a><a href="https://acme.jobs.personio.de/">x</a>'''
    assert [(b.ats, b.board, b.region) for b in boards_in_html(html)] == [
        ("greenhouse","razorpaysoftwareprivatelimited",None), ("greenhouse","sigmoid",None),
        ("lever","meesho",None), ("ashby","sarvam",None), ("workable","apna",None),
        ("recruitee","lenskartcareers",None), ("keka","spyneai",None), ("rippling","rippling",None),
        ("breezy","acme",None), ("personio","acme","de")]

def test_boards_in_html_dedupes_and_ignores_lookalikes():
    html = ('<a href="https://jobs.lever.co/cred">a</a><a href="https://jobs.lever.co/cred/abc">b</a>'
            '<a href="https://boards.greenhouse.io.evil.example/x">c</a>'
            '<a href="https://www.recruitee.com/">d</a><a href="https://api.keka.com/careers">e</a>')
    assert [(b.ats, b.board) for b in boards_in_html(html)] == [("lever","cred")]

def test_slug_candidates():
    assert slug_candidates("Groww", "groww.in") == ["groww"]
    assert slug_candidates("Observe.AI", "observe.ai") == ["observe", "observeai", "observe-ai"]
    assert slug_candidates("Cars 24 Services", None) == ["cars24services", "cars-24-services"]
    assert len(slug_candidates("A B C D E", "abcde.com")) <= 4

def test_next_read_india_found_is_active_daily():
    s = next_read(_s("candidate"), "ok", 3, now=NOW)
    assert (s.status, s.zero_india_reads, s.next_read_at) == ("active", 0, NOW + timedelta(hours=24))

def test_next_read_zero_india_backs_off_then_no_india():
    s1 = next_read(_s("candidate"), "ok", 0, now=NOW); assert s1.next_read_at == NOW + timedelta(days=7)
    s2 = next_read(s1, "ok", 0, now=NOW); assert s2.next_read_at == NOW + timedelta(days=14)
    s3 = next_read(s2, "ok", 0, now=NOW); assert (s3.status, s3.next_read_at) == ("no_india", NOW + timedelta(days=30))

def test_next_read_404_twice_is_gone():
    s1 = next_read(_s("active"), "not_found", 0, now=NOW); assert s1.status == "active"
    assert next_read(s1, "not_found", 0, now=NOW).status == "gone"

def test_next_read_failures_back_off_then_blocked():
    s = _s("active")
    for n in range(1, 7):
        s = next_read(s, "rate_limited", 0, now=NOW)
        assert s.next_read_at == NOW + min(timedelta(hours=2**n), timedelta(days=7))
    assert s.status == "blocked"

def test_ok_read_resets_failure_counters():
    s = next_read(Schedule("active", 3, 2, 1, NOW), "ok", 1, now=NOW)
    assert (s.failures, s.zero_india_reads, s.not_found_reads) == (0, 0, 0)
```
(`_s(status)` builds `Schedule(status, 0, 0, 0, NOW)`; `NOW` is a fixed aware UTC datetime.)

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_job_boards_domain.py -v --no-cov` — Expected: FAIL (`ModuleNotFoundError: domain.job_boards`).

- [ ] **Step 3: Implement `domain/job_boards.py`**

Host patterns are exact, as in `domain/posting_urls.py`: parse with `urlsplit`, require `https`/`http`, match `hostname` exactly or by suffix with a single-label subdomain. Subdomain slugs `www, app, api, careers, jobs, help, support` are ignored. Slug charset `[A-Za-z0-9][A-Za-z0-9_-]{0,79}`. Lever and Ashby slugs keep case as given; others are lower-cased. `slug_candidates` order: domain label (first label of the registrable domain), name with non-alphanumerics removed, name words joined by `-`; deduplicated, empty dropped, at most 4. `next_read` rules are the spec's ("Pure rules" section) with `not_found_reads` as the 404 counter (an addition to the spec's table, needed for "two in a row"); a `gone` result sets `next_read_at = now + 30 days`; a `blocked` result keeps the back-off it was given (it is not swept, so the date only records when it would have been).

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_job_boards_domain.py -v --no-cov` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(boards): pure rules for ATS boards`

---

### Task 2: The `job_boards` table [haiku]

**Files:**
- Create: `storage/migrations/versions/026_job_boards.py`
- Modify: `storage/models.py` (new `JobBoard` class after `Company`)
- Test: `tests/test_job_boards_schema.py` (same shape as `tests/test_outreach_pack_schema.py`)

**Interfaces:**
- Consumes: `BoardStatus`, `AtsName` (Task 1) as documentation only; columns are `Text`.
- Produces: ORM `JobBoard` with columns exactly as the spec's table plus `not_found_reads INT NOT NULL DEFAULT 0`: `id uuid pk default gen_random_uuid()`, `ats text not null`, `board text not null`, `region text null`, `company_id uuid null FK companies.id ON DELETE SET NULL`, `company_name text null`, `discovered_via text not null`, `status text not null default 'candidate'`, `india_jobs int null`, `total_jobs int null`, `first_seen_at timestamptz not null default now()`, `last_read_at`, `last_india_at`, `next_read_at timestamptz null`, `failures int not null default 0`, `zero_india_reads int not null default 0`, `not_found_reads int not null default 0`. Unique index `uq_job_boards_ats_board_region` on `(ats, board, coalesce(region, ''))`; index `ix_job_boards_status_next_read` on `(status, next_read_at)`; index `ix_job_boards_company_id`. Python properties on the model: `source -> str` (`f"{ats}:{board}"`) and `ref -> BoardRef`.

- [ ] **Step 1: Write the failing tests**: `test_migration_026_chain` (`revision == "026_job_boards"`, `down_revision == "025_profile_routine"`), `test_upgrade_is_idempotent_sql` (every `CREATE` uses `IF NOT EXISTS`), `test_downgrade_drops_what_upgrade_made`, `test_drift_job_boards_postgres_types` (each column's PostgreSQL type in the migration equals the model's).
- [ ] **Step 2: Run** `pytest tests/test_job_boards_schema.py -v --no-cov` — Expected: FAIL (file not found).
- [ ] **Step 3: Write the migration and the model.** Raw SQL with `IF NOT EXISTS`, like `025_profile_routine.py`; docstring says why the table exists and that no existing table changes.
- [ ] **Step 4: Run** the test file — Expected: PASS. Also `pytest tests -k "migration or schema" --no-cov` — Expected: no new failures.
- [ ] **Step 5: Commit** `feat(boards): job_boards table (migration 026)`

---

### Task 3: Teach the rest of the app the new ATS names [sonnet]

**Files:**
- Modify: `domain/posting_urls.py` (`_PATTERNS`, `_KNOWN_ATS`, `posting_ref`), `domain/liveness.py:46` (`_VERIFIABLE_PREFIXES`), `ingestion/validation.py` (`ALLOWED_JOB_DOMAINS`), `domain/platforms.py:48-49`, `agents/merge_rank.py:40` (`SOURCE_TRUST`), `services/public_roles.py` (the two `Job.source.like(...)` blocks at ~168 and ~217, and `_UNSUPPORTED_MESSAGE`), `services/liveness.py:174-176` (`_load_candidates` `LIKE` clauses)
- Test: extend `tests/test_validation.py`, `tests/test_liveness_domain.py`, `tests/test_platform_filter.py`; create `tests/test_posting_urls_boards.py`

**Interfaces:**
- Consumes: `ATS` from `domain.job_boards` — every list above is derived from it (`ATS` tuple or `f"{a}:%"` per name), not retyped.
- Produces: `parse_posting_url(url)` returns a `PostingRef` for one posting URL per new ATS: `apply.workable.com/{slug}/j/{shortcode}`, `{slug}.recruitee.com/o/{offer-slug}`, `{slug}.keka.com/careers/jobdetails/{id}`, `ats.rippling.com/{slug}/jobs/{uuid}`, `{slug}.breezy.hr/p/{id}`, `{slug}.jobs.personio.(de|com)/job/{id}`. `PostingRef.source` stays `f"{ats}:{company_slug}"`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("url,ats,slug", [
    ("https://apply.workable.com/apna/j/1A2B3C4D5E/", "workable", "apna"),
    ("https://lenskartcareers.recruitee.com/o/store-manager-bengaluru", "recruitee", "lenskartcareers"),
    ("https://spyneai.keka.com/careers/jobdetails/12345", "keka", "spyneai"),
    ("https://ats.rippling.com/rippling/jobs/0b6c1f3e-1a2b-4c5d-8e9f-001122334455", "rippling", "rippling"),
    ("https://acme.breezy.hr/p/0123abcd4567-backend-engineer", "breezy", "acme"),
    ("https://acme.jobs.personio.de/job/998877", "personio", "acme"),
])
def test_new_ats_posting_urls_parse(url, ats, slug):
    ref = parse_posting_url(url); assert (ref.ats, ref.company_slug) == (ats, slug)

def test_new_ats_posting_urls_pass_validation(): ...   # each URL above: validate_job_url(url)[0] is True
def test_every_ats_is_verifiable():                       # all(is_verifiable_source(f"{a}:x") for a in ATS)
def test_every_ats_has_full_trust():                      # SOURCE_TRUST[a] == 1.0 for a in ATS
```
- [ ] **Step 2: Run** them — Expected: FAIL.
- [ ] **Step 3: Implement.** Posting-id shapes: Workable shortcode `[A-Z0-9]{6,12}`, Recruitee offer slug `[a-z0-9-]{1,120}`, Keka id `\d{1,12}`, Rippling uuid, Breezy `[0-9a-f]{8,16}[a-z0-9-]*`, Personio `\d{1,12}`. `_UNSUPPORTED_MESSAGE` names all nine.
- [ ] **Step 4: Run** the four test files plus `pytest tests/test_source_contracts.py tests/test_liveness_service.py -v --no-cov` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(boards): recognise postings on nine ATSes`

---

### Task 4: Adapter framework and Greenhouse, Ashby, Lever [sonnet]

**Files:**
- Create: `ingestion/boards/__init__.py`, `ingestion/boards/base.py`, `ingestion/boards/greenhouse.py`, `ingestion/boards/ashby.py`, `ingestion/boards/lever.py`, `scripts/capture_board_fixtures.py`, `tests/fixtures/boards/{greenhouse,ashby,lever}.json.gz`
- Modify: `ingestion/scrapers/ats_scraper.py` (`fetch_greenhouse_company`, `fetch_ashby_company`, `fetch_lever_company` become calls to `read_board` plus the existing query/location filter)
- Test: `tests/test_board_adapters.py`

**Interfaces:**
- Consumes: `BoardRef`, `ReadOutcome`, `is_india_role` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class BoardRead: outcome: ReadOutcome; roles: list[Job]; total: int; complete: bool; company_name: str | None = None` in `ingestion/boards/base.py`.
  - `async def get_json(client: httpx.AsyncClient, url: str, *, params: dict[str, str] | None = None, method: str = "GET", json_body: dict[str, Any] | None = None) -> tuple[ReadOutcome, Any]` in `base.py`: 2 MB cap, no redirects, UA header; 404/410 → `not_found`; 429 → `rate_limited`; other non-200, 3xx, timeout, non-JSON or HTML body → `error`.
  - `READERS: dict[AtsName, Callable[[httpx.AsyncClient, BoardRef], Awaitable[BoardRead]]]` in `ingestion/boards/__init__.py`, one entry per module as later tasks add them.
  - Each module: `async def read_board(client: httpx.AsyncClient, ref: BoardRef) -> BoardRead` returning **all** roles (India filtering is the caller's), each a `domain.entities.Job` with `source=ref.source`, `source_url` the posting's public URL, `location_raw`, `title`, `description_clean` (from the list response only, cleaned to text), `posted_at` only when stated, `extra_metadata={"company_name": ...}`.
  - `async def read_board_name(client: httpx.AsyncClient, ref: BoardRef) -> str | None` in `ingestion/boards/greenhouse.py` only (`GET /v1/boards/{b}` → `name`), used by the probe. Workable and Keka put their name in `BoardRead.company_name` instead (Task 5).
- Endpoints: Greenhouse `boards-api.greenhouse.io/v1/boards/{b}/jobs?content=true`; Ashby `api.ashbyhq.com/posting-api/job-board/{b}`; Lever `api.lever.co/v0/postings/{b}?mode=json`.

- [ ] **Step 1: Record fixtures.** `scripts/capture_board_fixtures.py --ats greenhouse,ashby,lever` records Sigmoid, Sarvam and Meesho into `tests/fixtures/boards/<ats>.json.gz` (key = URL, value = `{status, headers, body}`), reusing the request-key convention of `scripts/capture_fixtures.py`.
- [ ] **Step 2: Write the failing tests**: replaying each fixture through a mock transport, assert `outcome == "ok"`, `complete is True`, `total == len(roles) > 0`, every role has title, `source_url` passing `validate_job_url`, `source == f"{ats}:{board}"`, and at least one role with `is_india_role(location_raw)`. Plus `test_html_200_is_error` (body `<!DOCTYPE html>` with status 200 → `error`, `roles == []`), `test_429_is_rate_limited`, `test_404_is_not_found`, `test_body_over_cap_is_error`, `test_redirect_is_error`, and `test_greenhouse_content_is_unescaped` (no `&lt;` left in `description_clean`).
- [ ] **Step 3: Run** `pytest tests/test_board_adapters.py -v --no-cov` — Expected: FAIL.
- [ ] **Step 4: Implement** `base.py` and the three modules; parse off the event loop with `ingestion/scrapers/_parsing.py`. Point the three `ATSScraper.fetch_*_company` methods at them.
- [ ] **Step 5: Run** the adapter tests and `pytest tests/test_source_contracts.py -v --no-cov` — Expected: PASS (the `ats_platforms` fixture still replays).
- [ ] **Step 6: Commit** `feat(boards): Greenhouse, Ashby and Lever board readers`

---

### Task 5: Workable, Recruitee and Keka [sonnet]

**Files:**
- Create: `ingestion/boards/workable.py`, `ingestion/boards/recruitee.py`, `ingestion/boards/keka.py`, fixtures `tests/fixtures/boards/{workable,recruitee,keka}.json.gz`
- Modify: `ingestion/boards/__init__.py` (`READERS`)
- Test: extend `tests/test_board_adapters.py`

**Interfaces:**
- Consumes: Task 4's `BoardRead`, `get_json`, `READERS`.
- Produces: `read_board` for each; `company_name` filled from Workable's account `name` and Keka's `careers/api/organization/default/careerportalinfo`. Endpoints: Workable `apply.workable.com/api/v1/widget/accounts/{b}` (posting URL `https://apply.workable.com/{b}/j/{shortcode}/`); Recruitee `https://{b}.recruitee.com/api/offers/` (dates are `"YYYY-MM-DD HH:MM:SS UTC"`); Keka `https://{b}.keka.com/careers/api/jobs/default/active` (location from `jobLocations[].name`/`countryName`, `posted_at` from `publishedOn`, posting URL `https://{b}.keka.com/careers/jobdetails/{id}`).

- [ ] **Step 1: Record fixtures** for Apna, Lenskart (`lenskartcareers`) and Spyne (`spyneai`).
- [ ] **Step 2: Write the failing tests**: the same per-adapter assertions as Task 4, plus `test_recruitee_date_parses` (a `posted_at` that is timezone-aware UTC) and `test_keka_requests_only_careers_paths` (every URL the mock transport saw starts with `https://spyneai.keka.com/careers/`).
- [ ] **Step 3: Run** — Expected: FAIL. **Step 4: Implement.** **Step 5: Run** — Expected: PASS.
- [ ] **Step 6: Commit** `feat(boards): Workable, Recruitee and Keka board readers`

---

### Task 6: Rippling, Personio and Breezy [haiku]

**Files:**
- Create: `ingestion/boards/{rippling,personio,breezy}.py`, fixtures for Rippling (`rippling`), Personio (`personio`, `de`) and Breezy (`breezy`)
- Modify: `ingestion/boards/__init__.py`
- Test: extend `tests/test_board_adapters.py`

**Interfaces:**
- Consumes: Task 4.
- Produces: `read_board` for each. Rippling `ats.rippling.com/api/v2/board/{b}/jobs?page=N`, at most 10 pages, `complete=False` if page 10 still had a next page. Personio `https://{b}.jobs.personio.{region or 'de'}/xml?language=en`, parsed with `xml.etree.ElementTree` (no external entities: use `defusedxml` only if already installed, else reject a body containing `<!DOCTYPE`). Breezy `https://{b}.breezy.hr/json`.

- [ ] **Step 1: Record fixtures.** **Step 2: Write the failing tests** (Task 4's shape; plus `test_rippling_page_cap_is_incomplete` and `test_personio_doctype_is_error`). **Step 3: Run** — FAIL. **Step 4: Implement.** **Step 5: Run** — PASS.
- [ ] **Step 6: Commit** `feat(boards): Rippling, Personio and Breezy board readers`

---

### Task 7: Writing a board's roles [sonnet]

**Files:**
- Create: `services/job_boards/__init__.py`, `services/job_boards/write.py`
- Test: `tests/test_job_boards_write.py` (uses `db_session` from `tests/conftest.py`)

**Interfaces:**
- Consumes: `BoardRead` (Task 4); `JobBoard` (Task 2); `job_persistence._stable_external_id`, `build_job_kwargs`, `embedding_text`, `_embed_quietly`; `liveness.record_sighting`; `uow.jobs.upsert_by_external_id(external_id, source, defaults)`.
- Produces:
  - `@dataclass class WriteSummary: inserted: int = 0; updated: int = 0; closed: int = 0; rejected_url: int = 0; skipped_budget: int = 0`
  - `async def board_company_id(session: AsyncSession, board: JobBoard) -> uuid.UUID`: `board.company_id` if set; else the placeholder company with domain `f"{ats}-{board}.board.talentradar.internal"` (created with `name=board.company_name or board.board`). Never name-matched.
  - `async def write_board_roles(session: AsyncSession, board: JobBoard, read: BoardRead, *, allow_new: bool) -> tuple[WriteSummary, list[dict[str, Any]]]`: India roles only, at most 200; existing rows (by `(external_id, source)`) get `build_job_kwargs` (so `last_seen_at`, `closed_at=None`) and a sighting; new rows only when `allow_new`, else counted in `skipped_budget`. Returns embedding items for new rows only. When `read.complete`, every stored row with `source == board.source`, `closed_at IS NULL` and `external_id` not in this read gets `closed_at = now`. Does not commit.

- [ ] **Step 1: Write the failing tests**

```python
async def test_new_india_roles_inserted_foreign_dropped(db_session): ...  # 2 India + 1 Skopje role -> inserted == 2
async def test_same_posting_from_live_search_is_one_row(db_session):
    # a row stored by persist_live_jobs for the same URL and source -> write_board_roles updates it
    assert summary.inserted == 0 and summary.updated == 1
async def test_reread_refreshes_last_seen_without_seen_cache(db_session): ...  # second write, last_seen_at advances
async def test_role_moved_to_remote_kept_not_closed(db_session): ...  # same URL, location "Remote (India)" -> not closed
async def test_complete_read_closes_absent_role(db_session): ...      # closed == 1, closed_at set
async def test_incomplete_read_closes_nothing(db_session): ...
async def test_unlinked_board_never_uses_name_matched_company(db_session):
    # a map company "Slice" exists; open-list board greenhouse:slice writes to the placeholder company
async def test_allow_new_false_only_updates(db_session): ...
async def test_at_most_200_roles_per_board(db_session): ...
```
- [ ] **Step 2: Run** `pytest tests/test_job_boards_write.py -v --no-cov` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(boards): write a board's India roles`

---

### Task 8: Retention ages board rows by last sighting [haiku]

**Files:**
- Modify: `services/job_retention.py` (rule 1 in `prune_stale_jobs`, ~line 218)
- Test: extend the existing retention tests (`grep -l prune_stale_jobs tests/`)

**Interfaces:**
- Consumes: `ATS` (Task 1).
- Produces: rule 1 deletes a raw row when `created_at` is past `RAW_RETENTION_DAYS`, except rows whose `source` starts with one of the nine `"{ats}:"` prefixes, which are aged by `coalesce(last_seen_at, created_at)`. The job-applications exemption is unchanged.

- [ ] **Step 1: Write the failing tests**: `test_board_row_seen_recently_is_kept` (created 60 days ago, `last_seen_at` 2 days ago, `source="lever:meesho"` → kept), `test_board_row_unseen_45_days_is_pruned`, `test_live_row_still_aged_by_created_at` (`source="linkedin"`, created 60 days ago, seen yesterday → pruned).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `fix(retention): keep board roles still on their board`

---

### Task 9: The roster store and the sweep [sonnet]

**Files:**
- Create: `services/job_boards/store.py`, `services/job_boards/sweep.py`
- Test: `tests/test_job_boards_sweep.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4-7; `SourceHealthService.record_attempt(source_name, *, job_count, latency_ms, status)` and `.is_available(source_name)`; `job_retention.database_size_bytes(session)`.
- Produces:
  - `store.py`: `async def upsert_board(session, ref: BoardRef, *, via: str, company_id: uuid.UUID | None = None, company_name: str | None = None) -> bool` (True when inserted; an existing row keeps its status, and only gains a `company_id` it lacked); `async def due_boards(session, limit: int, *, now: datetime) -> list[JobBoard]` (`status IN SWEPT_STATUSES AND (next_read_at IS NULL OR next_read_at <= now)`, order `next_read_at NULLS FIRST`); `async def apply_schedule(session, board: JobBoard, s: Schedule, read: BoardRead | None, india: int, *, now: datetime) -> None`; `async def top_live_boards(session, n: int = 25) -> list[BoardRef]` (active, `ats IN LIVE_SEARCH_ATS`, order `india_jobs DESC`).
  - `sweep.py`: constants `SWEEP_DEFAULT_LIMIT = 300`, `RUN_BUDGET_SECONDS = 480`, `MAX_NEW_PER_RUN = 1000`, `SIZE_STOP_BYTES = 350 * 1024 * 1024`, `MAX_BOARD_ROWS = 25_000`, `TOP_BOARDS_WHEN_FULL = 500`; `async def sweep(limit: int = SWEEP_DEFAULT_LIMIT, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> dict[str, int | str]`.
- Behaviour pinned by the spec: one queue per ATS, run concurrently, each sleeping `PACE_SECONDS[ats]` between requests; a queue whose breaker (`boards:<ats>`) is open is skipped; the first `rate_limited` stops that queue and records a failure; no board is started after 480 s; every DB write is its own short session opened after the request returns. `allow_new` is false when size ≥ `SIZE_STOP_BYTES`, when `MAX_NEW_PER_RUN` is reached, or when board rows ≥ `MAX_BOARD_ROWS` and the board is outside the top `TOP_BOARDS_WHEN_FULL` by `india_jobs`. Result keys: `read`, `active`, `inserted`, `updated`, `closed`, `rate_limited_ats` (comma list), `new_writes_paused` (`"size"`, `"cap"` or `""`).

- [ ] **Step 1: Write the failing tests** (fake readers via monkeypatched `READERS`, fake clock and sleep):
  `test_sweep_reads_due_boards_and_schedules_next`, `test_429_stops_only_that_ats` (Workable raises 429 on its first board: its other boards keep their `next_read_at`; Greenhouse boards are all read), `test_pace_respected` (recorded sleeps between two Workable reads ≥ 10), `test_no_board_started_after_budget`, `test_size_guard_pauses_new_rows` (monkeypatch `database_size_bytes` → 360 MB: `inserted == 0`, `updated > 0`, `new_writes_paused == "size"`), `test_open_breaker_skips_ats`, `test_gone_and_blocked_not_swept`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(boards): roster store and paced sweep`

---

### Task 10: Discovery from the open list and by name probe [sonnet]

**Files:**
- Create: `services/job_boards/discover.py`
- Modify: `config/settings.py` (add `job_boards_open_list_url: str = "https://raw.githubusercontent.com/storesignalhq/ats-job-boards/main/boards.csv"`)
- Test: `tests/test_job_boards_discover.py`, fixture `tests/fixtures/boards/open_list_sample.csv` (20 rows: our ATSes, plus `smartrecruiters` and an unknown ATS)

**Interfaces:**
- Consumes: Task 1 (`slug_candidates`, `names_match`, `PROBE_ATS`, `is_india_role`), Task 4 (`READERS`, `greenhouse.read_board_name`), Task 5 (`BoardRead.company_name` for Workable and Keka), Task 9 (`upsert_board`).
- Produces:
  - `async def discover_open_list(session, client, *, limit: int) -> dict[str, int]`: streams the CSV (reads only `ats`, `slug`, `company_name`), keeps rows whose `ats` is in `ATS`, upserts `candidate` with `via="open_list"`; returns `{"rows", "kept", "inserted"}`. Docstring credits the source and its CC BY 4.0 statement.
  - `async def probe_company(client, *, name: str, domain: str | None, sleep=asyncio.sleep) -> BoardRef | None`: tries `slug_candidates` × `PROBE_ATS`, paced; accepts a hit only with ≥1 India role and, where the ATS gives a name, `names_match`. At most 24 requests.
  - `async def discover_probe(session, client, *, limit: int) -> dict[str, int]`: listed tech companies (`is_tech IS TRUE`) with no `job_boards` row, skipping those with cache key `tr:boards:probed:<company_id>` (TTL 90 days); upserts hits with `via="name_probe"` and that `company_id`.

- [ ] **Step 1: Write the failing tests**: `test_open_list_keeps_supported_ats_only`, `test_open_list_reupsert_is_noop`, `test_probe_rejects_board_without_india_roles` (Greenhouse `slice` with only Skopje roles → `None`), `test_probe_rejects_name_mismatch`, `test_probe_accepts_groww`, `test_probe_skips_recently_probed_company`, `test_probe_request_budget` (≤ 24 requests seen).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(boards): discover boards from the open list and by name`

---

### Task 11: Discovery from YC and from careers pages [sonnet]

**Files:**
- Modify: `services/company_map/types.py` (`SiteFacts.boards: tuple[BoardRef, ...] = ()`), `services/company_map/website.py` (`extract_site_facts` sets `boards=tuple(boards_in_html(html))`; `_with_address_pages` merges the careers page's boards, deduplicated), `services/company_map/refresh.py` (`_step_sites`: upsert each `site.boards` with `via="careers_link"`, `company_id=company.id`; also `board_from_url(company.careers_url)` when set; result gains `"boards"`), `services/job_boards/discover.py`
- Test: extend `tests/test_company_map_website.py`; extend `tests/test_job_boards_discover.py`

**Interfaces:**
- Consumes: Tasks 1, 9, 10; `services.company_map.website.read_site(client, url) -> SiteFacts | None` (unchanged signature).
- Produces: `async def discover_yc(session, client, *, limit: int) -> dict[str, int]`: reads `https://yc-oss.github.io/api/companies/hiring.json`; keeps companies whose `all_locations` or `regions` mention India; per company, skips when `tr:boards:yc-site:<domain>` (30 days) is set; else `read_site(website)` and upserts its `boards` with `via="yc"`, `company_name=name`, no `company_id`; with none, runs `probe_company` unless `tr:boards:probed:<domain>` (90 days). Never creates a `companies` row. Returns `{"india", "read", "boards", "probed"}`.

- [ ] **Step 1: Write the failing tests**: `test_extract_site_facts_finds_cross_host_board` (homepage HTML with `https://jobs.lever.co/cred` → `facts.boards == (BoardRef("lever","cred"),)`), `test_careers_page_boards_merged`, `test_sites_step_upserts_boards_with_company_id`, `test_careers_url_that_is_a_board_is_used_unfetched`, `test_yc_keeps_india_only_and_creates_no_company`, `test_yc_skips_recently_read_site`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** the new tests and `pytest tests/test_company_map_website.py tests/test_company_map_new_sources_refresh.py -v --no-cov` — PASS.
- [ ] **Step 5: Commit** `feat(boards): discover boards from YC and careers pages`

---

### Task 12: Re-checking postings on the new ATSes [haiku]

**Files:**
- Modify: `ingestion/scrapers/ats_scraper.py` (`check_posting`), `services/liveness.py` (`reverify_batch` board cache)
- Test: extend `tests/test_liveness_service.py`

**Interfaces:**
- Consumes: `READERS` (Tasks 4-6), `parse_posting_url`/`posting_ref` (Task 3).
- Produces: `ATSScraper.check_posting(source, external_id, *, boards: dict[str, BoardRead | None] | None = None)` keeps today's per-posting checks for Greenhouse and Lever; for the other seven (and Ashby, as today) it reads the board once per batch through `READERS` and answers `OPEN`/`CLOSED` by membership of the posting URL. A read that is not `ok`, or not `complete`, answers `ERROR`. Personio's region comes from the `job_boards` row for that source (default `de`). The existing `ashby_boards` parameter is replaced by `boards`, and its one caller updated.

- [ ] **Step 1: Write the failing tests**: `test_check_posting_workable_open_by_membership`, `test_check_posting_incomplete_board_is_error`, `test_check_posting_rate_limited_is_error_not_closed`, `test_reverify_reads_each_board_once`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** `pytest tests/test_liveness_service.py tests/test_liveness_domain.py -v --no-cov` — PASS.
- [ ] **Step 5: Commit** `feat(liveness): re-check postings on every board ATS`

---

### Task 13: Steps, endpoint, workflow and visibility [haiku]

**Files:**
- Create: `services/job_boards/steps.py`, `.github/workflows/job-boards.yml`, `scripts/run_job_boards.py`
- Modify: `api/routers/ingest.py` (new endpoint; `/sources/health` gains `job_boards`), `DEPLOY.md` (data sources: credit the open list with a link and "CC BY 4.0 as stated by its publisher"), `Makefile` (`capture-board-fixtures` target running `scripts/capture_board_fixtures.py`)
- Test: `tests/test_job_boards_steps.py`

**Interfaces:**
- Consumes: Tasks 9-11 (`sweep`, `discover_open_list`, `discover_probe`, `discover_yc`), Task 2.
- Produces: `STEPS = ("open_list", "yc", "probe", "sweep", "prune")`; `MAX_LIMIT = {"open_list": 20000, "yc": 100, "probe": 60, "sweep": 600, "prune": 5000}`; `async def run_step(step: str, limit: int) -> dict[str, int | str]` (never raises; unknown step → `{"error": ...}`; `prune` deletes `gone` boards whose `last_read_at` is over 90 days old). Router: `JobBoardStep = Literal["open_list","yc","probe","sweep","prune"]`; `POST /api/v1/ingest/job-boards?step=...&limit=...` with `Depends(require_cron_or_admin)`, `limit` `ge=1, le=20000`. `/sources/health` adds `{"job_boards": {"by_status": {...}, "by_ats": {...}}}`.
- Workflow: copy `reverify.yml`'s gate, checkout and wake steps; crons `"17 */2 * * *"` → `sweep&limit=300`, `"41 3 * * *"` → `probe&limit=40`, `"5 4 * * 1"` → `yc&limit=60`, `"25 4 2 * *"` → `open_list&limit=20000`, plus `prune&limit=5000` after the daily probe; `workflow_dispatch` with a `step` input; a JSON `error` key becomes `::warning::`.

- [ ] **Step 1: Write the failing tests**: `test_run_step_unknown`, `test_run_step_caps_limit`, `test_run_step_never_raises` (a step that raises → `{"step": "sweep", "error": "RuntimeError"}`), `test_prune_removes_old_gone_boards_only`, `test_endpoint_requires_cron_token` (no token → 401/403).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS. Also `actionlint .github/workflows/job-boards.yml` if installed, else check the YAML parses with `python -c "import yaml,sys;yaml.safe_load(open(sys.argv[1]))" .github/workflows/job-boards.yml`.
- [ ] **Step 5: Commit** `feat(boards): scheduled job-board steps and workflow`

---

### Task 14: Live search reads the roster; docs [haiku]

**Files:**
- Modify: `ingestion/scrapers/ats_scraper.py` (`search_all_ats`; constants), `CLAUDE.md` (an "ATS job boards" paragraph under Architecture: the table, the steps, the nine ATSes, why Workday/SmartRecruiters are out, the live-search rule; migration list gains `026_job_boards`)
- Test: extend `tests/test_source_registry.py` or create `tests/test_ats_live_roster.py`

**Interfaces:**
- Consumes: `store.top_live_boards` (Task 9), `READERS` (Task 4).
- Produces: `search_all_ats` reads `top_live_boards(session, 25)`, cached under `tr:boards:live-top` for 3600 s; with no database or an empty result it uses the constants, with `postman` and `vercel` removed. Only `LIVE_SEARCH_ATS` boards are ever read here.

- [ ] **Step 1: Write the failing tests**: `test_live_reads_top_roster_boards`, `test_live_never_reads_slow_ats` (a top Lever board in the table is not requested), `test_live_falls_back_to_constants_without_db`, `test_constants_drop_dead_boards`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** them and `pytest tests/test_source_contracts.py -v --no-cov` — PASS.
- [ ] **Step 5: Full check**: `ruff check .`, `mypy .`, `pytest -m "not slow" --no-cov -n 4` — Expected: no new failures (Postgres-only tests may error locally without `talentradar_test`, as before).
- [ ] **Step 6: Commit** `feat(boards): live search reads the roster's busiest boards`
