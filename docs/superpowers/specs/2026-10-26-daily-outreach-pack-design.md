# Daily outreach pack

Date: 2026-10-26. Status: approved approach (B); design for review.

## Intent

Outreach today shows the user *whom* to contact and leaves the work to them: open a
suggestion, start a thread, add a person, ask for a draft. The user wants TalentRadar
to do that groundwork itself every day, so that the only things left to them are
**review, send, and apply**.

This is an upgrade of existing Outreach features (suggestions, people, drafts,
threads, the follow-up clock), not a new product area. The ranking, the draft
writer, the template fallback and the clock stay; what changes is that they run
ahead of the user and hand them a finished daily pack.

## Goals

- Every IST day, each user who opens TalentRadar gets a **pack of 3–5 companies**
  matched to their profile, whether or not the company is hiring.
- For each company, **up to 2 people** to write to, each with a **ready message**:
  a cold email when an address is known, otherwise a LinkedIn message with a link to
  their profile (or a LinkedIn people-search link).
- Every email address says where it came from: **found** (published at a cited
  URL) or **likely, unverified** (built from the company's address format, learned
  from a found address at the same company). The card names the address that taught
  the format.
- Matching open roles at the company are listed with **Apply** links; "I applied"
  records an application.
- "I sent it" creates the company thread, adds the person, records the touch and
  starts the existing follow-up clock. When a follow-up is due, its message is
  already written in the pack.
- A scheduled step finds published email addresses (company sites, GitHub profiles)
  so the pack has real addresses to use and learn formats from.

## Non-goals

- Sending anything. No endpoint sends a message; "Open in email" is a `mailto:` link
  opened by the user's own mail app.
- Phone numbers of people. Not collected, stored or shown. (A company's public
  switchboard number is out of scope here too.)
- Pooling one user's private contacts into anyone else's pack (Apollo's
  "contributor network").
- Paid enrichment (Apollo, Hunter paid tiers, data brokers), SMTP probing or any
  mailbox verification.
- Building packs for users who don't open the app.

## Anti-goals (would make this a failure even if it works)

- A guessed address presented as found, or stored anywhere as if it were found.
- A page load that reaches the web or calls a model. Reads stay reads.
- Burning the shared AI budget so that later users that day get only templates
  while early users get 10 model drafts each with room to spare.
- Cluttering Outreach with threads the user never acted on (the 100 open thread cap
  is real).
- Showing the same ignored company day after day.

## Constraints (from the project)

- Free tiers only. Groq free tier (8k tokens/min), 150 shared AI outreach drafts per
  UTC day (`DAILY_OUTREACH_DRAFT_BUDGET`), 20 per user per IST day
  (`PER_USER_DAILY_DRAFTS`, `claim_user_draft`), Tavily 800 searches a month.
- No worker. Scheduled work is a GitHub Actions workflow calling a cron-token
  endpoint (`X-Cron-Token`), like `company-map.yml` and `outreach.yml`.
- LinkedIn is never requested by the server; reached by stored profile URLs or
  `domain.linkedin_links.person_search_link` only.
- Company sites: robots.txt respected, 5 s, 300 KB, each site at most once per 30
  days (the existing `services/company_map/website.py` reader).
- Another user's pack, item, thread or person is a **404**, never a 403. Logs carry
  ids and counts only — never an address or a message.
- Rules are pure in `domain/`, I/O thin in `services/`, routers validation and
  delegation only; mypy strict.

## Approaches considered

- **A. Nightly batch for every user.** A 06:00 IST run picks and drafts for all
  users. Instant packs, but spends the shared budget on people who never log in
  (150 drafts ≈ 15 users at 10 each) and the free tier's per-minute limit makes the
  run slow.
- **B. Built on first open each IST day (chosen).** Picking companies and people is
  stored-data ranking with no model call, so it is instant; messages fill in over a
  few seconds through POSTs the page issues. Only people who show up spend the
  budget. Address discovery runs on a schedule because a page load may not reach
  the web.
- **C. Templates only.** Free and instant at any scale but noticeably generic. It
  remains the automatic fallback inside B when a cap is reached or the model fails.

The user chose **B**, with scheduled email discovery, and guessed addresses worked
out at read time.

## Design

### 1. Email discovery (scheduled)

Two sources, both public and cited:

