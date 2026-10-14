# Company Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Company Intel into a map of every sourceable Bengaluru employer. Each company carries a one-line summary, honest location precision, sourced founders and people, funding news and open roles.

**Architecture:**
- Pure domain modules (`domain/areas.py`, `company_taxonomy.py`, `company_identity.py`, `news.py`, `linkedin_links.py`) hold every rule.
- `services/company_map/` holds thin I/O importers:
  - Wikidata, OSM, company websites, Nominatim, RSS, GitHub and Tavily.
  - Each has a pure parser tested on recorded fixtures.
  - A `store` layer that merges by source precedence.
  - A `refresh` orchestrator, driven by cron endpoints.
- The read side adds `/map`, `/news` and richer drawer data.
- PR 2 renders it with MapLibre + OpenFreeMap.

**Tech Stack:**
- Backend: FastAPI, async SQLAlchemy, Alembic, httpx, BeautifulSoup/lxml, stdlib `xml.etree`, Groq via `services.llm._chat`.
- Frontend: Next.js 14 and `maplibre-gl`.

**Spec:** `docs/superpowers/specs/2026-10-14-company-map-design.md` (with `2026-10-14-outreach-suite-roadmap.md` for evidence)

## Global Constraints

- **Zero cost.** Free tiers only. No new paid service.
- **No LLM call on a read path** (map, list, drawer). LLM use is budgeted:
  - `DAILY_COMPANY_SUMMARY_BUDGET = 60`
  - `DAILY_NEWS_LLM_BUDGET = 20`
- **LinkedIn is never requested by the server.** Deep links only.
- **Every stored person** (`company_contacts` with `user_id IS NULL`) has `source_url` and `verified=False`. No guessed emails. The catalogue JSON never holds a person's name.
- **Nominatim:** 1 request/second, an identifying `User-Agent` (`TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)`), ≤ 40 lookups per run, results stored on the row.
- **Website fetches:**
  - robots.txt respected
  - 5 s timeout, 300 KB body cap, concurrency 4
  - each site at most once per 30 days
- **Text reaching a prompt** is flattened, bounded and fenced (`agents.interview.role_context._flat`, `<<<SITE>>>`).
- **mypy strict;** ruff isort first-party list. New code is fully annotated.
- **Never raise from a scheduled step.** Return `{"error": "..."}` and leave the last good data.
- **Map payload** ≤ 100 KB gzipped. Cache it in `CacheBackend` for 3600 s per city.
- **Source precedence**, highest first: `curated > site > wikidata > osm > news > llm`. Exception: `stage` and `last_funding_at` follow the newest dated evidence.

## Review Focus

1. **A company whose site robots.txt disallows us, or times out, or returns non-HTML.** It must end with no summary and stay unlisted, never get an LLM summary of a bare name. Test: `test_summary_step_skips_company_with_no_source_text` in Task 8.
2. **The same company arriving from two sources:**
   - Wikidata `www.razorpay.com`, catalogue `razorpay.com` → one row.
   - Two different domains with the same name → two rows.

   Tests: `test_upsert_matches_by_normalised_domain` and `test_same_name_different_domain_never_merges` in Task 11.
3. **A news title that only looks like funding** ("Razorpay CEO on raising the bar", "Layoffs at X as funding dries up"). It must not create a company or a funding row. Test: `test_non_funding_titles_are_not_funding` in Task 9.
4. **A coordinate outside Bengaluru** (a Wikidata HQ in Mumbai, a Nominatim hit for a same-named place elsewhere). It must not pin the company. Test: `test_point_outside_bbox_is_rejected` in Task 7.
5. **Re-running a step.** It must be idempotent: no duplicate people, news or companies, and slugs are stable. Test: `test_people_upsert_is_idempotent` in Task 10 and `test_refresh_step_twice_changes_nothing` in Task 11.

---

## File Structure

