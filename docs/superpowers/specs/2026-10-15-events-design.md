# Events — design (phase 3 of the outreach suite)

Status: draft for review 2026-10-15 · one PR, built after Outreach
Context: `2026-10-14-outreach-suite-roadmap.md` (§3.3 Eventbrite) and
`2026-10-14-company-map-design.md` (the map, the drawer, the refresh pattern)

## 1. What this is for

Outreach (phase 2) gets a user talking to people at companies they want to
work for. Most of those conversations go further when they start in a room:
a meetup talk, a demo night, a community mixer. Bengaluru has several of
these every week, scattered across Luma calendars, Google Developer Group
chapter pages and Eventbrite listings. Nobody looks at all three.

Events puts them on one list for Bengaluru: the tech, networking-oriented
gatherings a job seeker can walk into this week, with who hosts them. The
user can:

- save an event, or mark that they are going;
- add it to their own calendar with an `.ics` file;
- note who they met there, so the next step is an Outreach thread
  ("met at an event") rather than a forgotten business card.

It ties the suite together. A company on the map shows the events it hosts.
The Overview day plan says "the meetup you saved is tonight". The people met
there become the warmest outreach a user has.

The ledger strip states what the system holds, as the map's does:
"Events — Bengaluru · 41 upcoming · 9 this week · 2 you're going to".

## 2. Goals, non-goals, anti-goals

**Goals**
- Every upcoming tech, networking-oriented event in Bengaluru that we can
  read from Luma, GDG and Eventbrite through means each of them permits, plus
  online events run from India.
