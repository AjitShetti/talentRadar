# Events — wider Bengaluru coverage, entry, topics, cities (design)

Status: approved by Ajit 2026-11-23 · one PR on top of `2026-10-15-events-design.md`

## 1. What this is for

Ajit wants Events to work like Eventbrite, but for tech events only. That means
every tech event in Bengaluru, sorted by topic, with what entry costs and a
link to register. Other cities come later, once Bengaluru has been tried and
reviewed ("collect only from Bengaluru, we'll test how it works and review,
then implement for other cities").

The scheduled refresh already exists (`.github/workflows/events.yml`, twice a
day). It reads Luma, GDG and Eventbrite, and it stores topics, free/paid and
the source page. This change widens what it reads, and shows what it already
holds more plainly.

## 2. Goals, non-goals, anti-goals

**Goals**
1. **More Bengaluru sources on the same cron.**
   - More curated Luma calendars and GDG chapters in `data/events/sources.json`.
   - Eventbrite starts working as soon as Ajit sets `EVENTBRITE_TOKEN`; the code already reads it.
2. **Hasgeek by link.** Pasting a `https://hasgeek.com/<org>/<project>/` link reads that
   project's published iCal schedule and adds the event.
3. **Entry.** Every event says Free, Paid or "Entry not stated". When a source
   states a price (Eventbrite's ticket range, a GDG `offers.price`), the card says it.
4. **Topics up front.** A row of topic chips above the list, each with a count
   for the current filters. One click narrows the list; the dropdown stays in the sheet.
5. **Register.** Each row shows a "Register ↗" link that opens the source's
   registration page. When GDG names an external registration page (`offers.url`,
   e.g. KonfHub for DevFest), that page is used.
6. **Cities.** A city picker. Bengaluru is active. Mumbai, Delhi NCR, Hyderabad,
   Pune and Chennai are listed, disabled, as "Coming soon".

**Non-goals**
- Collecting any city other than Bengaluru. The relevance rules stay Bengaluru-only.
- Images. Dropped by Ajit 2026-11-23 ("you can totally ignore it").
- KonfHub as a source. Its terms limit content to personal, non-commercial use.
- Hasgeek on the cron. Its terms forbid "spider, crawl, or scrape", and it
  publishes no list of upcoming events we can read. Only its per-project iCal,
  which is built for calendar apps, is used, and only for a link a user pasted.
- Meetup (unchanged: paid, or forbidden).

**Anti-goals** (unchanged from the Events design)
- No request a source's terms or robots.txt forbid. No LLM call. Nothing is registered for the user.
- A pasted link is never fetched as given. The request is built from the captured pieces.

## 3. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | Widen the existing permitted sources, add Hasgeek by link, surface entry, topics, register and cities | Coverage is limited to what sources publish through permitted interfaces | **Chosen** |
| B | Also crawl Hasgeek / KonfHub / Luma pages | More events, but breaks those sites' terms and risks the server being blocked | Rejected |
| C | Read a general web search (Tavily) for events | Unstructured, costs search credits, needs an LLM to parse | Rejected (no LLM in Events) |

## 4. Design

### 4.1 Sources (`data/events/sources.json`)
- Add Bengaluru tech organiser calendars on Luma. Each `cal-…` id is taken by hand from
  the calendar's "Add iCal Subscription" link, never scraped, with `checked` set to the
  date. `trusted_tech` is true only for developer-community calendars.
- Add GDG chapters based in Bengaluru (for example Women Techmakers Bengaluru, and
  campus chapters) after confirming each slug's chapter page lists events.
- The run limits stay where they are: 26 Luma feeds, 30 GDG requests and 60 Eventbrite calls a run.
  The list must stay within 26 Luma feeds.
- `DEPLOY.md` says how to create the free Eventbrite personal token and where to set it.

### 4.2 Hasgeek by link
- `domain/event_urls.py`: host `hasgeek.com` with the path pattern
  `/<org>/<project>/` (`[a-z0-9_-]{1,80}` each, excluding reserved first segments such as
  `account`, `login`, `about`, `api`, `search`, `static`, `embed`). The key is `"<org>/<project>"`.
- `services/events/hasgeek.py`:
  - `schedule_url(key)` builds `https://hasgeek.com/<org>/<project>/schedule/ical`.
  - `fetch_schedule` reads it through `get_bounded`, with no redirects.
  - `parse_schedule(data, key)` builds a single `EventFacts`:
    - title from the calendar `NAME`/`X-WR-CALNAME`, falling back to the first `SUMMARY`;
    - start is the earliest `DTSTART`, end the latest `DTEND`;
    - location from the first session's `LOCATION`, the text before ` - https://` (observed:
      `Bengaluru - https://hasgeek.com/…`), used as the locality; geo when a `GEO` is stated;
    - `organiser_name` from `ORGANIZER;CN=…`, falling back to the org segment;
    - `url` is the project page, `external_id` the key, `source="hasgeek"`.
  - A schedule with no `VEVENT` (a project that has not published one) is `not_readable`, and
    the user gets the manual form.
  - The session iCal carries no project-level calendar fields, so the parser reads them
    from the raw calendar text (`domain/ical.py` gains a small helper for calendar-level
    properties).
- `user.import_link` routes `hasgeek` to an importer modelled on `_import_gdg`. It gets its
  own breaker key, `events:hasgeek`, and is stored with scope
  `SourceScope(key="hasgeek", india=False, trusted_tech=True)`. Hasgeek is a tech-community
  platform; the relevance rules still decide *where*.
- The `source` value `hasgeek` is added to the API schema, the frontend `EventSource` type
  and its labels ("Register on Hasgeek ↗"). No migration is needed: `source` is `String(16)`.
- Hasgeek is not on the cron and has no `/events` source-status entry.

### 4.3 Entry and register link (no migration)
Both go in `events.metadata` (`extra_metadata`), which already carries per-source extras:
- `price`: a display string of at most 40 characters, such as `"₹500"` or `"₹500–₹1,500"`.
  - Eventbrite: add `ticket_availability` to `expand`, then read
    `minimum_ticket_price.display` and `maximum_ticket_price.display`. Show a range when
    they differ. A free event has no price.
  - GDG: read JSON-LD `offers` (an object or a list). A numeric `price` above 0 with a
    `priceCurrency` gives the price; `price == 0` or `isAccessibleForFree: true` sets `is_free`.
- `register_url`: the GDG JSON-LD `offers.url` when it is https, no longer than 512
  characters, and on a different host from the GDG page. It is never fetched.
- A later sighting that does not state a value keeps the stored one, as with every other field.
- The card and the detail view gain `price` and `register_url`. The frontend's entry
  label is `Free` (`is_free`), the price, `Paid` (`is_free === false`), or `Entry not stated`.

### 4.4 Topic facet
- `list_events` returns `topics: [{topic, count}]`. Counts use the current filters except
  the topic filter, and are counted in Python over the matching rows' `topics`, bounded at
  2,000 rows (an upcoming Bengaluru list is far smaller). Only topics with a count above 0
  are returned, sorted by count, then by vocabulary order.
- The page shows the topic chips under the toolbar: "All", then each topic with its count.
  Pressing a chip sets the existing `topic` filter; pressing it again clears it.

### 4.5 Register on each row
The list row gains a `Register ↗` link (`register_url ?? url`, with `EXTERNAL_LINK_PROPS`).
A user-added event without a link shows none. The detail view's primary action reads
"Register on <Source> ↗", or "Register ↗" when the source's registration page is external.

### 4.6 Cities
`frontend/lib/event-cities.ts` lists the cities. Only `bengaluru` is `live`. The page title
becomes a city select (`Events — Bengaluru`). The other options are disabled and read
"<City> · coming soon". Nothing is stored or requested per city. The backend is unchanged:
it is Bengaluru by construction.

## 5. Testing
- Pure: `event_urls` Hasgeek patterns (accepts, plus rejects for reserved segments, extra
  path, other hosts); the Hasgeek schedule parser over a recorded fixture
  (`tests/fixtures/events/hasgeek_schedule.ics`); GDG `offers` parsing (price, free,
  register URL rules); Eventbrite price parsing.
- Service: `import_link` with a Hasgeek link (mocked transport); the store keeps `price`
  and `register_url` across sightings; the list returns the topic facet.
- `tests/test_event_sources*` still validates the larger `sources.json`.
- Frontend: rule tests for the entry label and city list; `tsc` and lint.

## 6. Left to the builder
- Which Luma calendars and GDG chapters are added (each vetted by hand, Bengaluru tech).
- Exact chip styling, within the page's existing `.ci-chip` and `.ev-code` vocabulary.