```
domain/areas.py               canonical Bengaluru areas, bbox, text/point lookups, jitter
domain/company_taxonomy.py    stage/sector/size vocabularies + mappers, import tier, "bigger company"
domain/company_identity.py    normalize_domain/normalize_name/slugify, merge_fields precedence
domain/news.py                classify title, extract funding facts, founders from text
domain/linkedin_links.py      deep-link builders (never fetched)
services/company_map/__init__.py
services/company_map/types.py      ImportedCompany, PersonCandidate, SiteFacts, GeoHit, FeedItem
services/company_map/wikidata.py   SPARQL + parse
services/company_map/osm.py        Overpass + parse
services/company_map/website.py    robots + fetch + extract_site_facts
services/company_map/geocode.py    Nominatim calls + parse + resolve_location
services/company_map/summary.py    clean/choose summary + budgeted LLM summary
services/company_map/news_feeds.py FEEDS, parse_feed, ingest_news
services/company_map/people.py     GitHub members, Tavily founders, upsert_people
services/company_map/store.py      find/upsert company, apply location, due-row queries
services/company_map/refresh.py    run_step(step, limit)
services/company_map/read.py       map_rows, hiring_for_you, people/news for drawer, latest_news
storage/migrations/versions/015_company_map.py
storage/models.py                  Company/CompanyContact columns, CompanyNews model
services/company_contacts.py       new kinds + serialised fields
services/companies.py              list/facets filters, drawer fields
services/job_retention.py          prune company_news
api/routers/company_intel.py       /map, /map/for-me, /news, /{id}/people/discover, filters
api/routers/ingest.py              /news, /company-map/refresh
scripts/refresh_company_map.py     local backfill
scripts/seed_companies.py          new catalogue fields
scripts/capture_fixtures.py        record company-map fixtures
.github/workflows/news.yml, company-map.yml
tests/fixtures/company_map/*       recorded payloads
tests/test_company_map_*.py
frontend/... (PR 2)
```

---

## PR 1 — Foundation

### Task 1: Areas and taxonomy (pure)

**Files:**
- Create: `domain/areas.py`, `domain/company_taxonomy.py`
- Test: `tests/test_company_map_domain.py`

**Interfaces:**
- Produces:
  - `domain.areas`:
    - `Area(name: str, lat: float, lng: float, aliases: tuple[str, ...])` (frozen dataclass)
    - `BENGALURU_AREAS: tuple[Area, ...]`
    - `BENGALURU_BBOX = (12.80, 77.40, 13.15, 77.80)` (south, west, north, east)
    - `in_bengaluru(lat: float, lng: float) -> bool`
    - `area_for_text(text: str | None) -> str | None`
    - `area_for_point(lat: float, lng: float, max_km: float = 2.5) -> str | None`
    - `jitter(key: str, lat: float, lng: float) -> tuple[float, float]`
  - `domain.company_taxonomy`:
    - `STAGES: dict[str, str]`, `SECTORS: dict[str, str]`, `SIZES: dict[str, str]`
    - `stage_from_round(text: str | None) -> str | None`
    - `sector_from_text(text: str | None) -> str | None`
    - `sector_from_wikidata(qids: list[str]) -> str | None`
    - `size_bucket(employee_count: int | None, employee_count_range: str | None) -> str | None`
    - `tier_for_import(*, is_vc: bool, founded_year: int | None, is_public: bool) -> str | None`
    - `is_bigger_company(tier: str | None, employee_count: int | None, employee_count_range: str | None) -> bool`

- [ ] **Step 1: Compile area centroids.**
  - One Overpass query: `node["place"~"suburb|neighbourhood|quarter"]["name"](12.80,77.40,13.15,77.80);out;`.
  - Hand-pick these canonical areas: HSR Layout, Koramangala, Indiranagar, Bellandur, MG Road, Whitefield, Sarjapur Road, Electronic City, Bannerghatta Road, Marathahalli, JP Nagar, Jayanagar, Domlur, Mahadevapura, Manyata Tech Park, Hebbal, Yeshwanthpur, BTM Layout, Ulsoor, Malleshwaram, Yelahanka, Banashankari, Basavanagudi, Rajajinagar, Vijayanagar, Peenya, Frazer Town, Kalyan Nagar, KR Puram, Bommanahalli, Old Airport Road, Richmond Town.
  - Add aliases such as `"hsr sector"`, `"koramangala 6th block"`, `"kadubeesanahalli"`, `"outer ring road"` → Bellandur, `"orr"`, `"cbd"` → MG Road.
  - Record each OSM node id in a comment.
- [ ] **Step 2: Write the failing tests.**
  - `area_for_text("HSR Sector 3, Bengaluru") == "HSR Layout"`
  - `area_for_text("Koramangala 6th Block") == "Koramangala"`
  - `area_for_text("Mumbai") is None`
  - `area_for_point(*<HSR centroid>) == "HSR Layout"`
  - `area_for_point(19.07, 72.88) is None`
  - `in_bengaluru(12.97, 77.59)` and `not in_bengaluru(19.07, 72.88)`
  - `jitter("abc", 12.9, 77.6) == jitter("abc", 12.9, 77.6)`, and the distance is 150–450 m
  - `stage_from_round("Series B") == "series_b"`; `"Series D" → "series_c_plus"`; `"pre-seed" → "pre_seed"`; `"IPO" → "public"`
  - `sector_from_text("Fintech & Payments") == "fintech"`; `"Developer tools" → "devtools"`; `"Edtech" → "edtech"`; `"IT Services & Consulting" → "it_services"`; unknown text → `None`
  - `size_bucket(None, "201-500") == "201-1000"`; `size_bucket(40, None) == "1-50"`; `size_bucket(None, "10000+") == "1000+"`
  - `tier_for_import(is_vc=True, founded_year=1990, is_public=False) == "vc"`; `(False, 2015, False) == "startup"`; `(False, 2015, True) is None`; `(False, None, False) is None`
  - `is_bigger_company("gcc", None, None)`; `is_bigger_company(None, 250, None)`; `not is_bigger_company("startup", None, "11-50")`
  - Every curated `industry` string in `data/companies/bengaluru.json` maps to some sector or `other` without raising