- **Company sites.** Extend `extract_site_facts` (`services/company_map/website.py`)
  to collect email addresses from `mailto:` links and visible text on the pages the
  reader already visits (homepage, and the `/contact` page it already follows; add a
  `/careers` or `/jobs` page and a `/team` or `/about` page to the same visit, within
  the same per-site limits). Keep only addresses whose domain is the company's
  registrable domain (`domain/company_identity.py`).
  - An address inside a person's card (the same card `_people` already reads) is
    that person's email.
  - `careers@`, `jobs@`, `hiring@`, `talent@`, `recruit*@`, `hr@` and similar are a
    **hiring inbox**: stored as a `careers_inbox` contact (the kind already exists).
  - `info@`, `hello@`, `contact@`, `support@`, `sales@`, `press@`, `privacy@`,
    `noreply@` and similar are dropped (not useful, not people).
- **GitHub profiles.** `parse_github_user` (`services/company_map/people.py`)
  ignores the `email` field the `/users/{login}` payload already carries. Read it,
  keep it only when its domain is the company's registrable domain (a personal Gmail
  is someone's private address, not a work contact), and store it on the person.

Storage: `company_contacts.email` already exists. Add **`email_source_url`**
(nullable, 512) so an address's provenance is separate from the row's `source_url`
(a person may be named on one page and their address printed on another). A curated
address is stored only with `email_source_url` set. Existing user-saved contacts are
untouched.

