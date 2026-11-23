# Job-board roster: find every employer's ATS board and poll it on a schedule

Date: 2026-11-23. Status: draft for approval. Reviewed by a builder check;
the calls made in that review are marked "call made".

## Why

The live ATS source (`ingestion/scrapers/ats_scraper.py`) polls 23 company slugs
hard-coded in the module, picked for "remote/India hiring" but mostly US
companies. Measured on 2026-11-23, those boards return 4,373 postings per search
and 220 (5%) of them are in India. Postman's board is gone (404), Vercel's is
empty, and 12 of the 23 have no India role at all. Meanwhile five Indian
startups found in a public list (Paytm, Apna, Lenskart, Tekion, Meesho) carry
509 India roles between them.

The board scrapers (LinkedIn guest, Indeed, Instahyre, foundit, Freshersworld,
Naukri) are the other half of sourcing. They get blocked, return thin rows, and
carry terms-of-service risk. An employer's own ATS API is public, keyless,
meant to be read, and is the highest-quality row we get (it is the only kind the
public surface will publish).

What the owner wants: jobs from startups, and a better way to find jobs than
the old scrapers. The way to do that is to know **which ATS board each employer
uses** and read those boards, for **any company with roles in India**, not only
the Bengaluru employers on the company map.

## Goals

1. A `job_boards` table: one row per (ATS, board id), whether or not the company
   is on the map.
2. Discovery steps that fill it from four places: the open list of ATS boards,
   YC's hiring list, ATS links on careers pages the company map already reads,
   and a guarded name probe.
3. A scheduled sweep that reads due boards politely and stores their **India**
   roles structurally (no LLM call), so search answers from the index.
4. The live `ats_platforms` source reads its boards from the table instead of
   the 23 constants.
5. Adapters for nine ATSes, each tested on a recorded fixture.

## Non-goals

- Workday (`*.myworkdayjobs.com`): answered 429 to the first request from a
  residential IP with both an identifying and a browser user agent. Not worked
  around.
- SmartRecruiters: `api.smartrecruiters.com/robots.txt` disallows every agent
  but LinkedInBot. Not read, though the API answers.
- Darwinbox, Zoho Recruit, Freshteam: no unauthenticated feed found.
- Teamtailor: boards sit on custom domains; later, if the careers-link step shows
  enough of them.
- schema.org `JobPosting` JSON-LD on careers pages, HN "Who is hiring", JobSpy,
  paid aggregators (TheirStack, Coresignal). Possible later work; not here.
- Changing or removing the board scrapers. They stay as they are.

## Anti-goals (a failure even if it runs)

- Any request to a path a host's robots.txt disallows for us, or a burst that
  gets the deployment's IP blocked (Workable is known to block for about a day).
- Attributing a board to the wrong company (measured: Greenhouse `slice` is a
  company in Skopje, not the Indian fintech).
- Storing roles that are not in India.
- Filling Neon's 0.5 GB: the sweep must stay inside a row and size budget.
- A search that waits on the sweep, or a sweep that holds a database transaction
  open across network calls.

## Constraints

- Free tiers only (Render 512 MB, Neon 0.5 GB, GitHub Actions as the scheduler,
  no worker). LLM calls are paid but none are needed here.
- India-only: a role is kept only when `domain.geo` resolves its location text to
  India (the same rule `is_publishable_role` uses; the `country` column is not
  trusted).
- Scheduled endpoints follow the existing pattern: `POST /api/v1/ingest/...`
  behind `require_cron_or_admin`, a `run_step` that never raises, a GitHub
  workflow that exits cleanly without `CRON_TOKEN`.
- Identifying user agent on every request:
  `TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)`.
- `posted_at` comes from the source or is absent (never `now()`).
- mypy strict; pure rules in `domain/`, I/O in `services/`, routers thin.

## Approaches considered

- **A. A table of boards, polled on a schedule (picked).** Discovery steps fill
  `job_boards`; a GitHub Actions sweep reads due boards with a per-ATS pace and
  stores India roles; the live ATS source reads the roster's busiest boards.
  Most to build (migration, steps, workflow), and the only option that scales to
  hundreds of boards without slowing searches or tripping rate limits.
- **B. Same table, read during each search.** No sweep, but a search has about 5
  seconds and cannot call hundreds of boards; every search would also hit
  Workable's limit. Rejected: does not fix the problem.