- [ ] **Step 3: Run.** `pytest tests/test_company_map_domain.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement both modules.**
  - Sector keywords are ordered, first match wins: ai, fintech, edtech, healthtech, logistics, gaming, d2c, consumer, devtools, deeptech, saas, it_services.
  - `area_for_text` matches the longest alias first.
  - `jitter` derives bearing and distance from `hashlib.sha256(key)`.
- [ ] **Step 5: Run.** The same command must PASS. Then `ruff check domain tests/test_company_map_domain.py`.
- [ ] **Step 6: Commit.** `feat(company-map): areas and company taxonomy`

### Task 2: Identity and precedence merge (pure)

**Files:**
- Create: `domain/company_identity.py`
- Test: `tests/test_company_map_identity.py`

**Interfaces:**
- Produces:
  - `normalize_domain(value: str | None) -> str | None`: strips scheme, `www.`, path and port; handles eTLD+1 with `co.in, org.in, net.in, co.uk, com.au`.
  - `normalize_name(name: str) -> str`
  - `slugify(name: str) -> str` (ASCII, max 96)
  - `SOURCE_PRECEDENCE: tuple[str, ...] = ("curated", "site", "wikidata", "osm", "news", "llm")`
  - `merge_fields(current: dict[str, Any], provenance: dict[str, str], incoming: dict[str, Any], source: str) -> tuple[dict[str, Any], dict[str, str]]`, returning `(updates, new_provenance)`.

- [ ] **Step 1: Write the failing tests.**
  - `normalize_domain("https://www.Razorpay.com/careers") == "razorpay.com"`
  - `normalize_domain("blog.zerodha.co.in") == "zerodha.co.in"`
  - `normalize_domain("") is None`
  - `normalize_name("Acme Technologies Pvt. Ltd.") == "acme technologies"`
  - `normalize_name("ACME Private Limited") == "acme"`
  - `slugify("Zeta (Directi)") == "zeta-directi"`
  - Merge rules:
    - a lower source does not overwrite a curated value
    - the same source refreshes its own value
    - a `None` incoming value never clears a field
    - news `stage="series_b"` with `last_funding_at` newer than the current one overwrites curated `stage="series_a"`
    - older funding news does not overwrite
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): identity and source precedence`

### Task 3: Schema — migration 015 and models

**Files:**
- Create: `storage/migrations/versions/015_company_map.py` (revision `015_company_map`, down `014_interview_role`)
- Modify:
  - `storage/models.py`: `Company` and `CompanyContact` get the new columns; add `CompanyNews`.
  - `services/company_contacts.py`: `VALID_KINDS` adds `founder`, `leader`, `engineer`; `_serialise` emits `x_url`, `github_url`, `profile_url`, `source_kind`.
  - `api/schemas/company_schemas.py`: `ContactSchema` gets the new optional fields.
- Test: `tests/test_company_map_schema.py`

**Interfaces:**
- Produces:
  - Company columns: `slug, summary, summary_source, stage, sector, area, address, pincode, lat, lng, geo_precision, geo_source, wikidata_id, employee_count, x_url, last_funding_at, enriched_at`. Types are as in spec §3.1, and `slug` and `wikidata_id` are unique.
  - CompanyContact columns: `x_url, github_url, profile_url, source_kind`.
  - `CompanyNews` (`__tablename__ = "company_news"`) with the spec §3.3 columns, `investors: StringArray`, and `company` relationship, nullable.

- [ ] **Step 1: Write the failing tests.**
  - The columns exist on `Company.__table__.c`, `CompanyContact.__table__.c` and `CompanyNews.__table__.c`.
  - `"founder" in VALID_KINDS`.
  - `_serialise` of a contact with `x_url` includes it.
  - The migration module's `revision`/`down_revision` chain to `014_interview_role`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement the models and the migration.**
  - Indexes: `ix_companies_area/stage/sector/geo_precision/last_funding_at`, `ix_company_news_company_id`, `ix_company_news_published_at`.
  - Partial unique index `uq_company_contacts_curated_person` on `(company_id, kind, lower(name)) WHERE user_id IS NULL AND name IS NOT NULL`.
  - `downgrade()` drops everything in reverse.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Run the migration against a local Postgres.**
  - Start it: `docker run -d --name tr-map-pg -e POSTGRES_PASSWORD=testpassword -e POSTGRES_DB=talentradar_test -p 5433:5432 pgvector/pgvector:pg15`
  - Run `alembic upgrade head`, `alembic downgrade -1`, then `alembic upgrade head`, all with `POSTGRES_PORT=5433`.
  - Expected: no errors.
