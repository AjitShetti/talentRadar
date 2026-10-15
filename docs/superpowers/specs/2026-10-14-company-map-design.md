# Company map — design (phase 1 of the outreach suite)

Status: draft for review 2026-10-14 · two PRs (foundation, then map UI)
Context and reverse-engineering evidence: `2026-10-14-outreach-suite-roadmap.md`

## 1. What this is for

Company Intel today is a ranked list of 399 hand-curated Bengaluru employers,
mostly large ones. It answers "who hires here?". It does not answer "who is
around me, what do they do, who runs it, and who could I talk to?".

Phase 1 turns Company Intel into a **map of every Bengaluru employer we can
source**: startups, VCs, GCCs, big tech and IT services. Each company gets:

- a one-line summary of what it does
- where it is, honest about how precisely we know
- who founded or leads it, with their public X / LinkedIn / GitHub links
- people who publicly say they work there
- recent news, with funding stories tagged
- its open roles

It is also the data foundation phase 2 (outreach) stands on: people with
sources, and a stream of funding news.

The ledger strip states what the system actually holds, for example
"1,240 employers · 870 on the map · 41 raised in 90 days". DESIGN.md asks for
this in place of a bare metric. The page always opens on Map (Ajit, 2026-10-15);
unpinned companies are one chip away, in List.

## 2. Goals, non-goals, anti-goals

**Goals**
- Every Bengaluru **tech** employer we can source, across all tiers
  (startups, scale-ups, unicorns, Big Tech, GCCs, IT services). Decision
  2026-10-15: the map lists tech employers only; a university, hospital,
  brewery or burrito chain that Wikidata happens to know is not listed (§4.2).
- A one-line summary for each, with its provenance shown.
- A location that never claims more precision than we have.
- Founders and leaders, each with a cited public source and the profile links
  that source published.
- For larger companies: headcount, leadership, people on GitHub, and LinkedIn
  deep links.
- Startup news with funding tagged and linked to companies.
- Map and List views with filters. The existing intel drawer is kept and
  extended.

**Non-goals (later, each its own decision)**
- Public / SEO company pages (`/company/[slug]`, `/startups/[area]`).
- User-submitted companies.
- Cities other than Bengaluru. The schema is per-city, but the area taxonomy
  ships for Bengaluru only.
- Ranking people to reach, the outreach tracker and drafts (phase 2).
- The events layer (phase 3).

**Anti-goals: failure even if it works**
- Fetching anything from LinkedIn, or from a people-data broker.
- A solid pin for a company we can only place by neighbourhood.
- A stored person without a source URL. A guessed email.
- An LLM call on any read path (map, list or drawer).
- Copying bangalorestartupmap.com's dataset or prose.
- A map payload that makes first paint wait. Budget: ≤ 100 KB gzipped.

## 3. Data model — migration `015`

### 3.1 `companies`: new columns

| column | type | notes |
|---|---|---|
| `slug` | `varchar(96)` unique | from name; collision → `-2`, `-3` |
| `summary` | `varchar(200)` | one line: what they sell, to whom |
| `summary_source` | `varchar(16)` | `curated \| site \| wikidata \| llm` |
| `stage` | `varchar(16)` | §3.4 |
| `sector` | `varchar(24)` | §3.4 |
| `area` | `varchar(48)` | canonical Bengaluru area (§3.5) |
| `address` | `varchar(300)` | as printed by the source |
| `pincode` | `char(6)` | |
| `lat`, `lng` | `double precision` | |
| `geo_precision` | `varchar(8)` | `exact \| street \| area \| city` |
| `geo_source` | `varchar(16)` | `curated \| wikidata \| osm \| nominatim \| pincode \| area` |
| `wikidata_id` | `varchar(16)` unique, nullable | |
| `employee_count` | `integer` | as-of date in `metadata.employee_count_as_of` |
| `x_url` | `varchar(256)` | the company's own handle |
| `last_funding_at` | `timestamptz` | denormalised from `company_news` for the "raised recently" filter |
| `enriched_at` | `timestamptz` | last website / Wikidata pass |

