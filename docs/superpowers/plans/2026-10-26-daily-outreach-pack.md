# Daily Outreach Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each IST day, hand every user who opens TalentRadar a pack of 3–5 matched companies, up to 2 people each, and a ready cold email or LinkedIn message per person, so the user only reviews, sends and applies.

**Architecture:** A scheduled discovery path stores published email addresses (company sites, GitHub) with provenance. Pure rules in `domain/` learn a company's address format, pick the pack and choose channels; `services/outreach/pack.py` builds the pack on the day's first `GET /outreach/pack` from the facts `suggest.py` already gathers, stores it in two new tables, and writes messages only from POSTs (one model call per company) under the existing and one new budget. Acting on an item reuses threads, people, touches, dismissals and applications.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, pytest (SQLite via `tests/outreach_support.py`), Groq via `services.llm._chat`, Next.js 14 App Router, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-daily-outreach-pack-design.md` — read it before any task.

## Global Constraints

- Pack size: at most 5 companies (`PACK_MAX_COMPANIES = 5`); at most 2 recipients per company (`PACK_MAX_PEOPLE = 2`); show fewer rather than pad; an empty pack is valid.
- Repeat rule: a company **seen** (`seen_at` set) in this user's pack in the last 14 days and left untouched is not picked (`PACK_REPEAT_DAYS = 14`). Skip-all dismisses for 30 days via `suggest.dismiss_suggestion`. A company with a `job_applications` row created by the user in the last 30 days is not picked (`PACK_APPLIED_DAYS = 30`).
- Day = IST day (`domain.outreach.ist_today`). 00:10 IST is a new day; UTC midnight is not.
- Budgets: shared `DAILY_OUTREACH_DRAFT_BUDGET = 150` (one unit per model call); per user `PER_USER_DAILY_DRAFTS = 20` (one per message); new per-user share of the shared pool `PER_USER_SHARED_DRAFTS = 8`, key `tr:budget:outreach-shared:<user_id>:<IST date>`. Claim order: user cap → user shared share → shared pool. Over any cap, or model failure, or unreadable reply → `fallback_draft` template, `generated=false`, `reason` set.
- No GET calls a model or reaches the web. Only POST `/outreach/pack/companies/{id}/write`, POST `/outreach/pack/items/{id}/write` and the existing POST `/outreach/threads/{id}/draft` call the model.
- A guessed address is never stored anywhere (contacts, outreach people, pack items, touches). Payload: `basis: "guessed"`, `learned_from: <found address>`, label "likely, unverified". Found: `basis: "found"`, `source_host`.
- No personal phone numbers anywhere.
- Another user's pack, item, thread or person → 404. Logs: ids and counts only, never an address or message text.
- Array columns are `TextArray` (`ARRAY(Text)`), never `StringArray`.
- mypy strict, ruff; routers validation + delegation only.
- Frontend uses the redesign's tokens/controls (`.primary-button`, `.outline-button`, `--r-*`), external links use `EXTERNAL_LINK_PROPS`.

## Review Focus

1. **Two people with the same name pattern at a company where the only found address is a hiring inbox** — no format is learned; no guess shown (Task 1 test `test_inbox_never_teaches`).
2. **A user opens Overview at 23:55 IST and /outreach at 00:05 IST** — two different packs, the second built fresh; the first's unseen companies don't trigger the repeat rule (Task 7 test `test_pack_rolls_over_at_ist_midnight_and_unseen_does_not_count`).
3. **The write POST fired twice concurrently for one company** (double click, two tabs) — spends at most one shared unit and the stored messages are the first writer's (Task 8 test `test_concurrent_write_spends_once`; take a row lock on the company's items).
4. **"I sent it" when the user already has 100 open threads** — 409 with the existing message, item stays `new`, nothing half-written (Task 9 test `test_sent_at_thread_cap_is_409_and_item_unchanged`).
5. **A company's found address is on a different domain from the company (a Gmail on a GitHub profile, an agency's address on the site)** — not stored, not used to learn (Task 4 test `test_foreign_domain_email_not_stored`).

---

### Task 1: Address format rules

**Files:**
- Create: `domain/email_patterns.py`
- Test: `tests/test_email_patterns.py`

**Interfaces:**
- Produces:
  - `Pattern = Literal["first", "last", "first.last", "firstlast", "first_last", "f.last", "flast", "first.l", "firstl", "last.first", "lastf"]`
  - `name_parts(name: str) -> tuple[str, str] | None` — `(first, last)` lowercased ASCII letters after NFKD-folding accents and dropping hyphens/apostrophes; `None` for one-word names, initials-only (`"A. K."`), digits, or any non-Latin letter. Middle names ignored (first token, last token).
  - `learn(examples: Iterable[tuple[str, str]]) -> Pattern | None` — examples are `(full name, address)` at one domain.
  - `guess(pattern: Pattern, name: str, domain: str) -> str | None`.
  - `local_part_kind(local: str) -> Literal["hiring", "drop", "personal"]` — the hiring/drop prefix lists (builder's call per spec; at least `careers, jobs, hiring, talent, recruit*, hr` → hiring; `info, hello, contact, support, sales, press, privacy, noreply, no-reply, admin, team` → drop).

- [ ] **Step 1: Write the failing tests**

```python
def test_learns_first_dot_last():
    assert learn([("Priya Kumar", "priya.kumar@acme.com")]) == "first.last"