- [ ] **Step 6: Commit.** `feat(company-map): migration 015 — company map columns, people kinds, company_news`

### Task 4: Wikidata importer

**Files:**
- Create: `services/company_map/__init__.py`, `services/company_map/types.py`, `services/company_map/wikidata.py`
- Create fixture: `tests/fixtures/company_map/wikidata.json` (recorded, trimmed to ~40 rows)
- Test: `tests/test_company_map_importers.py`

**Interfaces:**
- Produces, in `types.py` (frozen dataclasses):
  - `PersonCandidate(name: str, kind: str, title: str | None, linkedin_url: str | None, x_url: str | None, github_url: str | None, profile_url: str | None, source_url: str, source_kind: str)`
  - `ImportedCompany(name: str, domain: str | None, website_url: str | None, wikidata_id: str | None = None, lat: float | None = None, lng: float | None = None, address: str | None = None, founded_year: int | None = None, employee_count: int | None = None, x_url: str | None = None, linkedin_url: str | None = None, github_org: str | None = None, logo_url: str | None = None, description: str | None = None, sector: str | None = None, tier: str | None = None, stage: str | None = None, people: tuple[PersonCandidate, ...] = ())`
- Produces, in `wikidata.py`:
  - `SPARQL: str`. Selects `P159 wd:Q1355`, `P31/P279* wd:Q4830453`, and `P856` (required), plus optional fields.
  - `async fetch(client: httpx.AsyncClient) -> dict[str, Any]`
  - `parse(payload: dict[str, Any]) -> list[ImportedCompany]`. Groups rows by item. People: founders `P112` → `kind="founder"`, CEO `P169` → `kind="leader", title="CEO"`, with `source_url="https://www.wikidata.org/wiki/<QID>"` and X/LinkedIn/GitHub links from `P2002/P6634/P2037`. VC → `tier="vc"`. `P625` is parsed from `Point(lng lat)`; a point outside the bbox is dropped.

- [ ] **Step 1: Record the fixture.** Run the real query once (see `scripts/capture_fixtures.py` changes in Task 13) and trim it.
- [ ] **Step 2: Write the failing tests.**
  - It parses ≥ 20 companies.
  - Every company has a normalised domain.
  - No two have the same `wikidata_id`.
  - Every person has a `source_url` that starts with `https://www.wikidata.org/wiki/`.
  - A known row from the fixture parses to its exact expected fields.
  - X links have the form `https://x.com/<handle>`.
- [ ] **Step 3: Run, expect FAIL. Step 4: Implement. Step 5: Run, expect PASS.**
- [ ] **Step 6: Commit.** `feat(company-map): Wikidata importer`

### Task 5: OSM importer

**Files:**
- Create: `services/company_map/osm.py`, fixture `tests/fixtures/company_map/overpass.json`
- Test: `tests/test_company_map_importers.py`

**Interfaces:**
- Produces:
  - `QUERY: str`: `nwr["office"~"^(company|it)$"]["name"]["website"]` in the bbox, `out center tags;`
  - `async fetch(client) -> dict[str, Any]`
  - `parse(payload) -> list[ImportedCompany]`. Uses `center` for ways. Address comes from `addr:housenumber, addr:street, addr:suburb, addr:postcode`. Drops points outside the bbox.

- [ ] **Steps 1–5 follow Task 4.** Tests:
  - It parses ≥ 50.
  - Every result has lat/lng inside the bbox and a domain.
  - A way element uses its `center`.
  - An element with no website is skipped.
- [ ] **Step 6: Commit.** `feat(company-map): OSM office importer`

### Task 6: Website reader

**Files:**
- Create: `services/company_map/website.py`
- Create fixtures: `tests/fixtures/company_map/site_*.html` (three real homepages with `<script>` stripped)
- Test: `tests/test_company_map_website.py`