Indexes: `area`, `stage`, `sector`, `geo_precision`, `last_funding_at`.
`tier` gains the value `vc` and stays nullable; null shows as "Unclassified".
Field provenance lives in the existing JSONB column (`extra_metadata`) under
`provenance: {field: source}` (§4.2). There is no new column for it.

### 3.2 `company_contacts`: people join the existing table

New `kind` values:

- `founder`
- `leader`: CEO, CTO, VP or head of engineering, or a partner at a VC
- `engineer`: a public member of the company's GitHub org

New nullable columns: `x_url`, `github_url`, `profile_url` (personal site),
and `source_kind` (`curated | wikidata | site | news | github | tavily | user`).
A partial unique index on `(company_id, kind, lower(name)) WHERE user_id IS
NULL` dedupes curated people.

The table's rule stands: a curated row always has `source_url`, and
`verified` stays false until a human confirms it.

Because the people live here, the Role Dossier's "way in"
(`services/dossier.py::_way_in`) shows founders with no new code. Phase 2
ranks them. `_serialise` gains the new fields.

### 3.3 `company_news` (new)

| column | type | notes |
|---|---|---|
| `id` | uuid | |
| `url` | `varchar(1024)` unique | upsert key |
| `source` | `varchar(24)` | `inc42 \| yourstory \| ettech \| mint` |
| `title` | `varchar(400)` | |
| `snippet` | `varchar(600)` | the feed's own description, truncated |
| `published_at` | timestamptz | |
| `category` | `varchar(16)` | `funding \| acquisition \| launch \| other` |
| `company_id` | uuid, FK, `ON DELETE SET NULL` | null when unmatched |
| `company_name_raw` | `varchar(200)` | the subject as written |
| `round` | `varchar(24)` | Pre-seed, Seed, Series A … |
| `amount_text` | `varchar(64)` | as printed ("$10 Mn", "Rs 110 Cr") |
| `amount_usd` | `numeric` | approximate, fixed INR→USD constant |
| `investors` | `text[]` | |
| `extracted_by` | `varchar(8)` | `rules \| llm` |
| `created_at` | timestamptz | |

Retention (`services/job_retention.py`): delete non-funding rows older than
180 days, and funding rows older than 2 years.

### 3.4 Taxonomies — `domain/company_taxonomy.py`

- **Stage**: `bootstrapped, pre_seed, seed, series_a, series_b,
  series_c_plus, public, acquired`, plus null for unknown.
- **Sector**: `ai, saas, fintech, consumer, d2c, deeptech, edtech,
  healthtech, logistics, gaming, devtools, it_services, other`.
- **Type**: the existing `tier` (`big_tech, gcc, unicorn, scaleup, startup,
  agency, services`) plus `vc`.
- **Size**: buckets `1–50, 51–200, 201–1,000, 1,000+`, from
  `employee_count`, else from `employee_count_range`.

Mapping is pure and table-driven:

- The curated `industry` free text maps to a sector by keyword. For example,
  "Payments", "Lending", "Banking" and "Insurance" map to `fintech`.
- Wikidata `P452` (industry) maps to a sector by an item-id table.
- A funding round in the news maps to a stage.
- With none of those, the same keyword rules run over the summary. Anything
  still unmatched is `other`.

Type for companies that didn't come from the curated catalogue:

- `vc` when Wikidata classes the company as a venture-capital firm.
- `startup` when it was founded in 2008 or later and is not public or
  acquired.
- Otherwise null ("Unclassified"). Nothing guesses big tech vs services. A
  curated type always wins.

### 3.5 Bengaluru areas — `domain/areas.py`

`domain/areas.py` is the single source of truth, as `domain/geo.py` is for
"in India". Each canonical area has:

- a centroid
- aliases ("hsr sector 3", "koramangala 6th block", "kadubeesanahalli")
- its PIN codes