- **C. A checked-in list file**, built by a script and reviewed in a PR (like
  `data/company_sources/bengaluru_hiring_list.json`). Auditable, no migration
  for the list, but boards the company map discovers at run time could not be
  added, and the list goes stale unless someone reruns the script. Rejected.

The owner picked A on 2026-11-23, and decided the roster covers any company with
India roles.

## What was measured (2026-11-23)

Keyless endpoints, each read once with a real Indian employer:

| ATS | Endpoint | Example (jobs / India) | robots.txt |
|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{slug}/jobs` | Sigmoid 47/42, InMobi 75/42, Groww 8/8 | only `/embed/` disallowed |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{slug}` | Tekion 92/65, Sarvam 42/42 | none (401) |
| Lever | `GET api.lever.co/v0/postings/{slug}?mode=json` | Paytm 199/164, Meesho 47/46, CRED 11/10; 2-7 s | allowed, `Crawl-delay: 1` |
| Workable | `GET apply.workable.com/api/v1/widget/accounts/{slug}` | Apna 158/158 | allowed |
| Recruitee | `GET {slug}.recruitee.com/api/offers/` | Lenskart 88/76 | `/v/` disallowed |
| Keka | `GET {slug}.keka.com/careers/api/jobs/default/active` | Spyne 9/9, Pennant 5/5; has `countryCode`, `publishedOn`, salary, skills | `Disallow: /`, `Allow: /careers/` |
| Rippling | `GET ats.rippling.com/api/v2/board/{slug}/jobs?page=N` | Rippling 20/6 | `/internal/` disallowed |
| Personio | `GET {slug}.jobs.personio.de/xml?language=en` (XML; also `.com`) | no Indian example found | not checked |
| Breezy | `GET {slug}.breezy.hr/json` | no Indian example found | not checked |

An unknown slug is a 404 on Greenhouse, Ashby, Lever and Recruitee.

Discovery yield on a sample of 15 Indian startups:
- Careers-page links: 3 of the 9 pages robots.txt let us fetch (Razorpay →
  Greenhouse `razorpaysoftwareprivatelimited`, BrowserStack → Workday, Spyne →
  Keka). Atlan, Sarvam, Hasura, Postman and Zerodha disallow unknown agents.
- Name probe: 2 of 15 (Groww, CRED).
- Open list: found Groww, Atlan, Sarvam, Tekion, Paytm, Meesho, Apna, Lenskart,
  Freshworks, which the other two mostly missed.
- YC `companies/hiring.json`: 1,471 hiring companies, 55 with an India location,
  each with `website`, `all_locations` and `regions`.

No single method is enough; together they cover most of the sample.

## Design

### The table: `job_boards` (migration `026_job_boards`)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `ats` | text, not null | one of the nine in `domain.job_boards.ATS` |
| `board` | text, not null | the ATS's id for the board: a slug, or a subdomain for Recruitee, Keka, Breezy, Personio |
| `region` | text, null | only for Personio (`de` or `com` host). Lever's EU host is out of the first cut (call made in review: no Indian board seen there) |
| `company_id` | uuid fk `companies.id`, null, `ON DELETE SET NULL` | set when the board belongs to a company we hold |
| `company_name` | text, null | as the source names it; what `posting_company` resolves at write time |
| `discovered_via` | text, not null | `open_list`, `yc`, `careers_link`, `name_probe`, `manual` |
| `status` | text, not null | `candidate`, `active`, `no_india`, `gone`, `blocked` |
| `india_jobs` / `total_jobs` | int, null | from the last full read |
| `first_seen_at`, `last_read_at`, `last_india_at`, `next_read_at` | timestamptz | |
| `failures` | int, default 0 | consecutive failed reads |
| `zero_india_reads` | int, default 0 | consecutive reads with no India role |

Unique `(ats, board, coalesce(region, ''))`. Index on `(status, next_read_at)`.
No existing table changes. Retention (`services/job_retention.py`) deletes `gone`
rows 90 days after `last_read_at`; `job_boards` is small (about 16k rows at
most) and needs no size pruning.

### Pure rules (`domain/job_boards.py`, no I/O)

- `ATS`: the nine names, and each one's pace (minimum seconds between requests
  to that host): Greenhouse 0.5, Ashby 0.5, Lever 1 (its `Crawl-delay`), Rippling
  1, Recruitee 1, Personio 1, Breezy 1, Keka 2, Workable 10.