**Interfaces:**
- Produces:
  - `SiteFacts(title: str | None, description: str | None, text: str, address: str | None, pincode: str | None, people: tuple[PersonCandidate, ...], links: tuple[str, ...])` in `types.py`
  - `extract_site_facts(html: str, base_url: str) -> SiteFacts`. This is pure:
    - meta `description` or `og:description`
    - visible text, max 4,000 chars
    - an address: the first text block containing `Bengaluru|Bangalore` and `\b560\d{3}\b`, max 300 chars
    - people: an anchor to `linkedin.com/in/` or `x.com/`/`twitter.com/` whose surrounding block contains `founder|co-founder|ceo|cto|chief`
      - name from the anchor text or the nearest heading
      - kind is founder or leader
      - `source_url=base_url`, `source_kind="site"`
    - `links`: same-site `/about*` and `/contact*` hrefs
  - `async read_site(client: httpx.AsyncClient, url: str) -> SiteFacts | None`. It checks robots.txt via `urllib.robotparser` (fetched with the same client), fetches the homepage (5 s, 300 KB cap, `text/html` only), then at most one `/contact*` page for the address. It returns `None` when disallowed, on error, or for non-HTML.

- [ ] **Step 1: Write the failing tests.**
  - Fixture descriptions are extracted.
  - The address from a footer containing "560034" is found.
  - A page with no address gives `address is None`.
  - A "Co-founder & CEO" card with a LinkedIn link yields a founder with that `linkedin_url`.
  - A plain team-member LinkedIn link with no role word yields no person.
  - `read_site` returns `None` when `robots.txt` disallows `/`. Use an `httpx.MockTransport` client.
  - A non-HTML content-type returns `None`.
- [ ] **Steps 2–4: Run, expect FAIL; implement; run, expect PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): company website reader`

### Task 7: Geocoding and location resolution

**Files:**
- Create: `services/company_map/geocode.py`
- Test: `tests/test_company_map_geocode.py`

**Interfaces:**
- Produces:
  - `GeoHit(lat: float, lng: float, precision: str, source: str, area: str | None)` in `types.py`
  - `parse_nominatim(payload: list[dict[str, Any]], *, mode: str) -> GeoHit | None`
    - `mode="address"` → `street`
    - `mode="postcode"` → `area`, at the nearest area centroid with jitter applied later
    - `mode="name"` → `exact`, only for class `office|building|amenity` and name similarity ≥ 0.9 (`difflib`)
    - A hit outside the bbox → `None`.
  - `async geocode(client, *, address: str | None = None, postcode: str | None = None, name: str | None = None) -> GeoHit | None`. Uses `viewbox` = bbox, `bounded=1`, `countrycodes=in`, the identifying UA, and `await asyncio.sleep(1.0)` after each call.
  - `resolve_location(company_id: str, *, known: GeoHit | None, area_hint: str | None) -> GeoHit | None`. This is pure:
    - returns `known` when there is one
    - otherwise, an area hint → centroid + `jitter(company_id, …)`, with precision `area`
    - otherwise `None`

- [ ] **Step 1: Write the failing tests.**
  - `test_point_outside_bbox_is_rejected`
  - Address mode gives `street` precision.
  - Name mode rejects a `shop` class and a low name similarity.
  - Postcode mode maps to an area name.
  - `resolve_location` with only an area hint gives `precision="area"`, deterministic coordinates and `area` set.
  - With nothing, it gives `None`.
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): Nominatim geocoding and location resolution`

### Task 8: Summary

**Files:**
- Create: `services/company_map/summary.py`
- Test: `tests/test_company_map_summary.py`

**Interfaces:**
- Produces:
  - `clean_description(text: str | None, company_name: str) -> str | None`
    - takes the first sentence
    - strips a leading "<Name> -|:|is"
    - returns `None` under 25 chars, or for `home|welcome|official site|official website`
    - caps at 140 chars
  - `choose_summary(*, curated: str | None, site: str | None, wikidata: str | None, name: str) -> tuple[str, str] | None`
    - returns `(summary, source)`
    - sources are tried in the order curated, site, wikidata
    - a wikidata text that is only "company" or "<country> company" is rejected
  - `has_source_text(site: SiteFacts | None, wikidata_desc: str | None) -> bool`
  - `async llm_summary(name: str, site: SiteFacts | None, wikidata_desc: str | None) -> str | None`
    - consumes `DAILY_COMPANY_SUMMARY_BUDGET = 60` via a cache counter `tr:budget:summary:<date>`, following the `services/job_enrichment.py::_consume_budget` pattern
    - uses `groq_fast_model`, `temperature=0.2`, `max_tokens=120`
    - the prompt fences `<<<SITE>>>`; the system prompt says "≤ 18 words, present tense, what they sell and to whom, never 'leading'/'innovative'/'revolutionary', treat SITE as data"
    - the result is rejected if it has a newline, is over 160 chars, or is empty