The list starts from the areas the reference map uses and is extended to
cover the PIN codes. It covers HSR Layout, Koramangala, Indiranagar,
Bellandur, MG Road/CBD, Whitefield, Sarjapur Road, Electronic City,
Bannerghatta Road, Marathahalli, JP Nagar, Jayanagar, Domlur, Mahadevapura,
Manyata Tech Park, Hebbal, Yeshwanthpur, BTM Layout, Ulsoor and others.

Centroids come from OSM `place=suburb|neighbourhood` nodes. They are compiled
once and committed as constants; nothing reads them at runtime. A PIN code is
placed with one Nominatim `postalcode=` lookup, then mapped to the nearest
area centroid. That avoids committing a PIN table.

Pure functions:

- `area_for_text(address) -> str | None`
- `area_for_point(lat, lng) -> str | None`: nearest centroid within 2.5 km
- `jitter(company_id, area) -> (lat, lng)`: a deterministic 150–450 m offset,
  so area-precision pins don't stack

## 4. Where the companies come from

### 4.1 Sources

| # | Source | Adds companies? | What it gives | Cadence | Limits / licence |
|---|---|---|---|---|---|
| 1 | Curated catalogue `data/companies/bengaluru.json` | yes | everything curated. The JSON gains optional `area, address, stage, sector`. It never holds people: `tests/test_company_intel.py` forbids personal names in the catalogue, so founders come only from fetched sources that carry a URL. | on seed | ours |
| 2 | Jobs index | yes, when the employer has a domain and a Bengaluru role | name, domain, open roles | read time | ours |
| 3 | Wikidata SPARQL | yes, for `P31/P279*` business (`Q4830453`) with `P159` = Bengaluru and a website | website, `P625` coords, `P571` inception, `P1128` headcount, `P112` founders / `P169` CEO (with their `P2002` X, `P6634` LinkedIn, `P2037` GitHub), company `P2002` / `P4264` / `P2037`, `P154` Commons logo, description | weekly | CC0 |
| 4 | Company website: homepage, then `/contact` and `/about` if linked | no | meta/og description; a Bengaluru address with a `560xxx` PIN; founder/leader names adjacent to `linkedin.com/in/` or `x.com/` links the company itself published | rolling batches, each site at most once per 30 days | robots.txt respected; 5 s timeout, 300 KB body cap |
| 5 | OSM Overpass, `office=company\|it` with `name` | only objects carrying a `website` tag | coordinates, `addr:*` tags | monthly | ODbL — attribution on the map |
| 6 | Nominatim | no | geocode a printed address; or a name search bounded to Bengaluru | rolling, 1 req/s, ≤ 40 per run | usage policy: identifying UA with contact, results cached on the row, no bulk re-runs |
| 7 | Startup news RSS: Inc42, YourStory, ET Tech, Mint companies | a thin row for a funded company we don't know | news; round, amount, investors; founders named in the item | every 3 h | feeds published for syndication; we keep title, ≤ 600-char snippet, link |
| 8 | GitHub API (existing `services/github.py` token) | no | public org members (self-declared affiliation) with *their own* profile's name, blog, `twitter_username` | the `github` cron step, weekly per company with a `github_org`; ≤ 12 persisted per company. The drawer only reads stored rows, so a GET never writes. | 5,000 req/h |
| 9 | Tavily (existing key) | no | "Find founders" button: one search, `"<company>" founder OR co-founder`. A person is accepted only from a result whose URL is a `linkedin.com/in/…` profile and whose title has the form `<Name> - <title containing founder/co-founder/CEO> - <company> \| LinkedIn`, with the company matching after normalisation. The search engine's result is the source; we never request LinkedIn itself. Broker domains are dropped (`_BROKER_DOMAINS`). | on click, cached per company forever | monthly cap 800 of the free 1,000 |
| 10 | LinkedIn | — | **never fetched.** Deep links only (§6) | — | — |

Logos come from Wikimedia Commons only (`P154` via `Special:FilePath`).
Otherwise the existing initials mark is used. We don't hotlink arbitrary
company sites, which would leak users' IPs to them.