- A deterministic relevance verdict with its reasons ("GDG chapter · AI ·
  in Koramangala"), never a score.
- Save / going, an `.ics` download, and private notes on people met.
- A read-only interface Outreach consumes to turn "met at an event" into a
  thread, without either feature importing the other's tables.
- An `/events` page (list by date, filters, an optional map layer, detail
  view), an "Events" block in the company drawer, and day-plan cards on the
  Overview.

**Non-goals (later, each its own decision)**
- Sources beyond the three Ajit chose. Hasgeek and KonfHub are recorded as
  next candidates (§4.5); Meetup is not (§4.5).
- Cities other than Bengaluru.
- A subscribable per-user calendar feed (`webcal://…`). It needs a secret
  token in a URL; the `.ics` download covers the need first.
- Recommending events by profile ("events for backend engineers"). Filters
  come first; a ranking can come once there is usage to rank against.
- Public / SEO event pages.

**Anti-goals: failure even if it works**
- Buying a ticket, registering or RSVPing on the user's behalf. The only
  action toward a source is a link the user clicks.
- Reading Eventbrite pages, or any source's internal API, in a way its terms
  or robots.txt forbid (§4).
- Fetching a URL the user pasted. A pasted link is matched against strict
  patterns and the request is built from the captured id, as
  `domain/posting_urls.py` does for `/public/check`.
- An LLM call anywhere in this feature.
- Looking up, enriching or publishing the people a user says they met.
  They are the user's private notes.
- Marking an event cancelled because a feed stopped listing it. Only an
  explicit cancellation from the source cancels (the liveness rule: a missing
  answer is not a "closed").
- A solid pin for an event whose address the source withholds.

## 2a. Decided by Ajit

| Topic | Decision | Source |
|---|---|---|
| Sources | Eventbrite **plus** Luma **plus** Google Developer Group community events | roadmap 2026-10-14, brief 2026-10-15 |
| Scope | Bengaluru, tech, networking-oriented | 2026-10-15 |
| Order | Built after Outreach, and ties into it. The branches are built in parallel; Events merges second. | 2026-10-15 |
| People met | An Outreach channel: "met at an event" | 2026-10-15 |
| Standing | Zero cost; nothing sent for the user; India only; never fetch LinkedIn; no guessed contacts | roadmap §2 |
| Delegation | "Go with your recommendations": the remaining design calls are Claude's, each marked below | 2026-10-15 |

## 3. Constraints

- **Zero cost.** Render 512 MB, Neon 0.5 GB, Vercel, GitHub Actions cron.
  The Eventbrite token is a free personal token; without it, Eventbrite is
  simply off.
- **Scheduled work** is a GitHub Actions workflow calling an `X-Cron-Token`
  endpoint, one bounded step per call, that never raises (the company-map
  pattern, `services/company_map/refresh.py`).
- **Respect each source's terms and robots.txt** (§4). Each source sits
  behind its own `SourceHealthService` breaker.
- **India / Bengaluru only.**
- **Nothing is sent for the user.** Notes on people stay private.
- Pure rules in `domain/`, thin I/O in `services/events/`, thin routers,
  mypy strict, ruff, tests first, contract tests replaying recorded
  fixtures for every source parser.
- **DESIGN.md** is the design system: hairline rows, the One Lamp Rule,
  FlapText on live numbers, keyboard operable, 375 px.
- **Overview is a day plan**: new items are things to do today and go
  through the copilot card / `buildActions` ordering.
- **Migration** `018_events`, `down_revision = "016_company_is_tech"` on
  this branch; the controller re-points it to `017_outreach` at merge. It
  creates only this feature's tables.

## 4. Sources — what each permits, and what we do

Checked live and read-only on 2026-10-15, about a dozen requests in all (the
four robots.txt files plus three optional sources, one page or feed per
source, the published terms). No crawling.

### 4.1 Luma — the Bengaluru iCal feed plus curated organiser calendars

**Observed.**
- `luma.com/robots.txt` has rules for Googlebot only (`/social-share`,
  `/in/`, `/company/`, `/session-*`). Nothing applies to other agents.
- Luma's terms say you must not access the service "by any means other than
  our publicly supported interfaces", and must not republish site content
  except through them.
- Luma's help centre documents **iCal subscriptions** as a supported feature.
  There is one per calendar, and one per Discover city page, which carries
  "all upcoming approved events in that city". Calendar apps fetch them on a
  schedule.
- The Bengaluru city feed is
  `https://api.lu.ma/ics/get?entity=discover&id=discplace-G0tGUVYwl7T17Sb`.
  It returned `text/calendar` with 19 `VEVENT`s, nearly all of them tech:
  South Park Commons, Lossfunk, Swift Bengaluru × Okta, AI Garage's Voice AI
  Buildathon, Qiskit workshops, a product mixer.
  - Each event carries `UID evt-…@events.lu.ma`, `DTSTART`/`DTEND` in UTC,
    `ORGANIZER;CN="…"`, `SUMMARY` and `GEO` lat;lng.
  - `LOCATION` holds the event's Luma URL, and the `DESCRIPTION` begins with
    the public page URL and ends with "Hosted by …".
  - The street address is withheld ("Check event page for more details").
  - Every `STATUS` is `TENTATIVE`.
  - The feed declares `REFRESH-INTERVAL: PT12H`.
- The public Luma API needs Luma Plus (paid), and each key manages a single
  calendar. It is no use to us.

**What we do.** *Claude's call (delegated 2026-10-15): read Luma only through
its published iCal subscriptions. That means the Bengaluru Discover feed plus
a curated list of Bengaluru tech organiser calendars (`entity=calendar&id=cal-…`),
at most once per 12 h each, as its `REFRESH-INTERVAL` asks.* Reason: the iCal
subscription is a publicly supported interface built to be fetched by
machines. The discover page's embedded JSON and Luma's internal API are not
supported interfaces. The paid API is out under zero cost.

Display rules that follow from the terms:
- Title, organiser, time and area are shown.
- The description is stored bounded (≤ 600 chars), used for classification,
  and shown cut to ≤ 200 chars.
- The primary action is always "Register on Luma ↗".
- Images are never downloaded.

The `cal-…` ids for organiser calendars are taken by hand from each
calendar's "Add iCal Subscription" button, never scraped.

### 4.2 Google Developer Groups — public chapter and event pages within robots.txt

**Observed.**
- `gdg.community.dev/robots.txt` disallows `/api/`, `/gql/`, `/accounts/`,
  `/u/`, `/newsletter/`, `/triggers/` and `/health/` for every agent, with
  `Crawl-delay: 2`.
- The site's participation terms are "the agreement between you and Google".
  Google's Terms of Service forbid automated access "in violation of the
  machine-readable instructions on our web pages (for example, robots.txt
  files…)".
- A chapter page (`/gdg-bangalore/`, chapter id 793) is server-rendered. Its
  `<script type="application/json">` carries
  `props.pageProps.prerenderData.upcomingEvents.results[]`, with `title`,
  `start_date` (UTC), `url`, `event_type_title` and `description_short`.
  GDG Bangalore had 0 upcoming and 100 past events on the day.
- Each event page (`/events/details/<slug>/`) has a schema.org `Event`
  JSON-LD block:
  - `startDate` / `endDate` with `+05:30`
  - `location.name` and a `PostalAddress` with `postalCode 560001`
  - `eventStatus`, `eventAttendanceMode`, and `offers.url` (external
    registration)
  - `addressCountry` was `"US"` for a Bengaluru venue, so it is not trusted.
- There is no iCal feed. The JSON endpoints (`/api/event_slim/for_chapter/…`)
  are disallowed.

**What we do.** *Claude's call (delegated 2026-10-15): read a curated list of
Bengaluru chapter pages and their upcoming events' detail pages, never
`/api/` or `/gql/`. Wait 2 s between requests, make at most 30 requests per
run, and fetch an event's page at most once per 3 days unless its start time
changed.* Reason: robots.txt is the machine-readable instruction Google's
terms point to. Those pages are allowed, and the embedded JSON and JSON-LD
are what the page itself serves. Like `domain.geo`, India is decided from
`addressLocality` / `postalCode`, never from `addressCountry`.

*Claude's call (delegated 2026-10-15): "GDG on Campus" chapters are excluded.*
Reason: they are student-only events at a college. The curated list holds
city chapters (GDG Bangalore, GDG Cloud Bangalore, WTM Bangalore and the
like), which the builder verifies.

### 4.3 Eventbrite — the official API only

**Observed.**
- `eventbrite.com/robots.txt` allows `/d/` search pages for `*`. It
  disallows `/api/v3/destination/events/` (which page 2 of a search calls,
  roadmap §3.3), `/rss/` and `/directory/`.
- Eventbrite's Terms of Service carry a provision against scraping and
  automated data extraction. The roadmap noted this too.
- Event **search** in API v3 (`GET /events/search/`) was shut down on
  12 Dec 2019. Still documented:
  - `GET /v3/events/{id}/` (retrieve an event)
  - `GET /v3/venues/{id}/events/` (events at a venue)
  - `GET /v3/organizations/{id}/events/` (with an organizer filter; it may
    require membership)
- API access needs a personal OAuth token from a free account. The API Terms
  grant a "personal, non-exclusive, … revocable, limited" licence, and
  stored content must be deleted within 30 days if access ends.

**What we do.** *Claude's call (delegated 2026-10-15): Eventbrite is read
only through API v3 with a personal token (`EVENTBRITE_TOKEN`, optional).
Its public `/d/` pages are not read, and neither is their JSON-LD.* This
overrides the roadmap's 2026-10-14 note ("read only public pages, on a
schedule, at a low fixed rate"), because the brief says to prefer an
official API wherever a source's terms forbid scraping, and Eventbrite's do.

Discovery without search comes from three things:
1. **A curated list of Bengaluru tech organisers.** Their live events come
   from `GET /v3/organizers/{id}/events/?status=live&order_by=start_asc`. If
   that path is not served to a non-member token, the builder falls back to
   item 2 and records the finding in the source list.
2. **A curated list of recurring Bengaluru tech venues**, read through
   `GET /v3/venues/{id}/events/`.
3. **User-pasted Eventbrite links.** The numeric id is taken from the URL
   and resolved with `GET /v3/events/{id}/?expand=venue,organizer,format`.

Coverage will be thinner than the 37-event `/d/` page. That trade is
deliberate (Calls to check #1). Without a token, the step returns
`{"skipped": "no token"}` and Eventbrite links are "not readable right now".

Retention follows the API Terms. If `EVENTBRITE_TOKEN` is removed for good,
an admin step (`step=purge&source=eventbrite`) deletes stored Eventbrite
rows.

### 4.4 User-pasted links (any of the three)

"Add an event by link" accepts only URLs matching strict patterns in
`domain/event_urls.py`, and builds every request from the captured piece:

| Pasted | Captured | Request |
|---|---|---|
| `https://www.eventbrite.{com,co.in,…}/e/<slug>-<digits>` | the digits | `GET /v3/events/<digits>/` (API) |
| `https://gdg.community.dev/events/details/<slug>/` | the slug `[a-z0-9-]{1,160}` | the event page (robots-allowed) |
| `https://luma.com/<slug>`, `https://lu.ma/<slug>`, `luma.com/event/evt-…` | the slug or evt id | **no request.** It is looked up among events already read from a Luma feed. If it is absent, the user is offered "Add it by hand". |

*Claude's call (delegated 2026-10-15): a pasted Luma link is never fetched.*
Reason: an event page is not a "publicly supported interface", and an event
worth saving is usually already in the Bengaluru feed or an organiser's.

A link that matches no pattern, or cannot be resolved, opens a short
**manual form**: title, date and time, venue or "online", and the link. The
result is a private event (`owner_user_id` set), visible only to that user.
Imports are capped at 10 a day per user (`CacheBackend.incr`, failing open
like the LLM budgets) because two of the paths make an outbound request.

### 4.5 Other India tech sources (optional, checked, not in v1)

| Source | Finding | Verdict |
|---|---|---|
| Hasgeek | robots.txt only blocks `/account` and `/login`. It is Bengaluru-heavy: Rootconf, The Fifth Elephant, Rust Bangalore, the Bengaluru Systems Meetup. Its pages carry no Event JSON-LD on the home page. A per-project schedule iCal is likely (the platform is open source), but it is unverified. | **Next source.** Verify its terms and an iCal path, then add it to the registry with a fixture. |
| KonfHub | robots.txt allows everything and invites AI crawlers. It publishes `llms.txt`. | **Candidate.** Check its terms and whether a feed exists. |
| Meetup | robots.txt blocks `/api/`, `/gql*` and RSS/atom paths. API access needs a paid Meetup Pro plan, and its terms forbid scraping. | **Out.** It is paid, or forbidden. A pasted Meetup link goes to the manual form. |

*Claude's call (delegated 2026-10-15): v1 ships Ajit's three sources only.*
Reason: each new source is a terms check and a fixture, and three already
cover the city's main tech calendars.

## 5. Data model — migration `018_events`

Four new tables. Times are `timestamptz` (absolute). Everything a person
reads ("tonight", "Thu 16 Oct · 18:30–20:00", the `.ics`) is computed in
Asia/Kolkata.

*Claude's call (delegated 2026-10-15): Asia/Kolkata is the fixed offset
`+05:30` (`domain/event_time.IST`), not a `zoneinfo` lookup.* Reason: India
has no DST, and a fixed offset needs no `tzdata` on Windows dev machines or
on the slim image.

### 5.1 `events`

| column | type | notes |
|---|---|---|
| `id` | uuid | |
| `source` | `varchar(16)` | `luma \| gdg \| eventbrite \| user` |
| `external_id` | `varchar(160)` | Luma `evt-…`; GDG slug; Eventbrite numeric id; for `user`, the row id |
| `url` | `varchar(1024)` | the public event page (https only) |
| `title` | `varchar(300)` | |
| `snippet` | `varchar(600)` | the description, de-boilerplated, flattened and bounded |
| `starts_at` | timestamptz, not null | |
| `ends_at` | timestamptz | null when unknown. Readers assume start + 2 h. |
| `venue_name` | `varchar(200)` | |
| `address` | `varchar(300)` | as printed |
| `area` | `varchar(48)` | canonical, from `domain.areas` |
| `lat`, `lng` | double precision | |
| `geo_precision` | `varchar(8)` | `exact \| area \| none` (§6.4) |
| `is_online` | boolean, default false | |
| `is_free` | boolean, nullable | null = the source did not say |
| `organiser_name` | `varchar(200)` | |
| `organiser_url` | `varchar(512)` | |
| `hosts` | `text[]` | "Hosted by …" names, when stated |
| `format` | `varchar(16)` | `meetup \| talk \| workshop \| hackathon \| conference \| mixer \| demo \| hiring \| other` |
| `topics` | `text[]` | the closed vocabulary of §6.3 |
| `listed` | boolean, default false | the relevance verdict (§6) |
| `reasons` | `text[]` | the evidence behind `listed`, in display order |
| `is_cancelled` | boolean, default false | only from an explicit source signal |
| `duplicate_of` | uuid FK `events.id` `ON DELETE SET NULL` | §5.5 |
| `owner_user_id` | uuid FK `users.id` `ON DELETE CASCADE`, nullable | set only for manual events |
| `first_seen_at`, `last_seen_at` | timestamptz | `last_seen_at` moves on every sighting |
| `detail_fetched_at` | timestamptz | GDG/Eventbrite detail reads |
| `metadata` | jsonb | raw taxonomy ids (Eventbrite category/subcategory/format), source scope, link reasons |
| `created_at`, `updated_at` | timestamptz | |

Constraints and indexes:
- unique `(source, external_id)`
- `ix_events_starts_at`
- `ix_events_listed_starts_at (listed, starts_at)`
- `ix_events_duplicate_of`
- `ix_events_owner_user_id`

Events that start more than **90 days** ahead are not stored yet. They come
back in a later run.

### 5.2 `event_companies` — an event linked to companies on the map

| column | type | notes |
|---|---|---|
| `event_id` | uuid FK `ON DELETE CASCADE` | |
| `company_id` | uuid FK `companies.id` `ON DELETE CASCADE` | |
| `role` | `varchar(12)` | `organiser \| host` |
| `matched_on` | `varchar(24)` | `organiser_name \| organiser_domain \| host_name \| title_pair \| title_at` |

The primary key is `(event_id, company_id)`. There is a join table because
"BitGo x Giottus" names two companies.

### 5.3 `event_saves` — the user's side

| column | type | notes |
|---|---|---|
| `id` | uuid | |
| `user_id` | uuid FK `users.id` `ON DELETE CASCADE` | |
| `event_id` | uuid FK `events.id` `ON DELETE CASCADE` | |
| `status` | `varchar(8)` | `saved \| going` |
| `note` | `varchar(2000)` | the user's own note on the event |
| `created_at`, `updated_at` | timestamptz | |

There is a unique `(user_id, event_id)`.

### 5.4 `event_contacts` — people the user met

| column | type | notes |
|---|---|---|
| `id` | uuid | |
| `user_id` | uuid FK `users.id` `ON DELETE CASCADE` | |
| `event_id` | uuid FK `events.id` `ON DELETE CASCADE` | |
| `name` | `varchar(200)`, not null | |
| `title` | `varchar(200)` | |
| `company_name` | `varchar(200)` | as typed |
| `company_id` | uuid FK `companies.id` `ON DELETE SET NULL` | resolved by exact normalised name at write time |
| `linkedin_url`, `x_url`, `github_url` | `varchar(512)` | https, host-checked |
| `email` | `varchar(254)` | only what the person gave the user. The UI says so. |
| `note` | `varchar(2000)` | |
| `created_at`, `updated_at` | timestamptz | |

The index is `(user_id, created_at)`.

*Claude's call (delegated 2026-10-15): people met live in their own
user-private table, not in `company_contacts`.* Reason: `company_contacts`
rows are either curated (sourced, `verified=false`) or a user's contact *at a
company*. A person met at an event may have no company, and these are
private notes about a meeting. Outreach decides what to copy (§8).

### 5.5 Dedupe across sources

- **Within a source**, upsert on `(source, external_id)`. A re-run changes
  nothing (Review Focus 5).
- **Across sources**, there is the pure rule `domain.event_identity.same_event(a, b)`.
  Two events are one event when all three hold:
  - their starts are within 45 minutes;
  - their normalised title tokens have a Jaccard index ≥ 0.5. Normalising
    lowercases, folds to ASCII, drops punctuation and drops stop tokens:
    `the a an and x with at in of for bangalore bengaluru blr meetup
    edition 2026…`.
  - both are online, or they are within 1.5 km, or share an area, or either
    location is unknown.
- **The primary** is the row stored first. A later match sets
  `duplicate_of = primary.id` and is never listed. The primary's detail
  shows "Also on Eventbrite ↗" from its duplicates. *Claude's call (delegated
  2026-10-15): earliest-stored wins, not a source precedence.* Reason: it is
  stable and idempotent, and a duplicate carries nothing the primary lacks
  apart from its link.
- Candidates are found with an indexed `starts_at` window (±45 min). The
  comparison is pure.

### 5.6 Retention (`services/job_retention.py::prune_events`, 0.5 GB)

Run in the same pass as `prune_company_news`, with its own rollback:

1. An event with **no save and no contact** goes 14 days after it ends. A
   cancelled one goes 2 days after its start. An unlisted one goes as soon
   as it has ended.
2. An event that **someone saved or noted people at** is never pruned by
   age: it is that user's history. It goes when the last save and contact
   are gone.
3. **Under pressure** (`STORAGE_HIGH_WATER_BYTES`), unsaved past events go
   first, oldest first, inside `MAX_DELETIONS_PER_RUN`.

Budget: about 150–300 upcoming rows at about 1.5 KB is under 0.5 MB, and
history is pruned. Per-user saves and contacts are a few KB each.

### 5.7 Linking an event to a company

`domain.event_identity.company_candidates(organiser, hosts, title)` returns
`(name, role, matched_on)` from:

- the organiser name (`organiser`);
- each "Hosted by" name (`host`);
- the two sides of a title pair "A x B", "A × B" or "A + B" (`host`);
- the trailing "@ Name" or "at Name" of a title (`host`).

`services/events/store.py` resolves each one with
`services.company_map.store.find_existing(domain=…, name=…)`. The domain
comes from the Eventbrite organiser's website through
`normalize_domain`. It links only to a company with `is_tech IS TRUE`.

**An event never creates a company.** An ambiguous name links nothing (the
same rule as the map: name matching never guesses). Person names ("Hosted by
Ila Quill") match no company, so they link nothing.

## 6. Relevance — `domain/event_relevance.py` (pure)

`assess(facts: EventFacts, scope: SourceScope, host_is_listed_company: bool) -> Verdict`
returns `Verdict(store: bool, listed: bool, format: str, topics: tuple[str, ...],
reasons: tuple[str, ...])`. It is a verdict with evidence, in the style of
`domain/liveness.py`.

### 6.1 Where — decides `store`

- **In person**: `lat/lng` inside `domain.areas.BENGALURU_BBOX`. Or, with no
  coordinates, an address, venue or locality that `domain.geo.resolve_city`
  resolves to Bengaluru, or that `area_for_text` places. The reason reads
  "in <area>" or "in Bengaluru".
- **Online India**: `is_online`, plus at least one India signal:
  - the source scope is Indian (the Luma Bengaluru Discover feed, a curated
    calendar or chapter marked `india`);
  - the Eventbrite `start.timezone` is `Asia/Kolkata`;
  - the title or snippet says India, IST or Bengaluru.

  The reason reads "online · India".
- Anything else is **not stored**: an online event with no India signal, or
  an in-person event elsewhere (SPC's San Francisco events in its calendar
  feed, say).

### 6.2 What — decides `listed`

An event is listed when all three of these hold:

1. **Tech.**
   - A GDG chapter is tech.
   - A curated Luma calendar marked `trusted_tech` is tech.
   - Eventbrite category `102` (Science & Technology), or subcategory
     `2004` (High Tech), is tech.
   - Otherwise, any word of `TECH_WORDS` in the title or snippet, matched as
     a whole word: ai, ml, llm, genai, agents, data, cloud, kubernetes,
     devops, sre, backend, frontend, react, python, rust, golang, java,
     swift, kotlin, android, ios, flutter, security, open source, developer,
     engineering, product management, ux, figma, startup, founders, saas,
     hackathon, buildathon, api, database, quantum, qiskit, embedded,
     hardware, robotics, iot, semiconductor, fintech, web3, crypto custody…
2. **A networking-capable format** (§6.3):
   - in person, any format but `other` qualifies, and so does `other` when
     tech;
   - online, only `hackathon`, `workshop` or `conference`. A webinar meets
     nobody.
3. **Not excluded.** `EXCLUDE_PATTERNS` covers:
   - paid courses: "certification", "course", "training program",
     "masterclass" when `is_free is False`;
   - "free demo class";
   - "placement guarantee";
   - trading, forex, stock-market and astrology events;
   - network marketing;
   - speed dating and singles events;
   - yoga, meditation and wellness when no tech word is present;
   - anything that runs longer than 3 days (a course, not an event).

   `hiring` format (job fair, walk-in, placement drive) is listed **only**
   when `host_is_listed_company`. That keeps fee-charging placement drives
   out.

*Claude's call (delegated 2026-10-15): the rules are word lists with
whole-word matching, tested against real titles from the fixtures, and they
are re-run over stored rows by a `relevance` refresh step after any change.*
Reason: they are deterministic, reviewable and free. The cost is the
occasional miss, which a pasted link or the manual form covers.

### 6.3 Format and topics (closed vocabularies)

- **Format**, first match wins:
  - `hackathon`: hackathon, buildathon, jam
  - `conference`: conference, summit, conf, devfest, fest, I/O Extended
  - `demo`: demo day, demo night, showcase
  - `workshop`: workshop, hands-on, 101, bootcamp (free), codelab
  - `talk`: talk, fireside, panel, AMA, "-1 to"
  - `mixer`: mixer, social, networking, connect, meet & greet
  - `meetup`: meetup, community, chapter
  - `hiring`: job fair, walk-in, hiring event, placement drive
  - `other`
- **Topics**: `ai, data, cloud, devops, web, mobile, security, product,
  design, startups, opensource, hardware, web3, quantum, languages, career`.
  Each comes from a keyword table. An event may have none.

### 6.4 Location precision

- `exact`: Eventbrite venue coordinates inside the bbox. These are the only
  coordinates a source states for a venue it names.
- `area`:
  - A Luma event. Its `GEO` is drawn as an area ring, because Luma withholds
    the address until registration, so the point may be the venue's or only
    near it. The area comes from `area_for_point`.
  - A GDG venue, which has only a postal address. It is placed at the
    centroid of the area that `area_for_text(address)` finds, plus `jitter`.
- `none`: online, or nothing placeable. Such an event is not on the map.

*Claude's call (delegated 2026-10-15): events are never geocoded through
Nominatim.* Reason: this keeps Nominatim's 40-per-run budget for the company
map, and an event's area is enough to decide whether to go.

## 7. API

Authenticated, under `/api/v1/events`, in a new router `api/routers/events.py`.
The router is thin and delegates to `services/events/`.

| Endpoint | What |
|---|---|
| `GET /events` | Query: `when=today\|week\|weekend\|month` (the IST windows of `domain.event_time.window`; it overrides `from`/`to`), or `from, to` (ISO dates, IST; default today → +30 d), `mode=in_person\|online\|any` (default `in_person`), `topic`, `format`, `hide_paid`, `saved`, `company_id`, `q`, `limit≤100`, `offset`. Returns `{total, events: EventCard[], counts: {upcoming, this_week, going}, sources: [{source, last_read_at, status}]}`. Lists primaries only (`duplicate_of IS NULL`), plus the caller's own manual events. |
| `GET /events/{id}` | `EventDetail`: the card, plus `snippet` (≤ 200 shown), `reasons`, `also_on[{source,url}]`, `companies[{id,name,role}]`, `my: {status, note} \| null`, `people: EventContact[]`. |
| `PUT /events/{id}/save` | body `{status: "saved"\|"going", note?}`, an upsert. |
| `DELETE /events/{id}/save` | |
| `GET /events/{id}/ics` | `text/calendar`, `Content-Disposition: attachment; filename="<slug>.ics"`. |
| `POST /events/import` | body `{url}`. Returns `{event}`, or `{needs_details: true, reason}` (§4.4). 10 a day per user. |
| `POST /events/manual` | body `{title, starts_at, ends_at?, venue_name?, is_online, url?}`. Creates a private event and saves it as `going`. |
| `GET /events/{id}/people` | the caller's contacts at that event |
| `POST /events/{id}/people` | body `EventContactIn`. Allowed once the event has started. It creates a `going` save if none exists. |
| `PATCH /events/people/{pid}`, `DELETE /events/people/{pid}` | own rows only, else 404 |
| `GET /events/people-met?since=` | **The Outreach contract** (§8). |

`EventCard` = `{id, title, starts_at, ends_at, is_online, venue_name, area, lat, lng, geo_precision, is_free, organiser_name, format, topics, source, url, companies[{id,name}], my_status, is_cancelled}`.

Scheduled work, behind `require_cron_or_admin`:
`POST /api/v1/ingest/events/refresh?step=luma|gdg|eventbrite|relevance|link|prune&limit=N`
(§10).

## 8. People met → Outreach (the interface)

Outreach is built in parallel on its own branch, and its tables are unknown
here. So Events **publishes** a read-only contract and imports nothing from
Outreach.

```python
# services/events/met.py
@dataclass(frozen=True)
class MetAtEvent:
    source_ref: str          # "event_contact:<uuid>", Outreach's idempotency key
    name: str
    title: str | None
    company_name: str | None
    company_id: str | None   # a company on the map, when the typed name matched one
    linkedin_url: str | None
    x_url: str | None
    github_url: str | None
    email: str | None        # only what the person gave the user
    note: str | None
    event_id: str
    event_title: str
    event_url: str
    met_on: date             # the IST date of the event's start
    created_at: datetime

async def people_met(session: AsyncSession, user_id: str, *,
                     since: datetime | None = None, limit: int = 100) -> list[MetAtEvent]: ...
def context_line(met: MetAtEvent) -> str:   # "Met at <event title> on 16 Oct"
```

- `GET /api/v1/events/people-met?since=` returns the same fields as JSON.
- Outreach's touch for one of these is the channel "met at an event", with
  `occurred_at = met_on` and the context line above. Re-reading is safe:
  `source_ref` dedupes.
- **Frontend.** Each person row in the event detail has a "Start a thread"
  link to `/outreach?add=event_contact:<uuid>`. It renders only when
  `FEATURES.outreach` in `frontend/lib/features.ts` is true. The constant is
  false on this branch; the merge flips it.
- **At merge**, the controller wires Outreach's `?add=` handler to
  `people_met`. That is a merge step, not this branch's work.

*Claude's call (delegated 2026-10-15): a pull contract (Outreach reads
`people_met`), not a push into Outreach tables.* Reason: the branches are
parallel. A pull keeps Events self-contained and lets Outreach own its
shape.

## 9. Pages

### 9.1 Nav

- **Events**, with the lucide `CalendarDays` icon, after "Company intel" in
  `AppShell.tsx`'s `nav`.
- The label hides under 900 px like the others.

### 9.2 `/events` — `frontend/app/events/page.tsx`

- **Ledger strip** (`.page-heading.split-heading`):
  - context line: "Events — Bengaluru"
  - h1: "Where to meet people this week" with the amber full stop
  - subhead: "Tech meetups, talks and demo nights from Luma, GDG and
    Eventbrite. We never register for you."
  - right side: `.tracker-total` with the "upcoming" count in `FlapText`
  - under the heading, a mono ledger line: "41 upcoming · 9 this week ·
    2 you're going to · Luma read 05:31 · GDG 05:32 · Eventbrite off"
- **Toolbar**, on the `.ci-searchbar` pattern:
  - search
  - When: Today · This week · This weekend · Next 30 days
  - Topic and Format selects
  - toggles "Online too" (off by default) and "Hide paid"
  - a **List | Map** switch (`usePersistentState`, default List)
  - "Saved" as a `.status-tabs`-style rail: All · Saved · Going
  - the "Add by link" input, to the right on desktop and in the sheet on
    mobile
- **List.**
  - Day headers in Fragment Mono ("THU 15 OCT · TONIGHT"), each followed by
    hairline rows (`.ev-row`). A row has:
    - the IST time in mono ("18:30");
    - the title, then organiser and area ("Team Lossfunk · Indiranagar");
    - codes: format, first topic, `Free` when `is_free`, a `Cancelled` code
      in `--danger` text;
    - a save toggle (bookmark icon), with `aria-pressed`.
  - **Lamp** (One Lamp Rule): amber for an event the user is *going* to
    that is today or tomorrow, which needs them. Faint for saved. None
    otherwise.
  - The row selected for the drawer lights the amber left edge
    (`inset 3px 0 0 var(--accent)`), as `.ci-card` does.
  - Hover slides 10 px right (DESIGN.md row gesture).
- **Map** (optional layer):
  - `EventMap.tsx` reuses the company map's MapLibre setup. The worker URL,
    `graphite.json`, the bbox, token-read colours and the WebGL check are
    extracted once into `frontend/lib/maplibre.ts`, and `CompanyMap.tsx`
    imports from there unchanged in behaviour.
  - Pins: steel dots for `exact`, hollow rings for `area`. Amber only for
    the selected event and for events the user is going to.
  - Online events are not drawn. A chip reads "12 online — see List".
  - If the map fails, the page falls back to List with the one-line note.
- **Detail** is the `.ci-drawer` pattern, opened by `?event=<id>` (deep links
  from the Overview and the company drawer). It carries `.cm-over` (with its
  `.ci-backdrop.cm-over`), so it sits above the sticky nav. Full width under 800 px. Escape
  closes it and focus returns to the row. In order:
  1. **Head**:
     - the title;
     - when: "Thu 15 Oct · 18:30–20:00 IST";
     - where: venue and area with a precision note ("Area only — Luma shows
       the address after you register"), or "Online";
     - the codes.
  2. **Actions**:
     - **Register on Luma ↗**, the primary button. It is an external link
       with `rel="noopener"`, and the text names the source.
     - **Going** and **Save** toggles.
     - **Add to calendar (.ics)**.
  3. **Why it's listed**: the `reasons`, joined by " · ".
  4. **Hosted by**: organiser, hosts, and linked companies, each opening
     `/company-intel?company=<id>`.
  5. **About**: the snippet, ≤ 200 chars, with "more on Luma ↗".
  6. **Also on**: duplicates' links.
  7. **Your note** (once saved).
  8. **People you met** (once the event has started):
     - the list;
     - an add form: name, company, title, LinkedIn URL, note, and an email
       field labelled "only if they gave it to you";
     - per row, a LinkedIn people-search deep link
       (`domain.linkedin_links.person_search_link`, built at read time) when
       no profile URL was typed;
     - the "Start a thread" link (§8).
- **Empty states**, each a plain line with at most one action:
  - Nothing on record: "No events on record yet. The next read is at 05:20
    IST." (the time is from the workflow)
  - Filters exclude everything: "Nothing matches these filters." with
    **Clear filters**.
  - Saved, with none: "You haven't saved an event. Save one from the list
    and it shows up on your Overview the day it happens."
  - People met, with none: "Who did you meet? Add them while names are
    fresh."
  - A source down: its ledger entry reads "GDG unavailable", and the rest
    still list.
- **375 px.**
  - The ledger strip stacks.
  - The toolbar collapses to search plus a **Filters (n)** button opening
    the `.cm-sheet` bottom sheet, which holds When, Topic, Format, the
    toggles and Add by link.
  - A row is a two-line grid (time | title on line 1, meta and codes on
    line 2), with the save toggle as a 44 px target.
  - The drawer is full width. The map is full height under the strip.
- **Keyboard**:
  - rows are links;
  - the save toggle is a `button`;
  - the filter sheet traps focus and closes on Escape;
  - focus moves into the drawer on open and back on close.

### 9.3 Company drawer — "Events" block

- A new `EventsSection` in `frontend/app/company-intel/page.tsx`, after
  News. It fetches `api.events.list({company_id, from: today, limit: 3})`.
- It shows **"Hosted by <name>"**: up to 3 upcoming events, each as a
  `.ci-job` row ("Fri 16 Oct · 15:00 · Swift Bengaluru × Okta"), linking to
  `/events?event=<id>`.
- *Claude's call (delegated 2026-10-15): the section is hidden when the
  company has no upcoming event.* Reason: most companies host none, and a
  "No events" line on every drawer is noise. The company-map rule of saying
  an empty section plainly is about sections every company has.

### 9.4 Overview day plan

Three copilot card kinds, from a new producer `_event_cards(user_id)` in
`services/copilot.py`. Each is dismissible through `agent_memory`.

| kind | when | id | tone | title / detail |
|---|---|---|---|---|
| `event_today` | going or saved, the IST start date is today, not ended, not cancelled | `event_today:<event_id>` | `primary` | "Tonight: <title>" / "18:30 at Indiranagar, hosted by Team Lossfunk. Add the people you meet afterwards." |
| `event_soon` | the soonest going or saved event in the next 7 days, not today | `event_soon:<event_id>` | `action` | "<Weekday>: <title>" / "You saved it — decide whether you're going." (saved) or "You're going. Add it to your calendar." (going) |
| `event_met_prompt` | going, ended within the last 2 days, no contacts noted | `event_met:<event_id>` | `action` | "Who did you meet at <title>?" / "Note them now; each one can become an Outreach thread." |

A cancelled event that the user saved produces an `event_today`-slot card
with tone `warning` instead: "<title> was cancelled by the organiser".

**Ranking** in `buildActions` (dashboard `page.tsx`), hardest constraint
first:
1. interview today
2. **`event_today`**
3. onboarding
4. the briefing's lead card
5. **`event_soon`**, then **`event_met_prompt`**
6. first mock / practise, study, apply

At most 3 actions, as today. Cards not promoted stay in "Also on your
radar" (the existing `usedCardIds` logic).

*Claude's call (delegated 2026-10-15): an event tonight ranks right after an
interview today and above "Finish your profile".* Reason: it is a dated
commitment that expires tonight; the profile does not.

## 10. Scheduling

- **Workflow** `.github/workflows/events.yml`:
  - cron `20 23 * * *` (04:50 IST) and `20 11 * * *` (16:50 IST);
  - the same CRON_TOKEN gate, keepalive wake and `refresh()` helper as
    `company-map.yml`;
  - steps in order: `luma 26`, `gdg 30`, `eventbrite 60`, `link 500`,
    `relevance 2000`, then `prune` once a day (morning run only);
  - `workflow_dispatch` takes a single step.
- **Endpoint** `POST /api/v1/ingest/events/refresh?step=&limit=`. It calls
  `services.events.refresh.run_step(step, limit)`, which:
  - never raises, and returns `{"step", …counts}` or `{"step", "error"}`;
  - caps `limit` per step (`MAX_LIMIT`);
  - commits per source.

| step | does | rate limit | breaker key |
|---|---|---|---|
| `luma` | Discover feed + curated calendars. Each feed is skipped if read < 11 h ago (cache key `tr:events:luma:<id>:read`). | 1 req/s, ≤ 26 feeds, 15 s timeout, 1 MB cap | `events:luma` |
| `gdg` | Chapter pages, then the detail pages of new or changed upcoming events (each at most once per 3 days). | 2 s between requests (Crawl-delay), ≤ 30 per run, 15 s timeout, 1 MB cap; any path under `/api/` or `/gql/` is refused by a guard | `events:gdg` |
| `eventbrite` | Organiser and venue lists through the API. Skipped without a token. | 1 req/s, ≤ 60 calls per run (well under the API's per-token hourly limit) | `events:eventbrite` |
| `link` | Re-runs company linking for upcoming events (the map grows daily). | DB only | — |
| `relevance` | Re-runs `assess` on upcoming stored rows. | DB only | — |
| `prune` | `prune_events` (§5.6). | DB only | — |
| `purge` | Admin only, `source=eventbrite`. Deletes stored Eventbrite rows nobody saved or noted, for when API access ends (§4.3). Not in the workflow. | DB only | — |

- **Source health.** Each fetch records into `SourceHealthService`. Three
  failures open the breaker for 15 min, and the step returns
  `"<source>: circuit open"`. A 200 response with zero events three runs
  running marks the source `degraded` (the quiet failure).
- **User-Agent** everywhere:
  `TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)`.
- **Curated lists** live in `data/events/sources.json`: Luma discover ids and
  calendars (`{id, name, trusted_tech, india}`), GDG chapter slugs, and
  Eventbrite organiser and venue ids. They are checked in and edited by
  hand, and each entry carries a `checked` date.
- `scripts/refresh_events.py --step all` runs the same steps locally.

## 11. Free-tier budget

| Resource | Use |
|---|---|
| Neon | < 1 MB of events, plus per-user saves and contacts |
| Render | no resident memory. Feeds ≤ 1 MB each, parsed one at a time. |
| Outbound | ≤ ~120 requests a day across the three sources |
| Groq / Tavily / Nominatim | none |
| Vercel | one route. The map layer reuses the already-split `maplibre-gl` chunk. |
| Eventbrite | a free personal token, optional |

## 12. Failure behaviour

- **A source is down or blocked**: its breaker opens, its ledger entry says
  so, and the last good rows stay listed until they pass.
- **No Eventbrite token**: that source is off, and Eventbrite links offer
  the manual form.
- **Map unavailable**: List, with a note.
- **A feed changes shape**: the contract test still passes on the old
  fixture. Production health marks the source `degraded` after three empty
  runs, and `make verify-sources` catches it live.
- **A pasted link we cannot resolve**: the manual form, never an error page.

## 13. Testing

- **Pure**:
  - `domain/ical.py`: unfolding, unescaping, `DTSTART` in UTC, `TZID` and
    floating forms, `DATE` values, `GEO`, a `STATUS:CANCELLED`, and
    generating an `.ics` that round-trips;
  - `event_time`: the IST helpers and the "tonight" and "this week"
    boundaries at 23:59 / 00:00 IST;
  - `event_relevance`: against every title in the recorded fixtures, plus a
    table of excluded titles;
  - `event_identity`: title normalising, `same_event` and company
    candidates;
  - `event_urls`: every accepted pattern, and hostile inputs (userinfo,
    ports, look-alike hosts, `javascript:`).
- **Parsers vs recorded fixtures**, replayed offline by
  `tests/test_event_source_contracts.py`:
  - `tests/fixtures/events/luma_discover_bengaluru.ics`
  - two `luma_calendar_*.ics`
  - `gdg_chapter_gdg-bangalore.html.gz`
  - two `gdg_event_*.html.gz`
  - `eventbrite_event.json`
  - `eventbrite_organizer_events.json`

  `scripts/capture_fixtures.py --events` records them.
- **Store** (Postgres): upsert idempotency, cross-source dedupe, company
  links (never creating a company), cancellation only from an explicit
  signal, and the retention rules.
- **API**: list filters and IST windows, save/going, `.ics` headers, import
  patterns and the daily cap, people CRUD isolation between users, the
  `people-met` contract, and cron auth.
- **Copilot**: the three card kinds and dismissal.
- **Frontend**: `tsc`, a build, then a browser check of the list, filters,
  drawer, save, `.ics` download, the people form, the company drawer block,
  the Overview card and 375 px.

## 14. Left to the builder

- The curated lists in `data/events/sources.json`: Luma calendar ids taken
  from each calendar's iCal button, GDG city chapters, and Eventbrite
  organiser and venue ids. The builder verifies each and records the
  `checked` date.
- Whether `GET /v3/organizers/{id}/events/` serves a non-member token. If it
  does not, the builder uses venues and records that.
- The exact `TECH_WORDS`, format and topic keyword tables, driven by the
  fixtures' titles.
- `ev-*` CSS, following the preview (`.superpowers/sdd/events-preview.html`).

## 15. Calls to check

The five calls that would change the most if Ajit disagreed, most important
first:

1. **Eventbrite only through its official API with a free personal token**:
   curated organisers and venues plus pasted links. No reading of `/d/`
   pages or their JSON-LD, though robots.txt allows `/d/`, because
   Eventbrite's terms forbid scraping. This overrides the roadmap's §3.3
   note, and Eventbrite coverage will be far thinner than the 37-event
   search page.
2. **Luma only through its published iCal subscriptions**: the Bengaluru
   Discover feed plus curated organiser calendars, each at most every 12 h.
   Not the discover page's JSON, not the paid API. A pasted Luma link is
   never fetched. Descriptions are stored ≤ 600 chars and shown ≤ 200,
   always beside "Register on Luma ↗".
3. **GDG through public chapter and event pages, inside robots.txt**: no
   `/api/` or `/gql/`, 2 s between requests, at most 30 a run, under the
   Google Terms clause that ties automated access to robots.txt. "GDG on
   Campus" chapters are excluded.
4. **An event tonight ranks second in the day plan**: after an interview
   today and above "Finish your profile". An event this week, and the
   "who did you meet" prompt, rank after the briefing's lead.
5. **The relevance defaults**: in-person Bengaluru by default, online-India
   behind an "Online too" toggle. Online events are listed only as
   hackathons, workshops or conferences. A hiring event is listed only when
   a tech company on the map hosts it.