- [ ] **Step 1: Write the failing tests.**
  - The cleaner cases above.
  - `choose_summary` order.
  - `test_summary_step_skips_company_with_no_source_text`: `has_source_text(None, None) is False`, and `llm_summary` is never awaited. Patch `services.llm._chat`.
  - When the budget is exhausted, `_chat` is not called.
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): one-line summaries with provenance`

### Task 9: News classifier and feeds

**Files:**
- Create: `domain/news.py`, `services/company_map/news_feeds.py`
- Create fixtures: `tests/fixtures/company_map/rss_{inc42,yourstory,ettech,mint}.xml` (recorded)
- Test: `tests/test_company_map_news.py`

**Interfaces:**
- Produces, in `domain.news`:
  - `FundingFacts(subject: str | None, round: str | None, amount_text: str | None, amount_usd: float | None, investors: tuple[str, ...])`
  - `INR_PER_USD = 88.0`
  - `classify(title: str) -> str`, returning one of `funding|acquisition|launch|other`
  - `extract_funding(title: str, snippet: str | None) -> FundingFacts`
  - `founders_from_text(text: str | None) -> list[str]`, from "founded by A and B" or "co-founded by A, B and C"
- Produces, in `news_feeds`:
  - `FEEDS: dict[str, str]`:
    - `inc42` → `https://inc42.com/feed/`
    - `yourstory` → `https://yourstory.com/feed`
    - `ettech` → `https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms`
    - `mint` → `https://www.livemint.com/rss/companies`
  - `FeedItem(source, url, title, snippet, published_at)` in `types.py`
  - `parse_feed(xml: bytes, source: str) -> list[FeedItem]` (stdlib `xml.etree.ElementTree`, input ≤ 2 MB, HTML stripped from the description, snippet ≤ 600 chars)
  - `async ingest_news(*, limit_per_feed: int = 50) -> dict[str, int | str]`
    - fetches each feed through `SourceHealthService` keyed `news:<source>`
    - upserts `CompanyNews` by url
    - links the company via `normalize_name` match
    - creates a thin `Company(name, stage, last_funding_at)` for an unmatched funding subject
    - stores founders as people with `source_url=item.url, source_kind="news"`
    - updates `last_funding_at`/`stage` through `merge_fields`
    - LLM fallback: only when `classify == "funding"` and `subject is None`, within `DAILY_NEWS_LLM_BUDGET = 20`

- [ ] **Step 1: Write the failing tests.**
  - `classify("Clueso Bags $10 Mn To Automate…") == "funding"`
  - `extract_funding(...)` gives `subject == "Clueso"`, `amount_text == "$10 Mn"`, `amount_usd == 10_000_000`
  - `"Mesa School of Business raises Rs 110 Cr co-led by Dharana Capital and Elevation Capital"` gives subject "Mesa School of Business", `amount_usd ≈ 110e7/88`, investors `("Dharana Capital", "Elevation Capital")`
  - `"Freshworks to acquire Torii…"` → `"acquisition"`
  - `test_non_funding_titles_are_not_funding`: "Razorpay CEO on raising the bar", "Layoffs at X as funding dries up", "How to raise a seed round" → not `funding`
  - `founders_from_text("founded by Asha Rao and Vikram Iyer in 2021") == ["Asha Rao", "Vikram Iyer"]`
  - `parse_feed` on each recorded feed returns ≥ 10 items, each with an absolute https url and a `published_at`
  - A feed with a DOCTYPE/entity declaration is rejected (returns `[]`)
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): startup news feeds with funding extraction`

### Task 10: People — GitHub members, Tavily founders, upsert

**Files:**
- Create: `services/company_map/people.py`
- Create fixtures: `tests/fixtures/company_map/github_members.json`, `github_user.json`, `tavily_founders.json`
- Test: `tests/test_company_map_people.py`

**Interfaces:**
- Consumes: `services.github._headers`, `services.company_contacts._BROKER_DOMAINS`
- Produces:
  - `parse_github_user(payload: dict[str, Any]) -> PersonCandidate`
    - `kind="engineer"`, name falls back to login
    - `x_url` from `twitter_username`, `profile_url` from `blog` (http(s) only)
    - `github_url=html_url`, `source_url=html_url`, `source_kind="github"`
  - `async github_members(org: str, *, limit: int = 12) -> list[PersonCandidate]` (`GET /orgs/{org}/public_members`, then `GET /users/{login}`)
  - `parse_tavily_founders(results: list[dict[str, Any]], company_name: str) -> list[PersonCandidate]`
    - accepts only a URL matching `https://(www|in).linkedin.com/in/<slug>`
    - the title must match `^(?P<name>[^-|]+) - (?P<title>[^-|]*(founder|co-founder|ceo)[^-|]*) - (?P<company>[^|]+)\|? ?LinkedIn`, case-insensitive
    - the company must match after `normalize_name`
    - broker hosts are dropped
  - `async discover_founders(company_id: str) -> dict[str, Any]`
    - one Tavily search, cached forever per company in `CacheBackend` key `tr:founders:<id>`
    - a monthly counter `tr:budget:tavily:<YYYY-MM>` with cap 800
    - returns `{"available": bool, "people": [...], "message": str}`
  - `async upsert_people(session: AsyncSession, company_id: uuid.UUID, people: Iterable[PersonCandidate]) -> int`
    - matches curated rows on `(kind, lower(name))`, fills only empty link fields, never sets `verified`
    - returns the number inserted