### 4.2 Matching and precedence

- **Identity**: the registrable domain is the key. Normalise it to
  lowercase, without `www.`, with eTLD+1 handling `.co.in`, `.org.in` and
  `.net.in`. It is enforced by the existing `uq_companies_domain`.
  `wikidata_id` is the second key.
- **Name matching** never merges two rows. It only attaches data to an
  existing row, and only when:
  - the names are identical after normalisation (lowercase, punctuation and
    legal suffixes stripped: pvt, private, ltd, limited, llp, inc, corp), and
  - neither side carries a conflicting domain.
- **Field precedence**, highest first: curated > company site > Wikidata >
  OSM > news > LLM.
  - Every write records `provenance[field] = source`.
  - A lower source never overwrites a higher one. A source may refresh its own
    value.
  - Exception: `stage` and `last_funding_at` follow the newest *dated*
    evidence. A Series B announced this month beats a curated "Series A".
- **Listed** (map, list, facets, news): a tech employer (`is_tech IS TRUE`)
  with a `summary` or `description` that is a Bengaluru company. That means
  `office_cities` contains Bengaluru, or `area` is set, or `hq_city` is
  Bengaluru. Other thin rows stay hidden, as today.
- **Tech rule** (decision 2026-10-15). `companies.is_tech` (migration 016) holds
  `domain.company_taxonomy.is_tech`: tech words in the description or industry
  labels say yes ("software", "AI", "platform", "e-commerce", "digital",
  "semiconductor"…), even beside a non-tech word; otherwise a non-tech word
  ("university", "hospital", "bank", "airline", "restaurant", "pharmaceutical"…)
  says no; otherwise a software-ish sector or a tech word in the name says yes.
  No text is not tech. A curated catalogue tier is tech. NULL means "not
  classified yet" and is not listed. The verdict only moves towards `True`, so a
  later source that says less never unlists a company; the `tech` refresh step
  re-reads every row that is not yet `True`.

### 4.3 Location — per company, the first hit wins

1. A curated or Wikidata coordinate inside the Bengaluru bbox → `exact`.
2. An OSM object matched by domain → `exact`.
3. An address from the company's site → Nominatim → `street` if inside the
   bbox. Otherwise, if the address has a PIN code: area centroid + jitter →
   `area`.
4. A Nominatim name search bounded to Bengaluru, accepted only for class
   office/company/building with name similarity ≥ 0.9 → `exact`.
5. A known area (curated, or found in text) → centroid + jitter → `area`.
6. Otherwise → `city`: List only. The map shows "N not pinned".

Rendering follows precision. `exact` and `street` get a solid pin. `area`
gets a hollow ring with the tooltip "Approximate — area only".

### 4.4 One-line summary

Taken from the first of these that exists:

1. The first sentence of a curated description, ≤ 140 chars.
2. The site's meta or og description, cleaned. Strip the site name; reject
   anything under 25 chars or generic ("Home", "Welcome to …").
3. The Wikidata description, when it says more than "company".
4. The LLM.

The LLM step:

- Uses `groq_fast_model`, one call per company.
- Input: name, page title, meta description, Wikidata description, and the
  first 1,500 chars of homepage text. All of it is flattened, bounded and
  fenced as `<<<SITE>>>`, the same pattern as `role_context._flat`.
- Asks for "≤ 18 words, present tense, what they sell and to whom, no
  'leading' or 'innovative'".
- The result is checked for length and single-line form.
- **It runs only when there is real text to summarise**: a meta
  description, homepage text, or a Wikidata description. A name alone never
  goes to the model, because the model would invent a business. A company
  with no source text has no summary and stays unlisted (§4.2).
- Budget: `DAILY_COMPANY_SUMMARY_BUDGET = 60`, counted the same way as
  `services/job_enrichment.py`.

The UI shows the provenance in small type: "from their website", "from
Wikidata" or "AI summary".

### 4.5 People

- **founder**: from Wikidata `P112`, "founded by …" in news, the company site
  (a name next to "founder"/"co-founder" and a profile link), and Tavily on
  demand.