def test_one_word_name_teaches_nothing():
    assert learn([("Priya", "priya@acme.com")]) is None

def test_learns_flast_from_agreeing_examples():
    assert learn([("Priya Kumar", "pkumar@acme.com"), ("Ravi Shah", "rshah@acme.com")]) == "flast"

def test_example_fitting_two_patterns_teaches_nothing_alone():
    # first == last, so "ann@" is both `first` and `last`.
    assert learn([("Ann Ann", "ann@acme.com")]) is None

def test_ambiguous_example_resolved_by_an_unambiguous_one():
    assert learn([("Ann Ann", "ann@acme.com"), ("Ravi Shah", "ravi@acme.com")]) == "first"


def test_conflicting_examples_learn_nothing():
    assert learn([("Priya Kumar", "priya.kumar@acme.com"), ("Ravi Shah", "rshah@acme.com")]) is None

def test_inbox_never_teaches():
    assert learn([("Careers", "careers@acme.com"), ("Priya", "priya@acme.com")]) is None

def test_unexplained_example_is_ignored_not_fatal():
    assert learn([("Priya Kumar", "priya.kumar@acme.com"), ("Ravi Shah", "rs1987@acme.com")]) == "first.last"

def test_guess_applies_pattern():
    assert guess("f.last", "Ravi Shah", "acme.com") == "r.shah@acme.com"

def test_guess_folds_accents_and_hyphens():
    assert guess("first.last", "José Mar-Tin", "acme.com") == "jose.martin@acme.com"

def test_guess_refuses_one_word_initials_and_non_latin():
    assert guess("first.last", "Ravi", "acme.com") is None
    assert guess("first.last", "A. K.", "acme.com") is None
    assert guess("first.last", "रवि शाह", "acme.com") is None

def test_local_part_kinds():
    assert local_part_kind("careers") == "hiring"
    assert local_part_kind("recruitment") == "hiring"
    assert local_part_kind("hello") == "drop"
    assert local_part_kind("priya.kumar") == "personal"
```

Rule for `learn`: for each example whose local part is `personal`, compute the set of patterns that reproduce it; skip examples matching none. Learned only if the intersection over all explained examples is exactly one pattern **and** at least one explained example matched exactly one pattern.

- [ ] **Step 2: Run** `pytest tests/test_email_patterns.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the four functions in `domain/email_patterns.py` (pure; no I/O).
- [ ] **Step 4: Run** `pytest tests/test_email_patterns.py -v` and `mypy domain/email_patterns.py` — Expected: PASS, no errors.
- [ ] **Step 5: Commit** `feat(outreach): learn and apply a company's email address format`

---

### Task 2: Schema — provenance column and pack tables

**Files:**
- Create: `storage/migrations/versions/020_outreach_pack.py` (revision `020_outreach_pack`, down_revision `019_practice`)
- Modify: `storage/models.py` (`CompanyContact`; new `OutreachPack`, `OutreachPackItem`)
- Test: `tests/test_outreach_pack_schema.py` (model the file on `tests/test_outreach_schema.py`)