Schedule: this rides the existing `sites` and `github` steps of
`.github/workflows/company-map.yml`; no new workflow. Sites already re-read at most
once per 30 days; to seed addresses faster, the `sites` step should prefer listed
tech companies with named people and no stored address yet. (Exact ordering: the
builder's call.)

### 2. Address formats (pure, read time)

New `domain/email_patterns.py`:

- `learn(examples) -> Pattern | None`: from `(full name, address)` pairs at one
  domain, find the format they share — `first`, `last`, `first.last`, `firstlast`,
  `first_last`, `f.last`, `flast`, `first.l`, `firstl`, `last.first`, `lastf`
  (the builder may add common ones). A pattern is learned only when **every**
  example that can be explained agrees and **at least one** example is unambiguous
  (a one-word name matches several patterns and teaches nothing). Hiring inboxes
  never teach.
- `apply(pattern, name, domain) -> str | None`: only for names with a clear first
  and last part made of letters after normalising case, accents and hyphens; return
  `None` for anything else (initials only, one-word names, non-Latin scripts).
- Pure, no I/O; tested exhaustively on fixtures.

A guessed address is **computed when the pack or a thread is read and never
stored**: not in `company_contacts`, not in `outreach_people`, not in a pack item,
not in a touch. Its payload carries `basis: "guessed"` and `learned_from` (the found
address that taught it). When the user records "I sent it" to a guessed address,
the touch stores the channel and the message but not the address.

*(Decided by the designer, not the user.)* Guesses appear wherever a curated
person's channels are built (`people.channels_for`), so the thread drawer shows the
same address the pack did, with the same badge. `rank_people`'s `has_public_email`
keeps counting found addresses only. A follow-up uses the address as computed at
that moment; if it differs from what the pack showed (a new found address, or the
pattern became ambiguous and no guess is made), the card says so. Email follow-ups
are normally replies in the user's own mail thread, so this is rare and harmless.

The CLAUDE.md rules "No guessed emails" (company map) and "Email appears only for a
user-saved contact, a contact with a `source_url`, or an email the user typed"
(Outreach) are updated to say exactly this.

### 3. Choosing the pack (pure)

New `domain/outreach_pack.py`, fed by the facts `services/outreach/suggest.py`
already gathers in a few batched queries:

- Start from `rank_companies` output (it already excludes companies with a thread
  and dismissed ones, and already lets a non-hiring company through on funding,
  sector, stack and issues).
- Drop companies the user **saw** in one of their packs in the last 14 days and
  left untouched (no send, no skip, no apply). "Saw" means the /outreach Today
  section displayed it (`seen_at`, below); a pack built by an Overview visit that was
  never opened does not count. A skipped company is dismissed for 30 days through
  the existing `dismiss_suggestion`, so it is already excluded.
- Drop companies where the user has an application created in the last 30 days:
  they already applied, and a thread is the place to follow up.
- Keep only companies with **at least one reachable recipient**: a named person
  (any person can be reached through a LinkedIn link) or a hiring inbox.
- Take up to 5; if fewer than 3 pass `SUGGEST_THRESHOLD`, show what passes rather
  than pad. Zero is a valid pack and the page says why ("Nothing new matched today;
  add skills or target roles to widen it").
- People per company: up to 2 from `rank_people` (which already prefers founders and
  leaders at small companies and engineers/leaders at larger ones), never two of
  the same kind unless only that kind exists. A hiring inbox counts as a recipient
  only when the company has fewer than 2 named people.
- Channel per recipient: email if a found address exists; else email if a guess can
  be built; else LinkedIn. Format: `email` for email, `linkedin_note` (200 chars) when
  there's no existing connection to assume, per the existing `FORMATS`.

### 4. Storing the day's pack

Migration `020_outreach_pack` (after `019_practice`; alters `company_contacts` to add
`email_source_url` and creates two tables):

- `outreach_packs`: `id`, `user_id` (FK, cascade), `day` (IST date), `created_at`;
  unique (`user_id`, `day`).
- `outreach_pack_items`: `id`, `pack_id` (FK, cascade), `company_id` (FK, SET
  NULL), `position`, `contact_id` (FK to `company_contacts`, SET NULL; curated people
  and the user's own saved contacts are both rows there), `person_name`, `person_title`, `person_kind` (a snapshot, so the
  card survives a contact edit), `channel` (`email` | `linkedin`), `format`,
  `subject`, `message`, `generated` (bool), `reason` (the template reason or null),
  `status` (`new` | `sent` | `skipped`), `thread_id` (FK, SET NULL), `acted_at`,
  `reasons` (`text[]` — the company's reason lines, declared `TextArray` per the
  array-column gotcha), `seen_at`, `applied_at`. Addresses are not stored here
  (see §2).
- Items have a `kind`: `first` (a company recipient, as above) or `follow_up` (a
  due follow-up: `thread_id` and `outreach_person_id` set, `company_id` from the
  thread). Follow-up messages are stored on the item like first messages, so a
  reload never spends again (see §5).

*(Decided by the designer.)* The pack is built by the first `GET /outreach/pack` of
the IST day, whichever surface sends it: the Overview calls it to show its "Up next"
line, and /outreach calls it to show the Today section. Messages are written only
from /outreach (§5), so the Overview line counts companies and messages already
written ("5 companies to review, 3 messages ready"). /outreach marks the items it
displays with `seen_at` (a small POST, or as part of the write call). A second open
the same day reads the stored pack.
A profile save the same day does **not** rebuild it (predictable for the user; the
next day picks up the change). Retention: packs older than 30 days are pruned from
`services/job_retention.py` alongside `prune_outreach`.

### 5. Writing the messages

The model is called only from POSTs, never from a GET:

- `GET /api/v1/outreach/pack` builds (if needed) and returns today's pack: companies,
  reasons, matching open roles, recipients with their channel and address (found or
  guessed, with basis), and any message already written. No model call.
- `POST /api/v1/outreach/pack/companies/{company_id}/write` writes the messages for
  that company's recipients in **one model call** (both people's messages in one
  JSON reply), stores them on the items, and returns them. Idempotent: if the items
  already have messages it returns them and spends nothing.
- Budget: the call claims **one** unit of the shared `DAILY_OUTREACH_DRAFT_BUDGET`
  and **one per message** of the user's `PER_USER_DAILY_DRAFTS`. Over either cap, or
  on any model failure or unreadable reply, every message for that company is the
  existing template (`fallback_draft`), with `generated=false` and the reason — the
  same contract the thread draft endpoint has today. A 5-company pack therefore costs
  at most 5 shared calls and 10 user drafts.
- The prompt reuses `services/outreach/drafts.py`: refactor `_gather` so the
  `DraftContext` can be built from a pack item (company, person snapshot, user's
  profile, matching role) as well as from a thread, and reuse `build_prompt`'s fenced,
  bounded sections and the resume excerpt. Everything from a company site or a
  person's title is untrusted and fenced as today.
- The page issues the write POSTs one company at a time after the pack loads, so the
  free tier's per-minute token limit is respected for one user; across users the
  existing `_chat` retry/backoff applies.

- *(Decided by the designer.)* Fairness of the shared pool: besides the shared 150
  and the user's 20, each user may spend at most **8 shared units a day**
  (`PER_USER_SHARED_DRAFTS`, key `tr:budget:outreach-shared:<user>:<IST day>`,
  counted like the others: increment first, fail open on a cache error). A full
  pack takes 5, leaving 3 for thread drafts and follow-ups; past 8 the user gets
  templates while the pool lasts for others. That guarantees at least 18 users a
  day get model-written messages. The claim order is user cap, user shared share,
  then the shared pool; a refusal at any step spends nothing further.

Due follow-ups are part of the pack: when the pack is built (and on later reads that
day, for a follow-up that became due since), `touches.due_items` adds one `follow_up`
item per due person. `POST /api/v1/outreach/pack/items/{item_id}/write` writes it
through the existing thread draft path (`drafts.draft` with the thread and person,
which already knows the clock, the earlier message and the follow-up wording) and
**stores** the result on the item, so it is idempotent like the company write. "I
sent it" on a follow-up item records the touch on the existing thread.

### 6. Acting on the pack

- `POST /api/v1/outreach/pack/items/{item_id}/sent` — body: the final message text
  (the user may have edited it) and optional subject. Finds or creates the
  company's thread (`threads.create_thread` / `thread_for_company`), adds the person
  from the contact (found address kept on the person only if it was found; a guessed
  one is not), records an outbound touch with the item's channel through
  `touches.record_touch`, marks the item `sent` with `thread_id`. Respects the
  existing caps (100 open threads, 12 people, 60 touches: 409 as today). Idempotent
  on an already-sent item.
- `POST /api/v1/outreach/pack/items/{item_id}/skip` — marks the item skipped. When
  every item of a company is skipped, the company is dismissed for 30 days through
  `suggest.dismiss_suggestion`.
- **Apply**: the pack lists the company's open Bengaluru roles that match the user's
  target roles (the same match `suggest._role_counts` uses), each linking to
  `/roles/{id}`. "I applied" (`POST /api/v1/outreach/pack/companies/{company_id}/applied`
  with the job id) creates the application through the existing applications
  service, sets `applied_at` on that company's items, and links the application to
  the company's thread if there is one (`threads._set_application`). The company
  then stays out of packs for 30 days (§3).
- "Open in email" is a `mailto:` link built client-side from the address, subject and
  message. "Copy" copies the message. "Open LinkedIn" opens the stored profile URL
  or a people-search link in a new tab (`EXTERNAL_LINK_PROPS`).

### 7. Surfaces

- **/outreach**: a "Today" section at the top: the pack's companies as cards (name,
  one-line summary, reasons, matching roles with Apply), each with its recipient
  rows (name, title, channel, address with a **found** / **likely, unverified**
  badge and its source, the editable message, Copy / Open in email or Open LinkedIn
  / I sent it / Skip). Due follow-ups follow as a second list. Messages being
  written show a skeleton. Uses the Apple-dark tokens and controls from the redesign
  (`.primary-button`, `.outline-button`, `Segmented`).