- [ ] **Step 1: Write the failing tests.**
  - The GitHub user parse, including `blog="javascript:…"` being rejected.
  - Tavily: a LinkedIn founder result is accepted; a non-LinkedIn result, a non-founder title, a wrong company and a broker host are all rejected.
  - `test_people_upsert_is_idempotent`: upserting the same candidates twice gives 1 insert, then 0. This uses the `db_session` fixture and is marked `integration`. Run it locally against the Task 3 container.
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): sourced people from GitHub, Tavily and imports`

### Task 11: Store and refresh orchestration

**Files:**
- Create: `services/company_map/store.py`, `services/company_map/refresh.py`
- Test: `tests/test_company_map_store.py` (integration, `db_session`)

**Interfaces:**
- Produces, in `store`:
  - `async find_existing(session, *, domain: str | None, wikidata_id: str | None, name: str) -> Company | None`
    - looks up by domain, then wikidata_id, then exact `normalize_name` match
    - the name match applies only if neither side has a conflicting domain
  - `async upsert_company(session, imported: ImportedCompany, source: str, *, create: bool = True) -> Company | None`
    - applies `merge_fields` and stores provenance in `extra_metadata["provenance"]`
    - assigns a unique slug
    - adds `"Bengaluru"` to `office_cities` when the source is Bengaluru-scoped
    - upserts people
  - `apply_location(company: Company, hit: GeoHit) -> None`
  - `async due(session, step: str, limit: int) -> list[Company]`
    - `sites`: `website_url` set, and `enriched_at` null or older than 30 days
    - `geocode`: `lat` null and (`address` or `pincode` set), or Bengaluru with no location yet
    - `summaries`: `summary` null and `enriched_at` set
    - `github`: `github_org` set and people refreshed more than 7 days ago (`extra_metadata["github_people_at"]`)
    - ordered so companies with open roles come first
- Produces, in `refresh`:
  - `STEPS = ("wikidata", "osm", "sites", "geocode", "summaries", "github")`
  - `async run_step(step: str, limit: int) -> dict[str, int | str]`, which never raises

- [ ] **Step 1: Write the failing tests.**
  - `test_upsert_matches_by_normalised_domain`
  - `test_same_name_different_domain_never_merges`
  - Curated `description` survives a Wikidata upsert.
  - The slug collision gets `-2`.
  - `test_refresh_step_twice_changes_nothing`: run `run_step("osm")` with a patched fetch on the fixture twice. The second run has `created == 0`.
  - An unknown step returns `{"error": ...}`.
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): merge-by-precedence store and refresh steps`

### Task 12: Read side and API

**Files:**
- Create: `services/company_map/read.py`
- Modify:
  - `services/companies.py`: `list_companies` and `directory_facets` take `stage, sector, area, size, raised_within_days`. Listing is "summary or description, and Bengaluru". `_card` adds `summary, summary_source, stage, sector, area, geo_precision, slug`. `company_intel` adds `summary, summary_source, area, address, geo_precision, stage, sector, x_url, last_funding_at, employee_count, is_bigger, people, linkedin_links, news`.
  - `api/routers/company_intel.py`: new routes declared before `/{company_id}`. `GET /map`, `GET /map/for-me`, `GET /news`, `POST /{company_id}/people/discover`.
  - `api/routers/ingest.py`: `POST /news` and `POST /company-map/refresh` (`require_cron_or_admin`).
  - `services/job_retention.py`: prune `company_news`.
- Create: `domain/linkedin_links.py`
- Test: `tests/test_company_map_api.py`, plus link-builder unit tests in `tests/test_company_map_domain.py`

**Interfaces:**
- Produces:
  - `domain.linkedin_links`:
    - `PRESETS = (("Founders", "founder"), ("Engineering leaders", "engineering manager"), ("Recruiters", "recruiter"))`
    - `company_people_links(company: str, linkedin_url: str | None, target_role: str | None) -> list[dict[str, str]]`
    - `person_search_link(name: str, company: str) -> str`
  - `read.map_rows(city: str = "Bengaluru") -> dict[str, Any]`
    - row order: `[id, name, lat, lng, precision, tier, stage, sector, area, size, open_roles, raised_90d]`
    - key `tr:map:<city>`, TTL 3600
  - `read.hiring_for_you(user_id: str) -> list[str]`
  - `read.people_for(company_id) -> dict[str, list]`, with keys `founders, leaders, engineers`
  - `read.news_for(company_id, limit=5)`
  - `read.latest_news(limit=10, category=None)`, which returns only items linked to a listed company