**Interfaces:**
- Produces:
  - `CompanyContact.email_source_url: Mapped[str | None]` (String 512).
  - `OutreachPack`: `id` uuid pk, `user_id` FK users CASCADE, `day: date`, `created_at`; unique (`user_id`, `day`); relationship `items` ordered by `position`.
  - `OutreachPackItem`: `id`, `pack_id` FK CASCADE, `kind` String(12) `first|follow_up`, `company_id` FK companies SET NULL, `company_name` String(256), `position` int, `contact_id` FK company_contacts SET NULL, `outreach_person_id` FK outreach_people SET NULL, `person_name` String(128), `person_title` String(128) null, `person_kind` String(24) null, `channel` String(12) `email|linkedin`, `format` String(24), `subject` String(80) null, `message` Text null, `generated` bool null, `reason` String(24) null, `status` String(12) default `new` (`new|sent|skipped`), `thread_id` FK outreach_threads SET NULL, `reasons` `TextArray` default `{}`, `seen_at`, `applied_at`, `acted_at` timestamptz null, `created_at`. Index (`pack_id`, `position`); index (`company_id`).

- [ ] **Step 1: Write the failing tests** — `test_migration_020_chain` (down_revision is `019_practice`, single head); `test_pack_unique_per_user_day` (second insert same user+day raises `IntegrityError`); `test_items_cascade_with_pack`; `test_thread_delete_sets_item_thread_null`; `test_contact_has_email_source_url`; `test_drift_pack_tables_postgres_types` (compile each column's type with `postgresql.dialect()` from the migration's `op.create_table` calls vs the model; `reasons` is `TEXT[]`).
- [ ] **Step 2: Run** `pytest tests/test_outreach_pack_schema.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the migration (`op.add_column` on `company_contacts`; two `op.create_table`; downgrade drops both and the column) and the models.
- [ ] **Step 4: Run** `pytest tests/test_outreach_pack_schema.py tests/test_outreach_schema.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): migration 020 for the daily pack and email provenance`

---

### Task 3: Read published addresses (pure extraction)

**Files:**
- Modify: `services/company_map/types.py` (`PersonCandidate`, `SiteFacts`), `services/company_map/website.py` (`extract_site_facts`, `_people`), `services/company_map/people.py` (`parse_github_user`)
- Test: `tests/test_site_emails.py`, fixtures `tests/fixtures/company_sites/team_with_emails.html`, `contact_with_inboxes.html` (fictional names and domains only)

**Interfaces:**
- Produces:
  - `PersonCandidate.email: str | None = None`, `PersonCandidate.email_source_url: str | None = None`.
  - `SiteEmail` dataclass in `types.py`: `address: str`, `source_url: str` — a hiring inbox.
  - `SiteFacts.emails: tuple[SiteEmail, ...] = ()` — hiring inboxes only. An unattributed personal address (no person card around it) is dropped: without a name it can't teach a format, and it isn't a recipient.
  - Person cards: an address in the same card `_people` reads becomes that `PersonCandidate.email`, with `email_source_url` = the page URL.
  - `parse_github_user` sets `email`/`email_source_url` (`html_url`) from the payload's `email`.
- Consumes: `local_part_kind` (Task 1).

- [ ] **Step 1: Write the failing tests** — `test_mailto_in_person_card_attaches_to_person`; `test_visible_text_address_in_card_attaches`; `test_hiring_inbox_collected_as_hiring`; `test_drop_prefixes_ignored` (`hello@`, `noreply@` absent from `emails`); `test_obfuscated_at_not_parsed` (`priya [at] acme.com` ignored — no guessing from obfuscation); `test_github_user_email_read` (payload `{"login":"pk","email":"pk@acme.dev"}` → `email == "pk@acme.dev"`, `email_source_url == "https://github.com/pk"`); `test_github_user_without_email_is_none`. Domain filtering is **not** done here (Task 4 knows the company's domain).
- [ ] **Step 2: Run** `pytest tests/test_site_emails.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Address regex: standard `local@host.tld`, lowercase, ≤ 256 chars, from `mailto:` hrefs (strip `?query`) and text nodes. Extract before `_MENU_TAGS` are decomposed (footers carry inboxes).
- [ ] **Step 4: Run** `pytest tests/test_site_emails.py tests/test_company_map_website.py -v` (whichever existing website tests exist: `pytest -k website`) — Expected: PASS.
- [ ] **Step 5: Commit** `feat(company-map): read published addresses from sites and GitHub profiles`

---

### Task 4: Store discovered addresses on the schedule

**Files:**
- Modify: `services/company_map/people.py` (`upsert_people`), `services/company_map/store.py` or a new `services/company_map/inboxes.py` (`upsert_site_emails`), `services/company_map/website.py` (`_read_site` page visits), `services/company_map/refresh.py` (`_step_sites`, `due` ordering for `sites`)
- Test: `tests/test_company_emails_store.py`