- **Overview**: "Up next" leads with "Review today's pack — N companies, M messages
  ready" (or the due follow-ups count) linking to `/outreach#today`; this replaces the
  single `outreach_suggestion` action in `buildActions`.
- **Copilot briefing**: a new `outreach_pack` card kind contributes counts only
  (companies, messages ready) to the model, like `outreach_followup`. It is added to
  `MODEL_VISIBLE_CARD_KINDS` as a count; no names, addresses or text.

### 8. Testing

- Pure: `domain/email_patterns.py` (learning, ambiguity, refusal cases, apply on
  awkward names), `domain/outreach_pack.py` (3–5 rule, 14-day repeat rule,
  reachability, two-per-company, channel choice), site email extraction on recorded
  HTML fixtures (person-card attribution, inbox vs dropped role addresses, foreign
  domains ignored).
- Service/API: pack built once per IST day (00:10 IST is a new day, UTC midnight
  isn't), GET never calls the model (assert `_chat` not called), write is
  idempotent and spends one shared unit, template on cap/model failure, guessed
  address never persisted (inspect every table after a send), another user's pack
  item is 404, migration drift test covers the new columns and tables (Postgres
  types of `text[]` as for Events).
- Frontend: an e2e spec against mocked `/api/v1/outreach/pack` for review → Open in
  email → I sent it → thread appears.

## What's left to the builder

- Exact list of role-address prefixes kept as hiring inboxes vs dropped.
- Which extra site pages to visit and how to order the `sites` step to seed
  addresses sooner, within the existing per-site and per-run limits.
- The pack JSON field names and the frontend component breakdown.
- The prompt wording for the two-message write call, within the existing rules and
  fences.
- Whether the Overview shows a compact pack preview beyond the "Up next" action.

## Builds on

The redesign branch (`claude/talentradar-navbar-redesign-53ce86`, PR #33): the pack
UI uses its tokens and the new Overview. This work is on
`claude/daily-outreach-pack`, cut from that branch.