- `boards_in_html(html) -> list[BoardRef]`: ATS boards linked from a page.
  Exact host patterns only, like `domain/posting_urls.py`:
  `boards.greenhouse.io/{slug}`, `job-boards.greenhouse.io/{slug}`,
  `boards.greenhouse.io/embed/job_board?for={slug}`, `jobs.lever.co/{slug}`,
  `jobs.ashbyhq.com/{slug}`,
  `apply.workable.com/{slug}`, `{slug}.recruitee.com`,
  `{slug}.keka.com/careers`, `ats.rippling.com/{slug}`, `{slug}.breezy.hr`,
  `{slug}.jobs.personio.(de|com)`. A host that merely contains one of these is
  not matched. Ignores `{www,app,api,careers,jobs}` as a subdomain slug.
- `slug_candidates(name, domain) -> list[str]`: at most four guesses for the name
  probe: the registrable domain's label (`groww.in` → `groww`), the name
  lowercased with spaces and punctuation removed, and the same with `-` between
  words; deduplicated.
- `next_read(board, outcome, now)`: when to read again.
  - India roles found: `active`, read again in 24 h.
  - No India role: `zero_india_reads += 1`; read again in 7, 14, then 30 days.
    The third zero makes it `no_india` (monthly).
  - 404/410: `gone` after two in a row, a day apart.
  - 429, 5xx, timeout: `failures += 1`, back off 2^failures hours (max 7 days);
    `blocked` after 6.
  - Status moves are deterministic, and nothing here is a score.
- `is_india_role(location_text)`: delegates to `domain.geo`.

### Adapters (`ingestion/boards/`, one module per ATS)

Each exposes `async def read_board(client, ref) -> BoardRead`, where `BoardRead`
is `(outcome, roles: list[Job], total: int, complete: bool)`. It makes the
request(s) for one board, parses off the event loop
(`ingestion/scrapers/_parsing.py`), and returns `domain.entities.Job` with
`source=f"{ats}:{board}"`, the posting's own URL, location text, the
description when the list response carries one, and `posted_at` only when the
ATS states it. `complete` is true only when the whole board was read (no page
cap hit, no error). Rules every adapter follows:

- No redirects followed; a 3xx is an `error` outcome.
- Bodies read with a byte cap (2 MB; Lever and Greenhouse boards with hundreds of
  roles fit) that fails rather than truncates.
- Paginated ATSes (Rippling) stop at 10 pages.
- Descriptions come only from the list response, never from a request per
  posting: Greenhouse with `content=true` (double-encoded HTML, unescaped then
  cleaned), Ashby's and Lever's plain-text fields, Keka's `description`. Where
  the list carries none (Workable v1, Recruitee if absent), the row has none.
  `services/job_enrichment.py` never fetches one, so this is the only chance
  (review finding; call made: list-only keeps one request per board).
  Truncated to `MAX_DESCRIPTION_CHARS`.
- Keka reads only `/careers/api/...`, the path its robots.txt allows.

`ats_scraper.py`'s three `fetch_*_company` methods become thin calls to these
adapters, so live search and the sweep parse identically.

Every adapter has a recorded fixture and a contract test, like
`tests/test_source_contracts.py`. `make capture-fixtures` records them.

### Discovery steps (`services/job_boards/discover.py`)

All are steps of `run_step` (below) and write through one function,
`store.upsert_board(ref, company_id, company_name, via)`, which inserts a
`candidate` or leaves an existing row's status alone.

1. **`open_list`**: read the open board list (a CSV of `ats,slug,company_name,
   open_jobs,...`) from a URL in settings, keep rows whose ATS we support, and
   insert them as `candidate`. Monthly. See "The open list" below.
2. **`yc`**: read `yc-oss.github.io/api/companies/hiring.json`; keep companies
   whose `all_locations` or `regions` name India (55 today). These do not get
   `companies` rows: the map stays Bengaluru. Their state lives in the cache,
   keyed by registrable domain (call made in review, no migration for it):
   `tr:boards:yc-site:<domain>` (30 days) and `tr:boards:probed:<domain>`
   (90 days). The weekly YC list is itself the queue: each run takes up to
   `limit` companies whose keys have expired. For each, fetch its careers page
   with the same rules as the company map's site reader (robots.txt, identifying
   UA, 5 s, 300 KB) and run `boards_in_html`; if nothing is found, run the probe
   (step 4) for it in the same run, with the YC name as the name to match.
   Without a cache (in-memory fallback) a run may repeat work; that costs
   requests, not correctness.