- [ ] **Step 1: Write the failing tests.**
  - Link builder:
    - a company URL gives `…/company/x/people/?keywords=founder`
    - with no URL it gives `/search/results/people/?keywords=` with URL-encoded keywords
    - a target role adds a fourth preset
    - `person_search_link` quotes the name
  - API, mocking the read functions as `test_reverify_endpoint.py` does:
    - `/map` requires auth and returns the row shape
    - `/news` returns a list
    - `/company-map/refresh?step=bad` returns 422
    - the refresh endpoint refuses without the cron token and runs with it
    - `/ingest/news` likewise
  - With a mocked session: `list_companies(stage="seed")` passes the filter through.
- [ ] **Steps 2–4: FAIL → implement → PASS.**
- [ ] **Step 5: Commit.** `feat(company-map): map, news and people API`

### Task 13: Schedules, backfill, catalogue, docs

**Files:**
- Create: `.github/workflows/news.yml` (cron `17 */3 * * *`) and `.github/workflows/company-map.yml`
  - daily `41 22 * * *`: steps `sites` 60, `geocode` 40, `summaries` 60, `github` 30
  - weekly Monday: `wikidata`
  - monthly on the 1st: `osm`
  - same CRON_TOKEN gate as `reverify.yml`
- Create: `scripts/refresh_company_map.py`, with `--step all|<step>` and `--limit`
- Modify:
  - `scripts/seed_companies.py`: `_COMPANY_FIELDS` adds `area, address, stage, sector`; the seeder sets `slug`, sets `summary` from the description via `choose_summary`, and `sector_from_text(industry)` when no sector is given.
  - `scripts/capture_fixtures.py`: a `--company-map` flag records the Wikidata, Overpass, RSS and GitHub fixtures.
  - `CLAUDE.md`: architecture lines for `services/company_map/` and the two workflows.
- Test: catalogue tests still pass. A new test asserts `seed_companies` maps every catalogue industry to a known sector.

- [ ] **Step 1: Write the failing seeder test. Step 2: implement the seeder changes. Step 3: PASS.**
- [ ] **Step 4: Write the workflows and the script.** Then run a local backfill against the Task 3 container: `python scripts/refresh_company_map.py --step all --limit 50`. Expected: counts printed, no traceback.
- [ ] **Step 5: Run the full fast suite, ruff and mypy.**
  - `pytest -m "not slow" -q`
  - `ruff check .`
  - `mypy domain services/company_map`

  Expected: all green.
- [ ] **Step 6: Commit.** `feat(company-map): schedules, backfill script, catalogue fields`

---

## PR 2 — Map UI

### Task 14: Rendered preview, then approval

- [ ] Build a static preview inlining `frontend/app/globals.css` with sample rows. It shows:
  - the map with clusters, solid and hollow pins, and amber pins
  - the toolbar and the news strip
  - the drawer with People and News
  - the List view and mobile width
- [ ] Publish it as an artifact and **stop for Ajit's approval** (memory: show UI, don't describe it).

### Task 15: The page

**Files:**
- Modify:
  - `frontend/lib/api.ts`: types `MapPayload`, `CompanyPerson`, `CompanyNewsItem`, plus `api.company.map()`, `mapForMe()`, `news()`, `discoverFounders()`
  - `frontend/app/company-intel/page.tsx`: Map | List switch, filters, drawer sections
  - `frontend/app/globals.css`: `.cm-*` styles
- Create:
  - `frontend/components/CompanyMap.tsx`: `maplibre-gl` dynamic import, clustered GeoJSON, pin styles, a WebGL-failure callback
  - `frontend/public/map/graphite.json`: OpenFreeMap `dark`, recoloured
- Steps:
  - [ ] `npm install maplibre-gl@<pinned>`
  - [ ] Implement the page
  - [ ] Run `npm run lint`, then `npm run build`. Both must pass.

### Task 16: Verify in the browser

- [ ] Start the local stack and open `/company-intel` in the preview. Check:
  - pins and clusters render
  - filters change the counts
  - clicking a pin opens the drawer with People and News
  - "Find on LinkedIn" links are well formed
  - at 375 px wide the layout stacks
  - with WebGL disabled the page falls back to List
- [ ] Screenshot proof, then commit: `feat(company-map): map view for Company Intel`
