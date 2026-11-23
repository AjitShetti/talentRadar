# Events coverage, entry, topics and cities: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the Bengaluru events the cron reads, add Hasgeek by link, and show entry (free, paid or price), topic chips with counts, a Register link on every row, and a city picker with the other cities as "Coming soon".

**Architecture:** This extends the existing Events feature. Sources stay curated in `data/events/sources.json`. Price and an external registration URL go in `events.metadata`, so no migration is needed. The topic facet is counted in `services/events/read.py`. The page changes are confined to `frontend/app/events/page.tsx` plus two small pure modules.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest (SQLite), Next.js 14, Playwright rules specs.

**Spec:** `docs/superpowers/specs/2026-11-23-events-coverage-design.md`

## Global Constraints
- Bengaluru only. The relevance rules (`domain/event_relevance.py`) are not changed.
- No images, no LLM call, and no request a source's terms or robots.txt forbid. A pasted link is never fetched as given.
- `price` is at most 40 characters. `register_url` is https, at most 512 characters, and never fetched.
- The topic facet counts at most 2,000 rows.
- Cities: Bengaluru is live. Mumbai, Delhi NCR, Hyderabad, Pune and Chennai are "coming soon".
- Python: mypy strict, ruff. Run with `/d/projects/talentRadar/.venv/Scripts/python.exe`.

## Review Focus
- A Hasgeek link with extra path (`/schedule`, `/sub/…`) or a reserved first segment (`/account/x/`) is not recognised, and nothing is requested. (Task 2)
- A Hasgeek schedule with no `VEVENT` returns `not_readable`, not a 500. (Task 2)
- A GDG `offers.url` on `gdg.community.dev`, on http, or longer than 512 characters is not stored as `register_url`. (Task 3)
- A later sighting without a price keeps the stored price. (Task 3)
- An event with neither `url` nor `register_url` (manual, no link) shows no Register link. (Task 5)

---

### Task 1: More Bengaluru sources

**Files:** Modify `data/events/sources.json`, `DEPLOY.md`. Test: `tests/test_event_sources.py` (already validates the file).

- [ ] Add Luma calendar ids and GDG chapter slugs, each vetted by hand. A Luma id comes from the calendar's "Add iCal Subscription" link, and the calendar must be Bengaluru tech. A GDG chapter page must show Bengaluru events. Set `checked: "2026-11-23"`. The total stays at 26 Luma feeds or fewer.
- [ ] Add a short "Eventbrite token" note to `DEPLOY.md`: create a free personal OAuth token in Eventbrite's account settings, then set `EVENTBRITE_TOKEN` on Render.
- [ ] Run `pytest tests/test_event_sources.py tests/test_event_workflow.py -q`. Expected: pass.
- [ ] Commit `feat(events): more Bengaluru Luma calendars and GDG chapters`.

### Task 2: Hasgeek by link

**Files:** Modify `domain/event_urls.py`, `domain/ical.py`, `services/events/user.py`, `api/schemas/event_schemas.py` (source literals/descriptions), `storage/models.py` (column comment only). Create `services/events/hasgeek.py` and `tests/fixtures/events/hasgeek_schedule.ics` (the recorded Women in Systems schedule). Test: `tests/test_event_hasgeek.py`.

**Interfaces (produces):**
- `EventSource = Literal["eventbrite", "gdg", "luma", "hasgeek"]`. `parse_event_url("https://hasgeek.com/org/proj/")` returns `PastedEvent("hasgeek", "org/proj")`.
- `domain.ical.calendar_property(data: bytes, name: str) -> str | None` returns the first unfolded, unescaped value of a property outside any `VEVENT`.
- `services.events.hasgeek`:
  - `schedule_url(key: str) -> str`, raising `ValueError` on a bad key;
  - `async fetch_schedule(client, key) -> bytes`, through `get_bounded` with `follow_redirects=False`;
  - `parse_schedule(data: bytes, key: str) -> EventFacts | None`;
  - `HASGEEK_KEY = "events:hasgeek"`.

- [ ] Write the tests:
  - `test_hasgeek_link_recognised`: trailing slash optional; `?x` is ignored.
  - `test_hasgeek_link_rejected`: `http://`, `www.hasgeek.com`, `/org/`, `/org/proj/schedule`, `/account/x/`, `/about/policy/`, and `/static/a/` all return `None`.
  - `test_parse_schedule_fixture`:
    - title `"Women in Systems"`;
    - `starts_at == 2026-11-28T04:30Z`, `ends_at == 2026-11-28T08:00Z`;
    - `locality == "Bengaluru"`, `organiser_name == "Bengaluru Systems Meetup"`;
    - `url == "https://hasgeek.com/bengalurusystemsmeetup/women-in-systems2/"`;
    - `source == "hasgeek"`, `external_id == "bengalurusystemsmeetup/women-in-systems2"`.
  - `test_parse_schedule_multi_session`: two VEVENTs. The start is the earliest, the end the latest.
  - `test_parse_schedule_empty`: no VEVENT returns `None`.
  - `test_import_hasgeek_link` (httpx `MockTransport`, as in `tests/test_event_user_api.py`): the event is stored and saved. A second import makes no request.
  - `test_import_hasgeek_empty_schedule`: the answer is `{"needs_details": True, "reason": "not_readable"}`.
- [ ] Run them and confirm they fail.
- [ ] Implement:
  - Reserved first segments: `account, login, logout, about, api, search, static, embed, new, auth`.
  - `parse_schedule` follows spec §4.2.
  - `_import_hasgeek` follows `_import_gdg` with scope `SourceScope("hasgeek", india=False, trusted_tech=True)` and the `HASGEEK_KEY` breaker.