- **leader**: from Wikidata `P169`, the company site (CTO / VP / head of
  engineering), and VC partners.
- **engineer**: public members of the GitHub org only. Repo contributors are
  *not* stored as employees, because they may be outsiders.

Each person has a name; a title only if the source states one; profile links
only if the source printed them (Wikidata IDs become URLs); `source_url`;
`source_kind`; and `verified = false`.

A person with no LinkedIn URL gets a search deep link built at read time and
never stored (§6).

"Bigger company" means `tier ∈ {big_tech, gcc, unicorn, services}`, or
`employee_count ≥ 200`, or a range starting at 201+. For those, the drawer's
People section leads with headcount, leadership and the LinkedIn presets.
For startups it leads with founders.

### 4.6 News

- A GitHub Actions job calls `POST /api/v1/ingest/news` every 3 h. It fetches
  the four feeds and upserts by URL. Each feed has its own source-health
  counter (`news:inc42`, …), reusing `services/source_health.py`.
- The classifier `domain/news.py` is pure:
  - **Category** comes from the title.
  - **Funding subject** is the text before the funding verb ("Clueso **Bags**
    $10 Mn …" → "Clueso").
  - **Amount** covers `$10 Mn`, `Rs 110 Cr` and `INR 25 Cr`.
  - **Round** covers Pre-seed … Series F, Bridge and Debt.
  - **Investors** are the text after "led by", "co-led by" or "from".
  - "founded by A and B" yields founders.
- **Matching**: the subject is matched to a company by normalised name.
  - An unmatched funding item creates a thin company row (name, stage,
    `last_funding_at`). It is not listed on the map until it is a known
    Bengaluru company with a summary. Phase 2's India-wide funding radar
    reads it.
- **LLM fallback**: only when an item is classified funding but the subject
  can't be extracted. At most 20 a day.
- **Map strip**: shows only items linked to a listed company. Everything is
  stored for phase 2.

## 5. API

Everything is under `/api/v1/company-intel` and authenticated, as today.

| Endpoint | Change |
|---|---|
| `GET /map?city=Bengaluru` | **new.** `{generated_at, total, pinned, rows}`. Each row is positional, to stay small: `[id, name, lat, lng, precision, tier, stage, sector, area, size, open_roles, raised_90d]`. Listed companies only; `lat`/`lng` null when `city` precision. Cached in `CacheBackend` for 1 h per city, with an ETag. |
| `GET /map/for-me` | **new.** `{hiring_for_you: [company_id]}`: companies with an open Bengaluru role whose title contains the profile's target-role keywords, using the same normalisation as search. Empty without a target role. Separate so `/map` stays cacheable across users. |
| `GET /` (List) | `+ stage, sector, area, size, raised_within_days` filters |
| `GET /facets` | `+ stages, sectors, areas, sizes`, with counts |
| `GET /{id}` | `+ summary, summary_source, area, address, geo_precision, stage, sector, x_url, people{founders, leaders, engineers}, linkedin_links[{label,url}], news[≤5], last_funding` |
| `POST /{id}/people/discover` | **new.** The Tavily founder lookup (§4.1 #9). Returns a clear message when the monthly cap is reached. |
| `GET /news?limit=10&category=funding` | **new.** The strip. |

Scheduled work goes under `/api/v1/ingest`, behind `require_cron_or_admin`:

- `POST /news`
- `POST /company-map/refresh?step=wikidata|osm|sites|geocode|summaries|github&limit=N`
  - Each step processes a bounded batch of *due* rows and returns counts. A
    row is due when, for example, `enriched_at` is null or older than 30 days.
    Companies with open roles go first.
  - It never raises. A source error comes back as `{"error": …}` and the
    next run retries.

Workflows follow the same skip-without-`CRON_TOKEN` gate as `reverify.yml`:

- `.github/workflows/news.yml`: every 3 h.
- `.github/workflows/company-map.yml`:
  - daily: `sites` 60, `geocode` 40, `summaries` 60
  - weekly: `wikidata`
  - monthly: `osm`

The one-off backfill is `scripts/refresh_company_map.py --step all`, run
locally against Neon. It calls the same service functions.

## 6. LinkedIn deep links — `domain/linkedin_links.py` (pure)

- **Company page known** (`linkedin_url`, 193 curated today, or Wikidata
  `P4264`): `<company_url>/people/?keywords=<preset>`. This is the company's
  people tab, filtered.
- **Otherwise**: `https://www.linkedin.com/search/results/people/?keywords=<company> <preset>`.
- **Presets**:
  - Founders
  - Engineering leaders: "engineering manager", "head of engineering"
  - Recruiters: "recruiter", "talent acquisition"
  - The user's target role, from their profile
- **A named person without a profile link**:
  `…/search/results/people/?keywords="<name>" <company>`.

The server never requests any of these. They open in a new tab with
`rel="noopener"`, inside the user's own LinkedIn session. Before shipping,
the builder checks each URL shape in a signed-in browser.

## 7. The page

The route stays `/company-intel`. The nav label stays **Company Intel**
(PRODUCT.md brand names).

- **Ledger strip**: "Company Intel — Bengaluru · 1,240 employers · 870 on
  the map · 41 raised in 90 days". Every number is in `FlapText`.
- **Toolbar**:
  - search
  - Type, Area, Stage, Sector and Size selects
  - toggles "Hiring now" and "Raised ≤ 90 days"
  - a **Map | List** switch, persisted with `usePersistentState`
- **Map**:
  - **Library**: `maplibre-gl`, dynamically imported on this route only.
  - **Tiles**: OpenFreeMap vector tiles, with our own style JSON
    (`frontend/public/map/graphite.json`) derived from OpenFreeMap's `dark`
    and recoloured to DESIGN.md tokens. Land is Board Paper, water a surface
    tone, roads hairline, labels faint.
  - **Clustering**: the GeoJSON source uses `cluster: true`. Clusters are
    graphite discs with a Fragment Mono count.
  - **Pins**: steel (`--ink-2`) dots; a hollow ring for area precision.
    **Amber is lit only** for the selected company and for companies hiring
    for the user's target role (the One Lamp Rule).
  - **Filtering**: all filters run client-side on the loaded rows, as on the
    reference site, with no refetch.
  - **Initial view**: fit to the Bengaluru bbox.
  - **Unpinned companies**: a chip, "128 not pinned — see List".
  - **Attribution**: "© OpenStreetMap contributors · OpenFreeMap".
- **News strip**: a collapsible bottom-left panel, "Latest", with 5 items and
  a "Funding" tag. A linked item opens the company drawer. An unlinked one
  opens the article.
- **List**: the existing ranked `.ci-card` rows with the new filters. The
  look is unchanged.
- **Drawer** (the existing `.ci-drawer`, extended), in order:
  1. Header: logo, name, summary + provenance, tier/stage/sector codes, area
     and address with a precision note.
  2. Open roles.
  3. **People**: Founders · Leadership · On GitHub · Find on LinkedIn.
  4. **News**.
  5. Tech stack.
  6. Open source (existing).
  7. Talent contacts (existing).

  An empty section says so plainly ("No founders on record") and offers its
  action ("Find founders").
- **Below 700px**: the map is full height under the strip, filters move into
  a sheet, and the drawer is full width.
- **Map fails** (WebGL off, tiles down): switch to List with a one-line note.
- **Process**: a rendered preview artifact, built with the real
  `globals.css`, is shown for approval before any UI code is written.

## 8. Free-tier budget

| Resource | Use |
|---|---|
| Neon (0.5 GB) | companies +~1–2k rows (~5 MB with metadata); people ≤ ~5k rows (~2 MB); news ~40/day (~3 MB over 180 days) |
| Render (512 MB) | no new resident memory. The cached map payload is ~100 KB. Site fetches are bounded batches: concurrency 4, 5 s timeout, 300 KB body cap. |
| Vercel bundle | `maplibre-gl` ~220 KB gz, on this route only |
| Groq | summaries ≤ 60/day until backfilled; news fallback ≤ 20/day; nothing on read paths |
| Tavily | on demand only, cached; monthly cap 800 of the free 1,000 |
| GitHub API | token, 5,000/h; drawer lookups cached 7 days |
| Nominatim | 1 req/s, ≤ 40 per run; results stored on the row |
| GitHub Actions | free (the repo is public) |

## 9. Failure behaviour

Every source is optional. A failure leaves the last good data in place.

- **Only the curated catalogue**: the map still works. Most pins are area or
  city precision, and the page may open on List (§1).
- **Tiles or WebGL fail**: the page switches to List.
- **No LLM**: the summary falls back to the site or Wikidata, or shows "No
  summary yet".
- **A feed or importer is down**: its health counter records it, the step
  returns an error, and the next scheduled run retries.

## 10. Testing

- **Pure unit tests**:
  - areas: text, PIN and point lookups, and jitter determinism
  - taxonomy mapping from real curated industry strings
  - domain normalisation, matching and precedence merging
  - the news classifier and extractors, against 30+ real titles from the four
    feeds
  - the summary cleaner
  - the LinkedIn link builder
- **Importers** run against recorded fixtures: Wikidata JSON, Overpass JSON,
  the four RSS files, three homepages, GitHub members/users JSON and
  Nominatim JSON. `make capture-fixtures` is extended to record them, and
  offline contract tests replay them, as `tests/test_source_contracts.py`
  does.
- **API tests**: the `/map` shape and cache, the filters, the drawer payload,
  and cron auth.
- **Migration**: `015` up and down on a scratch database.
- **Frontend**: lint and build, then a browser check of the map, filters,
  drawer, mobile width and the WebGL-off fallback.

## 11. Delivery

- **PR 1 — foundation** (API only; the current UI keeps working):
  - migration `015`
  - `domain/{areas,company_taxonomy,news,linkedin_links}.py`
  - `services/company_map/` (importers, location, summary, people, news)
  - cron endpoints and the two workflows
  - the backfill script, the catalogue JSON extension and the seeder update
  - the API changes and their tests
- **PR 2 — map UI**: preview artifact, then the page, then the browser check.

## 12. Calls I made — check these

1. **Bengaluru only for now.** The schema is per-city.
2. **In-app only.** No public or SEO company pages yet: they would publish
   people's names on the open web, and deserve their own decision.
3. **MapLibre + OpenFreeMap rather than Leaflet + CARTO** (the reference
   site's choice). It is free with no key or quota, and vector tiles let the
   map wear the app's graphite palette. The cost is ~220 KB gz on this one
   route.
4. **Amber pins** only for the selected company and for companies hiring for
   your target role.
5. **GitHub *public org members* count as "works there".** Repo contributors
   do not.
6. **News is stored India-wide**, because phase 2 needs it. The map's strip
   shows Bengaluru-linked items only.
7. **No person without a source URL** is ever stored. LinkedIn links are
   built at read time and never requested.
8. **Logos** come from Wikimedia Commons only; otherwise initials.
9. **Entrackr is dropped** until a working feed URL is found.
10. **Type for imported companies**: `vc`, `startup` (founded 2008+ and not
    public), or Unclassified. Nothing guesses big tech vs services.
11. **The LLM never summarises a bare name.** A company with no source text
    stays unlisted rather than getting an invented summary.
12. **GitHub people are fetched by the scheduled job.** Opening the drawer
    never triggers an outside call or a write.
13. **"Find founders" accepts a person only** from a search result that is
    itself a LinkedIn profile titled with a founder/CEO role at that company.

## 13. Left to the builder

- The exact SPARQL class filter and the Overpass query. Check the counts
  against §3.4 of the roadmap.
- The area list, its centroids and the PIN table (compiled once, committed).
- The details of recolouring the style JSON.
- The exact regexes in the news classifier, driven by the fixtures.