**Interfaces:**
- Consumes: Task 2 column, Task 3 fields.
- Produces:
  - `email_domain_ok(address: str, company_domain: str | None) -> bool` in `domain/email_patterns.py` — true when the address host equals `normalize_domain(company_domain)` or is a subdomain of it.
  - `upsert_people(...)` additionally sets `email` + `email_source_url` when the candidate's address passes `email_domain_ok` and the row has no email (never overwrites).
  - `async def upsert_site_emails(session: AsyncSession, company: Company, emails: Iterable[SiteEmail]) -> int` — one `careers_inbox` curated contact per address passing `email_domain_ok` (`name=None`, `email`, `source_url` and `email_source_url` = page), deduped by address. Returns rows inserted.
  - `_read_site` additionally visits the first of `/careers`, `/jobs` and the first of `/team`, `/about` found in `facts.links` (same host, same `_Visit`, same robots/size/time limits), merging `people` and `emails`.
  - `_step_sites` calls `upsert_site_emails`; the `sites` due query orders listed tech companies that have named people and no stored email first.

- [ ] **Step 1: Write the failing tests** — `test_person_email_stored_with_provenance`; `test_foreign_domain_email_not_stored` (Gmail on a GitHub profile at company `acme.com` → `email is None`); `test_existing_email_never_overwritten`; `test_hiring_inbox_becomes_careers_inbox_contact_once`; `test_read_site_visits_careers_and_team_pages` (fake client records URLs; at most the homepage + contact + 2 extra pages); `test_sites_due_prefers_people_without_email`.
- [ ] **Step 2: Run** `pytest tests/test_company_emails_store.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the above.
- [ ] **Step 4: Run** `pytest tests/test_company_emails_store.py -k "company_map or people or website" -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(company-map): store published addresses with their source`

---

### Task 5: Channels say found or guessed

**Files:**
- Modify: `services/outreach/people.py` (`_build_channels`, `channels_for`, thread detail builder that calls them), `services/outreach/suggest.py` (`_top_person` call site)
- Test: `tests/test_outreach_channels_basis.py`

**Interfaces:**
- Consumes: `learn`, `guess`, `email_domain_ok` (Tasks 1, 4).
- Produces:
  - `async def company_address_format(session: AsyncSession, company_id: uuid.UUID) -> tuple[Pattern, str] | None` in `services/outreach/people.py` — learns from curated contacts at the company with a non-null `email_source_url` and an address passing `email_domain_ok` (named people give `(name, address)` examples; nameless rows such as hiring inboxes are skipped). Returns `(pattern, learned_from_address)`, `learned_from` = the first example that matched exactly one pattern.
  - `_build_channels(..., address_format: tuple[Pattern, str] | None = None, company_domain: str | None = None)` — email channel gains `basis: "found"` (stored address whose `email_source_url` or `source_url` host is set, or user-saved: `basis: "saved"`), else when no email and `address_format` and a guess exists: `{"channel": "email", "url": "mailto:<guess>", "label": <guess>, "direct": False, "basis": "guessed", "learned_from": <address>, "source_host": None}`. Every channel dict now has `basis` (`None` for non-email).
  - Email `source_host` prefers `email_source_url` over `source_url`.
  - `rank_people`'s `has_public_email` counts only `basis in {"found", "saved"}`.

- [ ] **Step 1: Write the failing tests** — `test_found_email_has_basis_found_and_host_from_email_source`; `test_guessed_email_when_format_learned`; `test_no_guess_without_format`; `test_no_guess_for_one_word_name`; `test_user_saved_email_basis_saved`; `test_guess_not_counted_as_public_email_in_ranking`; `test_thread_detail_shows_guess_and_stores_nothing` (load detail, then assert no `company_contacts`/`outreach_people` row has the guessed address).
- [ ] **Step 2: Run** `pytest tests/test_outreach_channels_basis.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_outreach_channels_basis.py tests/test_outreach_people.py tests/test_outreach_suggest.py -v` — Expected: PASS (update existing assertions only for the added `basis` key).
- [ ] **Step 5: Commit** `feat(outreach): label email channels found, saved or likely-unverified`

---

### Task 6: Pack selection rules

**Files:**
- Create: `domain/outreach_pack.py`
- Test: `tests/test_outreach_pack_domain.py`

**Interfaces:**
- Consumes: `Suggestion` from `domain/outreach_rank.py` (ranked companies, has `.company_id`, `.score`, `.reasons`), `RankedPerson`.
- Produces:
  - Constants from Global Constraints (`PACK_MAX_COMPANIES`, `PACK_MAX_PEOPLE`, `PACK_REPEAT_DAYS`, `PACK_APPLIED_DAYS`).
  - `@dataclass(frozen=True) class Recipient: contact_id: str; name: str | None; title: str | None; kind: str; channel: Literal["email","linkedin"]; format: str` (`email` or `linkedin_note`).
  - `@dataclass(frozen=True) class PackCompany: company_id: str; reasons: tuple[str, ...]; recipients: tuple[Recipient, ...]`.
  - `@dataclass(frozen=True) class PersonOption: person: RankedPerson; has_email: bool` (found/saved or guessable).
  - `choose_pack(ranked: Sequence[Suggestion], people: Mapping[str, Sequence[PersonOption]], inboxes: Mapping[str, str], excluded: AbstractSet[str]) -> list[PackCompany]` — `inboxes` maps company id → hiring inbox contact id; `excluded` = repeat-rule ∪ applied company ids (computed by the service).
  - `pick_recipients(people: Sequence[PersonOption], inbox_contact_id: str | None) -> tuple[Recipient, ...]`.

- [ ] **Step 1: Write the failing tests** — `test_takes_at_most_five_in_rank_order`; `test_fewer_than_three_is_not_padded`; `test_empty_is_valid`; `test_excluded_companies_skipped`; `test_company_without_person_or_inbox_skipped`; `test_two_people_prefer_different_kinds` (founder, founder, engineer → founder + engineer); `test_same_kind_allowed_when_only_kind`; `test_inbox_only_fills_when_fewer_than_two_named`; `test_channel_email_when_has_email_else_linkedin_note`.
- [ ] **Step 2: Run** `pytest tests/test_outreach_pack_domain.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** (pure).
- [ ] **Step 4: Run** the same — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): rules for choosing the daily pack`

---

### Task 7: Build and read the pack

**Files:**
- Create: `services/outreach/pack.py`
- Modify: `services/outreach/suggest.py` (expose the ranked signals and people without changing `suggestions()`), `api/routers/outreach.py`, `api/schemas/outreach_schemas.py` (or wherever outreach schemas live)
- Test: `tests/test_outreach_pack.py`

**Interfaces:**
- Consumes: Tasks 2, 5, 6.
- Produces:
  - In `suggest.py`: `async def ranked_for_pack(uid: uuid.UUID, now: datetime) -> tuple[list[Suggestion], dict[str, list[CompanyContact]], dict[str, CompanyContact]]` — the same batched reads `_compute` does (refactor `_compute` to call a shared `_gather_signals`), returning ranked suggestions (uncut by `CACHED_TOP`? cut at 25 is fine), people per company, and each company's curated `careers_inbox` contact with an email (one extra batched query).
  - `async def get_pack(user_id: str, *, now: datetime | None = None) -> dict[str, Any]` — builds today's pack if absent (unique `(user_id, day)`; on `IntegrityError` re-read), adds `follow_up` items for `touches.due_items` not already in the pack, returns the payload. **No model call, no network.**
  - `async def mark_seen(user_id: str, item_ids: list[str], *, now: datetime | None = None) -> None`.
  - Payload: `{"day": "YYYY-MM-DD", "companies": [{"company_id", "name", "summary", "reasons": [...], "roles": [{"job_id", "title"}], "applied": bool, "items": [Item]}], "follow_ups": [Item], "counts": {"companies", "messages_ready"}}`; `Item = {"id", "kind", "person": {"name","title","kind"}, "channel", "address": {"value","basis","source_host","learned_from"} | None, "linkedin_url", "format", "subject", "message", "generated", "reason", "status", "thread_id"}`. Address and LinkedIn link are computed at read time from the contact via Task 5 (`channels_for`/`_build_channels`); never from the item row.
  - Routes: `GET /api/v1/outreach/pack`, `POST /api/v1/outreach/pack/seen` body `{item_ids: list[str] (≤ 20)}` → 204.
  - Roles: the company's open Bengaluru jobs matching the user's target roles (reuse the `_role_counts` matching clause; ≤ 3 per company).

- [ ] **Step 1: Write the failing tests** — `test_first_get_builds_second_reads_same_pack`; `test_pack_rolls_over_at_ist_midnight_and_unseen_does_not_count` (Review Focus 2); `test_seen_untouched_company_not_repeated_within_14_days`; `test_seen_company_returns_after_14_days`; `test_applied_company_excluded_30_days`; `test_threaded_and_dismissed_companies_excluded`; `test_get_pack_never_calls_model` (patch `services.llm._chat` to raise); `test_follow_up_items_added_for_due_people`; `test_guessed_address_in_payload_not_in_item_row`; `test_other_users_items_cannot_be_marked_seen` (404 via router); `test_pack_route_requires_auth`.
- [ ] **Step 2: Run** `pytest tests/test_outreach_pack.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_outreach_pack.py tests/test_outreach_suggest.py tests/test_outreach_api.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): build and read the daily pack`

---

### Task 8: Write the messages

**Files:**
- Modify: `services/llm_budget.py`, `services/outreach/drafts.py`, `services/outreach/pack.py`, `api/routers/outreach.py`
- Test: `tests/test_outreach_pack_write.py`

**Interfaces:**
- Consumes: Task 7.
- Produces:
  - `PER_USER_SHARED_DRAFTS = 8`; `user_shared_key(user_id: str, now: datetime | None = None) -> str` (`tr:budget:outreach-shared:<user>:<IST date>`); `async def claim_user_shared(user_id: str) -> bool`.
  - `drafts.draft` (thread endpoint) claims user cap → `claim_user_shared` → shared pool, in that order; reason `daily_cap` for either user-level refusal.
  - `drafts.context_for_pack(item: OutreachPackItem, company: Company, profile row) -> DraftContext` (shares the field-building with `_gather`).
  - `drafts.build_pack_prompt(contexts: list[DraftContext]) -> tuple[str, str]` — one system + user prompt asking for JSON `{"messages": [{"i": 0, "subject": str|null, "message": str}, ...]}`, same fences and bounds as `build_prompt`, resume excerpt once.
  - `async def write_company(user_id: str, company_id: str, *, now: datetime | None = None) -> dict[str, Any]` in `pack.py` — today's `first` items for that company; if all have `message`, return them (spend nothing). Else: lock the items (`SELECT … FOR UPDATE`; SQLite ignores it — the test uses an `asyncio.Lock` per (user, company) as well), claim user cap once per message, then `claim_user_shared` once, then the shared pool once; one `_chat(..., json_mode=True, model=groq_fast_model, temperature=0.5, max_tokens=1600, reasoning_effort="low")`; parse with `_shape` per message (format limits); any message missing → template for that one; refusal/failure → templates for all, `reason` set. Store `subject`, `message`, `generated`, `reason` on the items. Returns `{"items": [Item]}`.
  - `async def write_follow_up(user_id: str, item_id: str, *, now: datetime | None = None) -> dict[str, Any]` — if the item has a message return it; else `drafts.draft(user_id, thread_id, person_id=…, fmt=…)` and store the result on the item.
  - Routes: `POST /api/v1/outreach/pack/companies/{company_id}/write`, `POST /api/v1/outreach/pack/items/{item_id}/write`.

- [ ] **Step 1: Write the failing tests** — `test_write_spends_one_shared_unit_and_one_user_draft_per_message`; `test_write_is_idempotent`; `test_concurrent_write_spends_once` (Review Focus 3: `asyncio.gather` two calls, `_chat` stub counts calls == 1); `test_user_shared_share_caps_at_8_then_templates`; `test_thread_draft_also_claims_user_shared_share`; `test_model_failure_gives_templates_with_reason`; `test_partial_reply_templates_only_missing_message`; `test_email_has_subject_linkedin_note_within_200_chars`; `test_follow_up_write_stored_and_reused`; `test_write_other_users_company_or_item_is_404`; `test_logs_contain_no_message_text` (caplog).
- [ ] **Step 2: Run** `pytest tests/test_outreach_pack_write.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_outreach_pack_write.py tests/test_outreach_drafts.py tests/test_outreach_drafts_domain.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): write the pack's messages in one call per company`