- [ ] Run `pytest tests/test_event_hasgeek.py tests/test_event_user_api.py tests/test_event_schema.py -q`. Expected: pass.
- [ ] Commit `feat(events): add a Hasgeek event by link through its iCal schedule`.

### Task 3: Entry price and register URL

**Files:** Modify `domain/event_facts.py` (`price: str | None = None`, `register_url: str | None = None`), `services/events/gdg.py`, `services/events/eventbrite.py` (`EXPAND` gains `ticket_availability`), `services/events/store.py` (`_sanitise`, `_metadata`), `services/events/read.py` (`_card`), `api/schemas/event_schemas.py` (`price`, `register_url` on the card). Tests: `tests/test_event_gdg.py`, `tests/test_event_eventbrite.py`, `tests/test_event_store.py`.

**Interfaces (produces):** card JSON gains `price: string | null` and `register_url: string | null`.

- [ ] Write the tests:
  - `test_gdg_offers_price`: `{"price": "499", "priceCurrency": "INR"}` gives `price == "₹499"` and `is_free is False`.
  - `test_gdg_offers_free`: `price "0"` gives `is_free is True` and `price is None`. `isAccessibleForFree: true` also gives `is_free is True`.
  - `test_gdg_offers_register_url`: `https://konfhub.com/devfestblr26` is kept. A `gdg.community.dev` URL, an `http://` URL and a 600-character URL each give `None`.
  - `test_eventbrite_price_range`: min `₹500.00`, max `₹1,500.00` gives `"₹500.00–₹1,500.00"`. Equal values give one. `is_free` gives `None`.
  - `test_store_keeps_price_and_register_url`: a second sighting without them keeps both. They show in the card.
- [ ] Run them and confirm they fail.
- [ ] Implement:
  - A currency symbol is used for `INR` (₹) and `USD` ($). Any other currency shows as `"<CUR> <amount>"`.
  - The amount is shown as stated.
  - Values are flattened and bounded at 40 and 512 characters.
  - `_metadata` writes a key only when the facts state it.
- [ ] Run `pytest tests/test_event_gdg.py tests/test_event_eventbrite.py tests/test_event_store.py tests/test_event_read_api.py tests/test_event_source_contracts.py -q`. Expected: pass.
- [ ] Commit `feat(events): store an event's price and external registration page`.

### Task 4: Topic facet

**Files:** Modify `services/events/read.py` (`list_events` returns `topics`), `api/schemas/event_schemas.py`. Test: `tests/test_event_read_api.py`.

**Interfaces (produces):** the list JSON gains `topics: [{topic: str, count: int}]`.

- [ ] Write `test_list_topic_facet`. Three events tagged `["ai"]`, `["ai","web"]` and `["web"]`. With `topic=ai`, the facet is still `ai:2, web:2`, ordered by count and then by `TOPICS` order. The list holds 2 events.
- [ ] Run it and confirm it fails.
- [ ] Implement `_topic_facet(session, where_without_topic) -> list[dict[str, Any]]`. It runs `select(Event.topics).where(...).limit(2000)` and counts in Python. `_filters` gains `include_topic: bool = True`.
- [ ] Run `pytest tests/test_event_read_api.py -q`. Expected: pass.
- [ ] Commit `feat(events): topic counts for the list's filters`.

### Task 5: Page (entry, register, topic chips, cities)

**Files:**
- Create `frontend/lib/event-entry.ts`, exporting `entryLabel(e: {is_free: boolean|null, price?: string|null}): string` and `registerHref(e): string | null`.
- Create `frontend/lib/event-cities.ts`, exporting `EVENT_CITIES: {id: string, name: string, live: boolean}[]`.
- Modify `frontend/lib/api.ts` (types), `frontend/app/events/page.tsx` and the events CSS block in `frontend/app/globals.css`.
- Test: `frontend/e2e/event-entry.rules.spec.ts`.

- [ ] Write the rules tests:
  - `entryLabel`: free gives `Free`; a price gives that price; `is_free === false` gives `Paid`; null gives `Entry not stated`.
  - `registerHref`: `register_url` comes before `url`; neither gives `null`.
  - Cities: exactly one live city, `bengaluru`.
- [ ] Implement:
  - The row shows the entry code in place of the bare "Free", and a `Register ↗` link (`EXTERNAL_LINK_PROPS`).
  - The detail view shows the entry and a "Register on <Source> ↗" action, or "Register ↗" when the link is external.
  - Topic chips (`.ci-chip`, `aria-pressed`) under the toolbar, from `topics`.
  - The title gets a city select. The other cities are disabled options labelled "<City> · coming soon".
  - `SOURCE_LABEL` and `SOURCE_NAME` gain `hasgeek: 'Hasgeek'`.
- [ ] Run `npx playwright test e2e/event-entry.rules.spec.ts`, `npx tsc --noEmit` and `npm run lint` in `frontend/`. Expected: all pass.
- [ ] Commit `feat(events): entry, register link, topic chips and the city picker`.

### Task 6: Docs and checks
- [ ] Update `CLAUDE.md`'s Events section: Hasgeek by link, and price and register URL in metadata.
- [ ] Run `ruff check .`, `mypy` on the changed modules, and `pytest tests/test_event_*.py -q`. Expected: all pass.
- [ ] Verify the page in the browser preview.
- [ ] Commit `docs(events): Hasgeek links, entry and cities`.