3. **`careers_link`**: the company map's site reader keeps only `SiteFacts` and
   drops the HTML, and keeps only same-host links. So `extract_site_facts`
   gains a `boards` field: `boards_in_html` run over the homepage (catching a
   cross-host "Careers" link to `jobs.lever.co/...`) and the one careers or jobs
   page it already fetches. The `sites` step upserts each board with that
   `company_id`. Also read `companies.careers_url` as a string: when it is
   itself a board URL, that is a board (never fetched). No new request.
4. **`probe`**: for listed tech companies (`is_tech IS TRUE`) with no board yet,
   and the YC India companies above, try `slug_candidates` on Greenhouse, Ashby,
   Lever, Workable, Recruitee and Keka. A hit is kept **only if** the board has at
   least one India role, **and**, where the ATS states a company name
   (Greenhouse's `/v1/boards/{slug}`, Workable's account name, Keka's
   `careerportalinfo`), that name matches the company's under
   `domain.company_identity` normalisation. A probe is at most 24 requests per
   company, paced like the sweep, and a company is probed at most once per 90
   days (`tr:boards:probed:<company_id or domain>`).

**Which company a board's jobs are filed under** (review finding; call made).
`persist_live_jobs` picks a company by name match (`posting_company`), which is
how a board named "Slice" would land on the wrong company. Board writes do not
use that. A board with `company_id` writes its jobs to that company. A board
without one (open list, YC) writes to a placeholder company keyed by the board
itself (`{ats}-{board}.board.talentradar.internal`, named `company_name`), and
is never name-matched to a map company. Only `careers_link` and a name-checked
`probe` set `company_id`.

### The sweep (`services/job_boards/sweep.py`)

`sweep(limit)` takes boards with `status in (candidate, active, no_india)` and
`next_read_at <= now`, oldest first, at most `limit` (default 300). It groups
them by ATS and runs one queue per ATS concurrently, each queue respecting that
ATS's pace. A queue stops for the rest of the run on its first 429 and reports a
failure to `SourceHealthService` under `boards:<ats>`; an open breaker skips that
ATS. The run starts no new board after 480 s (the Events rule).

For each board read:
1. No database session is open during the request.
2. Keep India roles only, at most 200 per board.
3. Write them with a new `services/job_boards/write.py::write_board_roles`, not
   `persist_live_jobs` (review finding). `persist_live_jobs` skips any URL it
   saw in the last 7 days (the `tr:seen:` cache), so a daily read would never
   refresh a role it already holds, and it name-matches the company. The board
   writer:
   - computes `external_id` exactly as the live path does (MD5 of the posting
     URL, `job_persistence._stable_external_id`), so a role found by live search
     and by the sweep is one row under `uq_jobs_external_id_source`;
   - upserts on `(external_id, source)`: an existing row gets `last_seen_at` and
     a sighting (`liveness.record_sighting`); a new row is written structurally
     (`build_job_kwargs`, `enrichment_status='raw'`) under the board's company
     (see "Which company");
   - validates URLs with `validate_job_url`, whose allowlist gains
     `apply.workable.com`, `recruitee.com`, `breezy.hr`, `keka.com`,
     `ats.rippling.com` and `jobs.personio.de`/`.com` (review finding: Workable,
     Recruitee and Breezy posting URLs fail the current fallback rule, so Apna's
     and Lenskart's roles would be dropped);
   - embeds new rows only, awaited inside the run's 480 s budget (it is not in
     the background on this path).
4. Update the board row (counts, status, `next_read_at`) in its own short
   transaction.

Size and retention (review finding; calls made):
- Today `prune_stale_jobs` deletes a raw row 45 days after `created_at`, open or
  not, so a role still on its board would be deleted and re-inserted under a new
  id every 45 days, breaking its public `/r/` URL. Change rule 1 for rows whose
  `source` is one of the nine ATS prefixes: age them by `last_seen_at`
  (`coalesce(last_seen_at, created_at)`), so a role the sweep still sees is kept
  and one gone from its board for 45 days goes. Other sources keep the current
  rule.