---

### Task 9: Sent, skip and applied

**Files:**
- Modify: `services/outreach/pack.py`, `api/routers/outreach.py`, schemas; possibly `services/applications.py` (extract a `create_application(user_id, job_id) -> JobApplication` service from the router's inline code, and make the router call it)
- Test: `tests/test_outreach_pack_actions.py`

**Interfaces:**
- Consumes: `threads.create_thread`, `people.add_person`, `touches.record_touch`, `suggest.dismiss_suggestion`, `threads._set_application`.
- Produces:
  - `async def mark_sent(user_id: str, item_id: str, *, message: str, subject: str | None, now: datetime | None = None) -> dict[str, Any]` — `first`: `create_thread(company_id=…, source="suggestion")`, `add_person(contact_id=…)` (no email passed — a guessed address must not land on the person; a found one already lives on the contact), `record_touch(direction="out", channel=item.channel, body=message (≤ 2000), person_id=…)`; `follow_up`: `record_touch` on the item's thread/person. Then `status="sent"`, `thread_id`, `acted_at`. Idempotent on `sent`. Any `Conflict` from the thread/people/touch caps propagates as 409 and the item stays `new` (do the item update last, in the same unit).
  - `async def mark_skipped(user_id: str, item_id: str, *, now: datetime | None = None) -> None` — when all `first` items of that company are `skipped`, call `dismiss_suggestion`.
  - `async def mark_applied(user_id: str, company_id: str, job_id: str, *, now: datetime | None = None) -> dict[str, Any]` — job must belong to the company; creates the application (409 if it exists is treated as success), sets `applied_at` on the company's items, links to the company's thread if one exists.
  - Routes: `POST /outreach/pack/items/{item_id}/sent` body `{message: str (1..2000), subject: str|null (≤ 80)}`; `POST /outreach/pack/items/{item_id}/skip` → 204; `POST /outreach/pack/companies/{company_id}/applied` body `{job_id}`.

- [ ] **Step 1: Write the failing tests** — `test_sent_creates_thread_person_touch_and_marks_item`; `test_sent_twice_is_one_touch`; `test_sent_to_guessed_address_stores_no_address` (scan `company_contacts`, `outreach_people`, `outreach_touches`, `outreach_pack_items` for the guess string); `test_sent_at_thread_cap_is_409_and_item_unchanged` (Review Focus 4); `test_follow_up_sent_records_follow_up_touch`; `test_skip_all_dismisses_company_30_days`; `test_skip_one_of_two_does_not_dismiss`; `test_applied_creates_application_and_links_thread`; `test_applied_job_of_other_company_is_404`; `test_actions_on_other_users_items_are_404`.
- [ ] **Step 2: Run** `pytest tests/test_outreach_pack_actions.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/test_outreach_pack_actions.py tests/test_outreach_threads.py tests/test_outreach_touches.py tests/test_api.py -k "application or outreach" -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): send, skip and apply from the daily pack`

---

### Task 10: Briefing card, retention and project rules

**Files:**
- Modify: `services/outreach/briefing.py`, `services/copilot.py`, `services/outreach/retention.py`, `services/job_retention.py`, `CLAUDE.md`
- Test: `tests/test_outreach_briefing.py`, `tests/test_outreach_pack_retention.py`

**Interfaces:**
- Produces:
  - `outreach_cards` returns `{"followup", "suggestion", "pack"}`; `pack` card kind `outreach_pack`, built from an **existing** pack only (reads `outreach_packs` for today; never builds one): title "Today's pack: N companies", detail "M messages ready", action `{label: "Review", href: "/outreach#today"}`, `meta: {"companies": N, "messages_ready": M}`. When a pack card exists the suggestion card is omitted.
  - `MODEL_VISIBLE_CARD_KINDS` unchanged; the copilot adds `outreach_pack_companies` and `outreach_pack_messages_ready` counts to the model-visible facts the same way `outreach_follow_ups_due` is added.
  - `async def prune_packs(session: AsyncSession, now: datetime | None = None) -> int` in `services/outreach/retention.py`: deletes packs whose `day` is older than 30 IST days; called from `job_retention` next to `prune_outreach`.
  - CLAUDE.md: Company map bullet "No guessed emails" and Outreach "Email appears only for…" rewritten to the spec §2 wording; add a short "Daily pack" paragraph under Outreach naming `services/outreach/pack.py`, `domain/outreach_pack.py`, `domain/email_patterns.py`, migration `020_outreach_pack`, the three budgets.

- [ ] **Step 1: Write the failing tests** — `test_pack_card_counts_only_and_no_names`; `test_pack_card_absent_without_todays_pack` (and assert no pack row was created); `test_suggestion_card_omitted_when_pack_card_present`; `test_copilot_model_facts_include_pack_counts_not_text`; `test_prune_packs_drops_older_than_30_days_keeps_recent`.
- [ ] **Step 2: Run** them — Expected: FAIL.
- [ ] **Step 3: Implement**, and edit CLAUDE.md.
- [ ] **Step 4: Run** `pytest tests/test_outreach_briefing.py tests/test_outreach_pack_retention.py tests/test_copilot*.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(outreach): daily pack in the briefing; prune old packs; document the rules`

---

### Task 11: The Today section and Overview

**Files:**
- Modify: `frontend/lib/api.ts`, `frontend/app/outreach/page.tsx`, `frontend/app/dashboard/page.tsx` (`buildActions`), `frontend/app/globals.css` (`.pack-*` styles)
- Create: `frontend/components/outreach/TodayPack.tsx`, `frontend/components/outreach/PackCompanyCard.tsx`, `frontend/lib/outreach-pack.ts` (pure helpers), `frontend/e2e/outreach-pack.spec.ts`

**Interfaces:**
- Consumes: Task 7–9 routes and payload.
- Produces:
  - `api.outreach.pack: { get(), seen(itemIds), writeCompany(companyId), writeItem(itemId), sent(itemId, {message, subject}), skip(itemId), applied(companyId, jobId) }` with types `OutreachPack`, `PackCompany`, `PackItem`, `PackAddress`.
  - `lib/outreach-pack.ts`: `mailtoHref(address: string, subject: string | null, body: string): string` (RFC 6068 encoding; `encodeURIComponent` per field, `%0D%0A` newlines); `addressBadge(a: PackAddress): { label: 'found' | 'likely, unverified' | 'saved'; hint: string }` (hint: `on <source_host>` or `format learned from <learned_from>`).
  - `TodayPack`: on mount `get()`, then `seen()` for displayed items, then `writeCompany` sequentially for companies with unwritten items (skeleton while pending), then `writeItem` for unwritten follow-ups. Per item: editable textarea (subject input for email), Copy, Open in email (`mailtoHref`) or Open LinkedIn (`EXTERNAL_LINK_PROPS`), I sent it (sends the edited text), Skip. Per company: reasons, roles with Apply (`/roles/{id}`) and I applied. Empty state copy: "Nothing new matched today. Add skills or target roles to widen it." Rendered at the top of `/outreach` with `id="today"`.
  - Overview `buildActions`: an `outreach_pack` card becomes the lead Outreach action ("Review today's pack", chips `N companies`, `M ready`, href `/outreach#today`), replacing the `outreach_suggestion` action when present. The Overview calls `api.outreach.pack.get()` once on load (this builds the pack, per spec) and uses its counts if the briefing card isn't there yet.

- [ ] **Step 1: Write the failing e2e spec** `frontend/e2e/outreach-pack.spec.ts` against mocked `/api/v1/outreach/pack*` routes: `shows companies with badges` (one `found`, one `likely, unverified` with "format learned from"), `Open in email has the encoded mailto`, `I sent it posts the edited text and the item shows Sent`, `Skip hides the item`, `empty pack shows the widen copy`. Add a pure test for `mailtoHref` in the same spec (no browser).
- [ ] **Step 2: Run** `npx playwright test e2e/outreach-pack.spec.ts` from `frontend/` — Expected: FAIL (needs the stack for auth like `events.spec.ts`; if the stack is down, run the pure test only and note it).
- [ ] **Step 3: Implement** the components, api calls, styles, and `buildActions` change.
- [ ] **Step 4: Run** `npx tsc --noEmit` and `npm run build` in `frontend/` — Expected: no errors, build succeeds. Run the e2e spec if the stack is up.
- [ ] **Step 5: Commit** `feat(frontend): today's outreach pack on /outreach and the Overview`

---

### Task 12: Whole-branch check

- [ ] **Step 1: Run** `make check` (ruff + mypy strict) — Expected: clean.
- [ ] **Step 2: Run** `make test` — Expected: all pass.
- [ ] **Step 3: Run** `alembic upgrade head` against a scratch Postgres if one is available (`make up` stack), then `alembic downgrade -1` and `upgrade head` again — Expected: no errors. If no Postgres is available, say so in the hand-off.
- [ ] **Step 4: Commit** any fixes `chore: fix lint and types for the daily pack`
