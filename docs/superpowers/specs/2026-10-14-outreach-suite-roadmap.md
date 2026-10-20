# Outreach suite — roadmap and reverse-engineering notes

Status: roadmap approved 2026-10-14 · each phase gets its own design doc

## 1. Why

Applying through job portals mostly fails: listings are flooded with
AI-generated applications and screened by filters. People get hired through
other routes: cold email to a founder, a contribution to the company's open
source, a conversation with someone inside, a referral. TalentRadar already
leans this way (Role Dossier "way in", referral drafts, sourced contacts).
This suite makes outreach the main path:

1. **Company map** — every employer in Bengaluru on a map, with founders,
   people and a one-line summary. Replaces the Company Intel directory.
2. **Outreach-first job search** — who to reach, through which channel, and
   a tracker for the conversation. Founders, employees, maintainers, and
   companies that just raised.
3. **Networking events** — where to meet those people in person.
4. **Daily practice** — LeetCode daily questions plus 24–48 hour take-homes
   with an LLM-judged leaderboard, so users always have something to build.

Order: 1 (foundation PR, then map UI PR) → 2 → 3. Phase 4 is independent and
may run in parallel.

## 2. Decisions made by Ajit (2026-10-14)

| Topic | Decision |
|---|---|
| LinkedIn | Deep links that open in the user's own LinkedIn session, plus public sources. **Never scrape LinkedIn.** |
| DSA questions | **Do not author problems.** Import LeetCode question metadata and send users to leetcode.com to solve them. |
| Take-homes | A **24–48 hour** window, not a week. People will use AI to build them, so the judge scores *how they built it and their thought process*, not the code alone. |
| Events | Eventbrite **plus** Luma and Google Developer Group community events. |
| Order | Accepted as above. |

Standing constraints: zero cost (free tiers only); nothing sent on the user's
behalf; no contact is invented or guessed (`services/company_contacts.py`);
India-only.

## 3. What the reference products actually do

Observed 2026-10-14 in a browser, from network traffic and the data embedded
in each page. These notes record behaviour. They are not data to copy.

### 3.1 bangalorestartupmap.com

- A Next.js App Router page. The **whole dataset is server-rendered into the
  page** as props of the map component (~1.1 MB of RSC payload). There is no
  search API; every filter runs in the browser.
- 1,085 entries: 1,011 `startup`, 74 `vc`. Record shape: `name, slug, kind,
  tagline, description, stage, sector, tags[], area, hsr_location (address
  text), lat, lng, logo, website, founders (string), founder_links[{name,url}],
  founded_year`.
- Coverage is thinner than it looks:
  - 699 entries have coordinates.
  - 531 name founders.
  - 182 have founder links. 78 are LinkedIn profiles and 99 are just
    `google.com/search?q=<Name>+<Company>+LinkedIn`.
  - No entry has an X link.
- Map: Leaflet, CARTO raster tiles (a key is shipped in the props), clustered
  markers. Map/Grid toggle. Filters: type (startups/VCs), 17 areas + Other,
  stage (Pre-seed … Series C+, Public, Acquired, Bootstrapped), 10 sectors.
- Jobs are **counts, not postings**: `roles[company_slug] = [{track,
  seniority, fresher_friendly, posted_day}]`, 4,338 roles across 131 companies.
- News strip: 15 items from Inc42, ET Tech, YourStory, Entrackr, Mint and
  Business Standard. Each is tagged `funding | launch | other` and linked by
  `company_slug`.
- Each company has a page at `/company/{slug}` (description, tags, area,
  street address, fund size for VCs, partners or founders with links). There
  are SEO pages per area (`/startups/{area}`), plus `/news` and a `/submit`
  form.

### 3.2 LeetCode and HackerRank

- LeetCode GraphQL (`/graphql/`) answers these **without auth from a server**,
  verified with curl:
  - `activeDailyCodingChallengeQuestion` returns one question per UTC date,
    the same for every user. 2026-10-14 was "Maximize Sum of Squares of
    Digits", Medium, `math|greedy`.
  - `dailyCodingChallengeV2(year, month)` returns that month's calendar.
  - `questionList` returns the problem set metadata: 4,082 questions with
    slug, difficulty, tags and `isPaidOnly`.
  - `recentAcSubmissionList(username)` returns a user's public accepted
    submissions.
- The daily question is picked by hand and is not personalised. September 2026
  had 8 Easy / 14 Medium / 8 Hard, often on the same topic two days running.
- The weekly contest runs Sundays 02:30 UTC (08:00 IST): 90 minutes, 4
  problems.
- HackerRank: practice tracks (`/rest/contests/master/tracks`) plus
  **Orchestrate**, a roughly monthly 24-hour hackathon to "design, build and
  ship an agent". Sponsored company challenges run alongside it. Orchestrate
  is the model for our take-homes.

### 3.3 Eventbrite (`/d/india--bangalore/networking/`)

- The page is server-rendered. Results live in
  `window.__SERVER_DATA__.search_data.events.results` (20 per page) and are
  repeated as a schema.org `ItemList` of `Event` JSON-LD.
- Page 2 onward calls an internal `POST /api/v3/destination/search/` (CSRF
  token), then `GET /api/v3/destination/events/?event_ids=…&expand=…`.
- Each event carries three taxonomies:
  - `EventbriteCategory/101` Business & Professional, `/102` Science &
    Technology, …
  - `EventbriteSubCategory/1001` Startups & Small Business, `/1010` Career,
    `/1011` Investment, `/2004` High Tech, …
  - `EventbriteFormat/10` Meeting or Networking Event.

  Plus free-text `OrganizerTag/*` (Hiring, Womenintech, …). Each event also
  has `primary_venue.address` with lat/lng, `is_online_event`,
  `is_cancelled`, start/end in `Asia/Kolkata`, and `tickets_url`.
- There were 37 networking events for Bangalore at the time of observation.
  Related slugs: `business--networking`, `startup`, `tech-conferences`,
  `hackathon-events`, `job-fairs`, `artificial-intelligence`, `tech-events`,
  `meetup`.
- Eventbrite's terms forbid scraping. Read only public pages, on a schedule,
  at a low fixed rate, behind a circuit breaker.

### 3.4 Data sources checked for phase 1

| Source | Result |
|---|---|
| Wikidata SPARQL, `P159` HQ = Bengaluru (`Q1355`) | 594 organisations, 453 with a website, 125 with a company X handle, 101 with founders, 21 with headcount. Founder/CEO people: 118, of whom 21 have an X handle and 26 a LinkedIn ID. |
| OSM Overpass, `office=company\|it` + `name` + `website`, Bengaluru bbox | ~349 objects |
| OpenFreeMap styles | `liberty`, `positron`, `bright`, `dark`, `fiord` all served |
| RSS | Inc42 `/feed/` ✓, YourStory `/feed` ✓, ET Tech `rssfeeds/13357270.cms` ✓, Mint `/rss/companies` ✓, Entrackr `/feed/` ✗ (404) |

## 4. Phases

| Phase | Design doc | Status |
|---|---|---|
| 1. Company map (foundation + map UI) | `2026-10-14-company-map-design.md` | in design |
| 2. Outreach-first job search | `2026-10-15-outreach-design.md` | in build |
| 3. Networking events | to write | — |
| 4. Daily practice + take-homes | to write | — |