- The prune's pressure line is `STORAGE_HIGH_WATER_BYTES` (400 MB). The sweep
  writes no new jobs above 350 MB (its own threshold, below the prune's, so the
  two do not fight); it still refreshes existing rows and board rows, and says
  so in its result.
- Budget: at most 1,000 new rows a run and 25,000 board-sourced rows in all
  (about 100-150 MB with descriptions capped at 4 KB and 384-float embeddings);
  over that, new rows are written only for boards whose `india_jobs` rank in the
  top 500.

Closing roles. This is new behaviour: today nothing closes a role during a board
read; absence is checked only by `reverify_batch`, one stored row at a time.
When a board read is `complete`, a stored row of that board not in the read is
marked `PostingCheck.CLOSED` through the same liveness path the reverify uses.
An incomplete or failed read closes nothing. The places that name the three
ATSes today all learn the new ones: `domain/posting_urls.py` (`_PATTERNS`,
`_KNOWN_ATS`, `posting_ref`), `ATSScraper.check_posting`,
`services/liveness._load_candidates` (its `LIKE` clauses), `domain/platforms.py`,
`agents/merge_rank.SOURCE_TRUST` (ATS trust for all nine) and
`services/public_roles._UNSUPPORTED_MESSAGE`. For an ATS with no per-posting
endpoint, `check_posting` reads the board and checks membership, as it does for
Ashby. Personio's host (`de` or `com`) is read from the `job_boards` row.

`is_verifiable_source` accepts the nine ATS prefixes, so these rows can reach the
public surface; `is_publishable_role`'s India rule still applies.

### Live search

`ATSScraper.search_all_ats` reads the 25 `active` **Greenhouse and Ashby**
boards with the most `india_jobs` (cached for 1 h) instead of the three
constant lists, then fans out as today (review finding; call made). Only those
two ATSes are read at search time: they answer in under half a second and
tolerate load. Lever (2-7 s, past the 2 s per-board timeout), Workable (blocks
bursts), Keka and the rest are read only by the paced sweep, and their roles
reach search through the index. With no database, or an empty table, it falls back to the current
constants (with Postman and Vercel removed). The constants are deleted once the
roster has been populated in production.

### Scheduling

`POST /api/v1/ingest/job-boards?step=open_list|yc|probe|sweep|prune&limit=N`
(`require_cron_or_admin`), handled by `services/job_boards/steps.py::run_step`,
which never raises and returns `{"step": ..., counts...}` or `{"error": ...}`.

`.github/workflows/job-boards.yml`, gated on `CRON_TOKEN` like `reverify.yml`:
- every 2 hours: wake the API, then `sweep&limit=300`;
- daily: `probe&limit=40`;
- weekly (Monday): `yc`;
- monthly (the 2nd): `open_list`.

At 300 boards every 2 hours (3,600 a day), the initial 15k candidates are
screened in about five days; after that most reads are `active` boards (once a
day) and `no_india` ones (monthly). Workable's 3,177 candidates at one request
per 10 s are the slowest part (about nine hours of queue time spread over runs).

`scripts/run_job_boards.py --step ...` runs the same steps locally.

### Visibility

`GET /api/v1/ingest/sources/health` (admin) gains a `job_boards` block: counts by
ATS and status, and the last sweep's result. No user-facing UI change.

## The open list

`storesignalhq/ats-job-boards` (`boards.csv`, 15,647 boards on 2026-11-16, built
from the Common Crawl URL index). Its announcement states CC BY 4.0; the
repository carries no licence file. The owner decided on 2026-11-23 to use it
as published without asking first. Since CC BY asks for credit, the source is
named, with a link, in `DEPLOY.md`'s data-sources notes and in the step's
docstring. Only `ats`, `slug` and `company_name` are read; every board is
re-checked against its own ATS before it is trusted, so a stale or wrong row
costs one request.

## Left to the builder

- Exact parsing of each ATS's location fields into one location string.
- Whether Rippling, Personio and Breezy are worth shipping in the first cut if no
  Indian board turns up during screening (they can follow).
- Test layout, beyond: pure tests for `boards_in_html`, `slug_candidates` and
  `next_read`; a contract test per adapter; a sweep test with a fake clock and a
  fake 429; and the migration drift test.
