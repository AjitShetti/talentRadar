# Outreach — design (phase 2 of the outreach suite)

Status: draft for review 2026-10-15 · one PR
Context: `2026-10-14-outreach-suite-roadmap.md` (why, and the reverse-engineering
notes) · `2026-10-14-company-map-design.md` (the companies, people and funding
news this phase stands on)

## 1. What this is for

Applying through portals mostly fails: listings are flooded and filtered.
People get hired because a founder answered a short note, an engineer passed
their name on, or a maintainer saw their pull request. TalentRadar already
holds the raw material for that route — every Bengaluru tech employer on a
map, its founders and engineers with cited sources, funding news, open roles,
and a referral-ask drafter. What is missing is the habit around it: **who to
reach today, through which channel, what to say, and when to nudge again.**

Outreach is that habit. A user picks companies (or takes the ranked
suggestions), sees who inside each one is worth writing to, gets a draft they
edit, sends it themselves in LinkedIn, email, X or GitHub, and clicks **"I sent
it"**. TalentRadar then keeps the clock: it tells them when a follow-up is due,
caps the follow-ups so nobody gets pestered, and records the conversation when
the user says someone replied.

The measure of success is **conversations started**, not messages sent. The
page's one big number is the count of threads where someone wrote back.

## 2. Goals, non-goals, anti-goals

**Goals**
- A per-user, per-company **thread** with the people in it, every touch
  (channel, direction, time) and the next date that matters.
- **Suggestions**: a ranked, explainable list of companies worth reaching
  this week, built from data we already hold. Deterministic; no LLM.
- **Who to write to inside a company**: the people on record, ranked by
  company size and how reachable they are, each with a reason.
- **An open-source way in**: good-first-issues from the company's GitHub org.
- **Drafts on demand**: one model call per draft, a template when the model
  is unavailable or the user's daily cap is reached, always edited by the user.
- **A follow-up rule** that is generous enough to be useful and strict enough
  never to read as spam.
- **The day plan**: due follow-ups and one fresh suggestion feed the Overview,
  under the existing "dated commitments > suggestions > counts" rule.
- Works at 375 px and from the keyboard.

**Non-goals (later, each its own decision)**
- Reading the user's mailbox, LinkedIn inbox or X DMs to detect replies.
  The user records a reply; nothing is inferred.
- Email, push or calendar reminders (Ajit: "No email or push reminders").
- Follow-up dates on the Applications tracker. It keeps its own
  stale-application nudge (§7).
- A "Reach out" entry on the Role Dossier. The thread can carry a role (§3.1),
  but the dossier button waits for its own small PR.
- New people sources. Outreach uses the company map's people and the user's
  own saved contacts; it never discovers anyone.
- Events as a source of suggestions (phase 3 adds them; the `event` channel
  exists now so a meeting can be logged).
- Shared or team threads. A public profile of what someone has reached.

**Anti-goals: failure even if it works**
- Anything sent on the user's behalf. No send button anywhere; the strongest
  verb in the UI is "Copy and open LinkedIn".
- Bulk: a multi-select, a "message all founders" action, a sequence, or an
  automatic follow-up.
- A guessed address. `firstname@domain` patterns, an email "finder", or an
  email that a curated row cannot cite.
- Fetching a LinkedIn page, or automating LinkedIn in any way. Deep links
  that open in the user's own session only.
- Streaks, guilt copy, or a red badge for a missed day. The target is soft.
- "Messages sent" as the headline metric, or any leaderboard of it.
- A draft that invents shared history, a mutual friend, a project or a number.
- An LLM deciding whom to contact or in what order.
- Storing a draft the user did not choose to keep.

## 3. Data model — migration `017_outreach`

`revision = "017_outreach"`, `down_revision = "016_company_is_tech"`. The
migration creates five tables and alters none, so it stacks cleanly with 018
(events) and 019 (practice): the controller re-points one `down_revision` line.

**Claude's call (delegated 2026-10-15):** the daily target lives in its own
`outreach_settings` table, not a `profiles` column. A `profiles` column would
put the Profile model in three parallel branches' diffs; a one-row-per-user
table keeps 017 self-contained.

### 3.1 `outreach_threads` — one per user per company

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK users, `ON DELETE CASCADE`, not null | private to this user |
| `company_id` | uuid FK companies, `ON DELETE SET NULL`, null | null for a typed company we don't know (§3.6) or a company later removed |
| `company_name` | `varchar(256)` not null | snapshot, so a thread outlives its company row |
| `job_id` | uuid FK jobs, `ON DELETE SET NULL`, null | a posting attached as context |
| `job_title` | `varchar(256)` null | snapshot; jobs are pruned after 45 days |
| `application_id` | uuid FK job_applications, `ON DELETE SET NULL`, null | link to the tracker (§7) |
| `source` | `varchar(12)` not null | `suggestion \| drawer \| list \| typed \| overview` |
| `note` | `varchar(500)` null | the user's "why this company", bounded |
| `next_step` | `varchar(140)` null | after a reply: "Call on Thursday" |
| `next_step_at` | `date` null | the date that step is due, IST |
| `closed_at` | timestamptz null | set when the user closes the thread |
| `outcome` | `varchar(16)` null | `referred \| interviewing \| offer \| not_now \| not_a_fit \| other` |
| `created_at`, `updated_at` | timestamptz | |

Indexes: `(user_id)`; a partial unique index `uq_outreach_threads_user_company
ON (user_id, company_id) WHERE company_id IS NOT NULL` — re-adding a company
returns the existing thread (reopening it if closed).

### 3.2 `outreach_people` — the people in a thread

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `thread_id` | uuid FK outreach_threads, `ON DELETE CASCADE` | |
| `contact_id` | uuid FK company_contacts, `ON DELETE SET NULL`, null | a curated person or the user's own saved contact |
| `name` | `varchar(128)` not null | snapshot |
| `title` | `varchar(128)` null | snapshot |
| `kind` | `varchar(24)` not null | the contact's kind |
| `state` | `varchar(10)` not null, default `planned` | `planned \| waiting \| replied \| dropped` (`cold` is derived, §4) |
| `first_sent_at` | timestamptz null | |
| `last_outbound_at` | timestamptz null | |
| `follow_ups_sent` | smallint not null default 0 | |
| `next_follow_up_at` | timestamptz null | stored; recomputed on every touch; the user can snooze it |
| `replied_at` | timestamptz null | |
| `email` | `varchar(256)` null | only for a person with no contact row (§3.6) |
| `linkedin_url` | `varchar(512)` null | only for a person with no contact row (§3.6) |
| `created_at` | timestamptz | |

Partial unique `(thread_id, contact_id) WHERE contact_id IS NOT NULL`. In a
thread with a company, every person is a `company_contacts` row first, so a
person always has a source: a curated row cites `source_url`, a user-saved row
is the user's own (`source_kind = 'user'`). The contact row is the source of
truth for links and email; the snapshot columns only keep the name readable
if the contact row goes away.

**User-saved contacts.** "Add someone you know" in a thread calls the existing
`save_contact` (private, `user_id` set), then adds that contact to the thread.
Nothing about the person becomes visible to anyone else. The email a user
types for their own contact is theirs to use; we never generate one.

### 3.3 `outreach_touches` — what happened

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `thread_id` | uuid FK outreach_threads, `ON DELETE CASCADE` | |
| `person_id` | uuid FK outreach_people, `ON DELETE SET NULL`, null | null for a thread-level touch (a GitHub comment) |
| `user_id` | uuid FK users, `ON DELETE CASCADE` | denormalised for the per-day count |
| `channel` | `varchar(10)` not null | `linkedin \| email \| x \| github \| call \| event \| other` |
| `direction` | `varchar(3)` not null | `out \| in` |
| `kind` | `varchar(10)` not null | `first \| follow_up \| reply \| other`, set by the service from the clock (§4), never by the client |
| `note` | `varchar(500)` null | bounded free text |
| `body` | `varchar(2000)` null | a copy of the sent message, only when the user ticks "Keep a copy" |
| `happened_at` | timestamptz not null | defaults to now; backdating up to 30 days, never the future |
| `created_at` | timestamptz | |

Index `(user_id, happened_at)`.

### 3.4 `oss_issue_snapshots` — good-first-issues per company

| column | type | notes |
|---|---|---|
| `company_id` | uuid PK, FK companies `ON DELETE CASCADE` | |
| `org` | `varchar(128)` | the `github_org` it was read for |
| `status` | `varchar(12)` | `ok \| not_found \| rate_limited \| error` |
| `total_count` | integer not null default 0 | GitHub's total for the query |
| `issues` | JSONB | at most 5 `{repo, number, title, url, labels[], updated_at, comments}` |
| `fetched_at` | timestamptz not null | |

Shared across users: it is public data about the company.

### 3.5 `outreach_settings`

`user_id` uuid PK FK users `ON DELETE CASCADE`, `daily_target` smallint not
null default 3 (0–10; 0 turns the suggestion nudge off), `updated_at`.
No row means the default.

### 3.6 Typed companies

**Claude's call (delegated 2026-10-15):** typing a name searches every stored
company (not only listed ones) and offers up to five matches. If the user
picks none, the thread is created with `company_id = NULL` and the typed name.
Such a thread has no suggestions, people on record or issues — only people the
user adds. *Reason: a user's typing must not create rows in the shared
`companies` table, which the map lists and other users see.*

A `company_contacts` row needs a `company_id`, so in a name-only thread "Add
someone you know" stores the person on the thread only: an `outreach_people`
row with `contact_id = NULL` and the name, title, email and LinkedIn URL the
user typed. Those two link columns are used for nothing else.

### 3.7 Caps and retention (0.5 GB Neon)

| limit | value | on breach |
|---|---|---|
| open threads per user | 100 | 409 "Close a thread before adding another" |
| people per thread | 12 | 409 |
| touches per thread | 60 | 409 |
| note / thread note | 500 chars | 422 |
| next step | 140 chars | 422 |
| kept message copy | 2,000 chars | 422 |

**Claude's call (delegated 2026-10-15):** retention deletes a thread that is
closed or cold and has had no touch for 365 days (touches and people
cascade). It runs inside the existing `services/job_retention.py` pass.
*Reason: a year-old dead thread is history nobody reads; the caps keep the
live set tiny (~1 KB a thread), so the prune exists for the long tail, not
for pressure.* Estimate: 1,000 users × 30 threads × 10 touches ≈ 30 MB worst
case; `oss_issue_snapshots` ≈ 1,000 rows × 2 KB = 2 MB.

## 4. Stages and the follow-up rule — `domain/outreach.py` (pure)

Ajit fixed the trigger: "The user clicks **'I sent it'**, which starts the
follow-up clock." The rest is delegated.

**Claude's call (delegated 2026-10-15): the clock runs per person, not per
thread.** Writing to a founder on Monday and an engineer on Thursday are two
clocks; a single thread clock would nag about the founder on the engineer's
schedule.

| constant | value |
|---|---|
| `FOLLOW_UP_AFTER_DAYS` | `(5, 7)` — first follow-up 5 days after the first message, second 7 days after the first follow-up |
| `MAX_FOLLOW_UPS` | `2` |
| `COLD_AFTER_DAYS` | `10` days after the last follow-up |
| `RECONTACT_AFTER_DAYS` | `90` — a cold person can be drafted for again after this |
| `SNOOZE_MAX_DAYS` | `14` |
| `BACKDATE_MAX_DAYS` | `30` |
| `SAME_COMPANY_WEEK_SOFT_LIMIT` | `2` first messages per company per 7 days before the pacing note shows |

*Reason: 5/7/10 days, two nudges, then rest — about three weeks end to end —
is the common advice for cold outreach and is short enough to keep a search
moving. Three unanswered notes is where persistence becomes pestering.*

**Person states.** Stored: `planned` (in the thread, not written to),
`waiting` (at least one outbound touch, no reply), `replied`, `dropped` (the
user took them out of the plan). Derived at read time:
`cold` = `waiting` and `follow_ups_sent == MAX_FOLLOW_UPS` and
`now ≥ last_outbound_at + COLD_AFTER_DAYS`. Deriving `cold` means no job has to
run for a thread to cool.

**Transitions** (`apply_touch(person, touch, now) -> PersonClock`):
Each transition also names the touch's `kind`: `first`, `follow_up`, `reply`
or `other`.

- `out` on `planned` → `waiting`; `first_sent_at = last_outbound_at = at`;
  `next_follow_up_at = at + 5 d`; kind `first`.
- `out` on `waiting` with `follow_ups_sent < MAX_FOLLOW_UPS` → kind
  `follow_up`: `follow_ups_sent += 1`, `last_outbound_at = at`;
  `next_follow_up_at = at + 7 d` after the first follow-up, `None` after the
  second.
- `out` on a `cold` person once `RECONTACT_AFTER_DAYS` have passed since
  `last_outbound_at` → kind `first`: a fresh first message,
  `follow_ups_sent = 0` and the first-message clock again.
- `in` (any channel), or a `call`/`event` touch in either direction → kind
  `reply`: `replied`; `replied_at = at`; `next_follow_up_at = None`.
- Any other `out` (on `replied` or `dropped`, a third follow-up, or a cold
  person inside 90 days) → kind `other`, logged, no clock change. The UI
  offers no draft in these states, but the user may have written anyway, and
  the timeline should say so.
- A thread-level touch (no person, e.g. a GitHub issue comment) has kind
  `first` for its first `out` in that thread and `other` after that.
- Snooze sets `next_follow_up_at = now + n days`, 1 ≤ n ≤ 14, `waiting` only.

**What "replied" means.** The person answered in any way — yes, no, "not
now", a call booked. A "no" is still a conversation started; the user can then
close the thread with outcome `not_now`. Only the user records it ("They
replied"), with an optional bounded note and an optional next step + date.

**Thread stage** (`thread_stage(people, closed_at, now)`), first match wins:
`closed` (closed_at set) > `replied` (anyone replied) > `waiting` (anyone
waiting, not cold) > `cold` (anyone cold) > `planned`.

**Due items** (`due_items(threads, now)`), sorted: next steps first (a
conversation already started), then follow-ups by `next_follow_up_at`
ascending:
- a `waiting` person with `next_follow_up_at ≤ now`;
- a `replied` thread with `next_step_at ≤ today (IST)`.

**Pacing note.** When a company already has 2 first messages in the last 7
days, the thread shows "You've written to 2 people at Tessel AI this week. Give
them a few days before the next." It never blocks.

**"Today" is IST.** **Claude's call (delegated 2026-10-15):** day boundaries
for "due today", "reached today" and the per-user draft cap use a fixed
UTC+05:30 offset (India has no DST). *Reason: the server runs UTC; a user in
Bengaluru should not see "today" roll over at 05:30.*

**Daily target.** "A soft daily target the user can set (default 3), with no
streak penalty." **Claude's call (delegated 2026-10-15):** "reached today"
counts the user's touches of kind `first` whose `happened_at` falls on today
(IST): a first message to a person, or a thread's first GitHub comment.
Follow-ups don't count. *Reason: follow-ups are
already surfaced as due work; the target is about starting new conversations,
and counting nudges would reward nagging.* Missing the target changes nothing
tomorrow.

## 5. Suggestions and people — `domain/outreach_rank.py` (pure)

Ajit fixed the inputs: "raised in 90 days, hiring for the target role,
sector/skills match, open source to contribute to". No LLM is on this path.

### 5.1 Ranking companies

Candidates: listed Bengaluru companies (`listed_clause("Bengaluru")` from
`services/company_map/read.py`), minus companies already in one of the user's
threads (any state) and minus suggestions the user dismissed. The service
loads at most 400 candidates that have at least one signal, scores them in
Python and caches the top 25 per user for an hour.

**Claude's call (delegated 2026-10-15): the weights.**

| signal | points | reason line |
|---|---|---|
| hiring for the target role in Bengaluru (same title matcher as `hiring_for_you`) | 35, or 40 with ≥ 3 such roles | "Hiring Backend Engineer · 2 open" |
| any open Bengaluru role, none matching | 8 | "6 open roles" |
| last funding ≤ 30 days | 30 | "Raised Series A · 12 days ago" |
| last funding 31–90 days | 20 | same |
| last funding 91–180 days | 8 | same |
| skills overlap (user skills ∩ company stack + its postings' skills) | 5 per skill, max 20 | "Uses Python, Kafka" |
| sector match (user's target roles + career goals text → `sector_from_text`) | 10 | "Fintech" |
| good-first-issues open (`total_count ≥ 1`) | 12 | "4 good first issues" |
| a named person on record (founder, leader, engineer or user-saved) | 5 | — |

A company needs **≥ 20 points** to be suggested — at least one strong signal.
Ties: score, then most recent funding, then name. Each suggestion carries its
top three reason lines, strongest first, and the top-ranked person (§5.2).
*Reason: hiring for your role is the most direct route to a job; fresh money
is the strongest predictor of new headcount; skills and sector make the note
credible; open source is a real but slower route.*

A user with no profile still gets suggestions from funding, open roles and
open source, with a line asking for a target role (§8.4).

Dismissing a suggestion ("Not now") reuses briefing dismissals: card id
`outreach_suggestion:<company_id>`, snoozed 30 days. The same id hides the
Overview card and the /outreach row, so the two can never disagree.

### 5.2 Ranking people inside a company

**Claude's call (delegated 2026-10-15): who to write to depends on size.**
`size_class`: `large` when `is_bigger_company(...)` is true; `small` when the
size bucket is `1-50` or the stage is pre-seed, seed or Series A; else `mid`.

| kind | small | mid | large |
|---|---|---|---|
| `referral` (user-saved: someone they know) | 55 | 55 | 55 |
| `founder` | 50 | 35 | 10 |
| `leader` | 40 | 45 | 35 |
| `engineer` | 25 | 30 | 40 |
| `recruiter` | 20 | 35 | 45 |
| `other` | 15 | 15 | 15 |
| `careers_inbox` | 10 | 15 | 20 |

Bonuses: user-saved +15, `verified` +10, a stored profile link (LinkedIn, X,
GitHub) or a sourced public email +8. A `careers_page` row is a link, not a
person, and is shown as the thread's careers channel instead. Ties: name.

Reason lines (one per kind × size): small founder "Founders read their own
messages at this size"; mid leader "Engineering leads hire for their own
teams"; large recruiter "Recruiters run the pipeline at this size"; large
engineer "An engineer here can refer you"; user-saved "Someone you know".
*Reason: at 20 people the founder is the hiring manager; at 2,000 the founder
is unreachable and a recruiter or a peer engineer is the way in.*

**Verified vs unverified.** Both are shown. Unverified people carry the
existing `unverified` badge and the host of their source. Verification only
adds 10 points: almost every sourced person is unverified (company map §4.5),
and hiding them would hide the founders.

### 5.3 Channels per person (built at read time, never fetched)

- **LinkedIn**: the stored profile link, else `person_search_link(name,
  company)` (company map §6), labelled "Search LinkedIn".
- **Email**: only a stored email that is either user-saved or carries a
  `source_url`; the UI shows "from <host>". It opens
  `mailto:<addr>?subject=…&body=…`.
- **X**: the stored `x_url`. **GitHub**: the stored `github_url`, or the issue.
- **Other**: `call`, `event`, `other` are log-only.

## 6. The open-source path — `services/outreach/oss.py`

Source: GitHub's official REST search API, `GET /search/issues` with
`q = org:<org> is:issue is:open no:assignee archived:false
label:"good first issue","good-first-issue","help wanted" updated:>=<today-365d>`,
`sort=updated`, `order=desc`, `per_page=5`. The comma inside one `label:`
qualifier is an OR; the builder confirms that with one live call before
recording the fixture.

**Claude's call (delegated 2026-10-15): a scheduled step only; a page load
never calls GitHub.** *Reason: unauthenticated search is 10 requests a minute
from Render's shared IP; a page that opened ten threads would exhaust it, and
the company map already set the rule that the drawer makes no outside call.*

- `POST /api/v1/ingest/outreach-issues?limit=N`, behind
  `require_cron_or_admin`, called daily by `.github/workflows/outreach.yml`
  (02:40 UTC, 08:10 IST). It never raises.
- Rate: with `GITHUB_TOKEN`, 30 searches/minute → a 2.2 s gap and a cap of 60
  per run. Without it, 10/minute → a 6.5 s gap and a cap of 20. A 403 or 429
  stops the run at once and returns `{"rate_limited": 1, ...}`.
- Due order: companies in someone's open thread whose snapshot is ≥ 1 day old;
  then listed tech companies with a `github_org` that raised ≤ 90 days ago or
  have open roles, snapshot ≥ 3 days old; then the rest, oldest first.
- A 404 or 422 records `not_found` with zero issues. Any other failure records
  `error` and keeps the previous `issues`.
- Each issue is validated: `url` must start `https://github.com/<org>/`; title
  flattened to 200 chars; at most 4 labels of 40 chars.

The thread shows the snapshot ("4 good first issues · checked 2 days ago"),
each issue linking to GitHub, with "Draft a comment" (§6.1 format
`github_comment`). With no `github_org`, the section says so; with
`not_found`, "No public issues found".

**Terms.** What Outreach reads from outside, and what it does not:

| source | allowed | how |
|---|---|---|
| GitHub REST API | yes — an official API, used within its documented rate limits | the daily step above |
| LinkedIn | **never fetched** | deep links the user opens in their own session; a draft copied to their clipboard |
| Email | nothing is sent | `mailto:` in the user's own client |
| X | never fetched | the stored profile link |

## 7. Relationship to the Applications tracker

**Claude's call (delegated 2026-10-15): separate, linked, no conversion.** A
thread is about a company and the people in it; an application is about one
posting. They run side by side.

- A thread may carry one role (`job_id` + `job_title`) and one application
  (`application_id`). Adding a company from a role (the List's role rows, a
  future dossier button) attaches the role; if the user already tracks that
  role, the application is linked automatically.
- The thread shows the application as a chip ("Applied · screening") linking
  to `/applications`. With a role and no application, "Track this role" calls
  the existing `POST /applications` and links the result.
- The tracker gets no follow-up dates and no outreach column. Its existing
  `stale_application` card stays the nudge for a silent application.

*Reason: converting a thread into an application would lose the people and
the clock; merging them would give the 109-line tracker a second job. A link
costs two nullable columns.*

## 8. API and pages

### 8.1 Endpoints — `api/routers/outreach.py`, prefix `/api/v1/outreach`, authenticated

| method & path | does |
|---|---|
| `GET /` | the page: `{target: {daily, reached_today}, conversations_started, due[], threads[]}` |
| `GET /suggestions?limit=10` | `{suggestions[], profile_complete}` |
| `POST /suggestions/{company_id}/dismiss` | snooze 30 days; clears the cache |
| `GET /companies/search?q=` | up to 5 stored companies by name, for "Add a company" |
| `GET /companies/{company_id}` | `{thread: ThreadSummary \| null}` for the company drawer |
| `POST /threads` | `{company_id?, company_name?, job_id?, source}` → 201 with the thread, or 200 with the existing one |
| `GET /threads/{id}` | the full thread (§8.3) |
| `PATCH /threads/{id}` | `note, next_step, next_step_at, job_id, application_id, close: {outcome}, reopen` |
| `DELETE /threads/{id}` | removes the thread and its touches |
| `POST /threads/{id}/people` | `{contact_id}` or `{name, title?, email?, linkedin_url?}` (name-only threads) |
| `PATCH /threads/{id}/people/{person_id}` | `{snooze_days}` or `{state: "dropped"}` |
| `DELETE /threads/{id}/people/{person_id}` | |
| `POST /threads/{id}/touches` | `{person_id?, channel, direction, note?, body?, happened_at?, next_step?, next_step_at?}` → the full thread |
| `POST /threads/{id}/draft` | `{person_id?, format, relationship?, issue_url?}` → `{message, subject, generated, reason, remaining_today, max_chars}` |
| `GET /settings`, `PUT /settings` | `{daily_target}` |

A thread id that is not the caller's is a **404**, never 403, so ids reveal
nothing. Every service function takes `user_id` and filters on it.

Cron: `POST /api/v1/ingest/outreach-issues?limit=` (§6).

### 8.2 The `/outreach` page

Nav: a new item **Outreach** (lucide `Handshake`) between "Find roles" and
"Applications". **Claude's call (delegated 2026-10-15):** that position, and
not `Send` as the icon — *nothing is sent from here*.

Layout, desktop (DESIGN.md ledger strip + a two-column split with a vertical
hairline):

- **Ledger strip.** Context line "Outreach · Bengaluru"; title
  "Start conversations."; subhead "Who to reach, how, and when to follow up.
  Nothing is ever sent for you."; below it the target line — three etched
  ticks lit `--success` per reach, "1 of 3 reached today", and a "Daily target"
  select (0–10). Right side: one `FlapText` counter, **conversations started**.
- **Left column.**
  - **Due today** (only when there is something): hairline rows with an amber
    lamp. "Follow up with Asha Rao" / "Tessel AI · wrote 10 Oct · follow-up 1 of 2",
    then the actions *Draft follow-up* · *I sent it* · *They replied* ·
    *Snooze 3 days*. A next-step row reads "Ledgerline · Call with Vikram ·
    today".
  - **Your threads**: a status rail (`.status-tabs`) — All, Not started,
    Waiting, Replied, Cold, Closed — over hairline rows: lamp (amber = due,
    green = replied, `--ink-2` = waiting, faint = planned/cold/closed),
    initials mark, company name, a line naming the lead person and stage, and a
    mono date ("DUE 17 OCT", "WROTE 12 OCT"). A row opens the thread drawer.
- **Right column — "Who to reach".** The "Add a company" search bar on top,
  then the ranked suggestions: name, area in mono, one-line summary, reason
  lines in the `ov-tags` idiom, "Start with: Asha Rao, founder", and two
  actions, *Add to outreach* and *Not now*. No plate numerals: the order is a
  ranking, not a plan.

Amber is spent on the due lamps, the one primary button per drawer, and the
lit focus marker — never on the suggestions list.

**The thread drawer** reuses `.ci-drawer` (full width under 800 px) and is
addressable as `/outreach?thread=<id>` so the Overview can deep-link into it.
`/outreach?company=<id>` scrolls to and highlights that suggestion. Sections:

1. Header: initials, company name, summary, stage/sector codes, the role chip
   and application chip, "Open in Company intel".
2. **People in this thread**: per person a lamp, name · title, kind, the
   verified/unverified badge, "Source: host", the channel links, the state
   line ("Wrote 10 Oct · follow-up due 15 Oct"), and the one action the state
   allows: *Draft first message*, *Draft follow-up*, or none. Under it *I sent
   it*, *They replied*, and a "⋯" with Snooze and Remove. The pacing note sits
   above the people list when it applies.
3. **Who else to add**: the ranked people not yet in the thread, each with its
   reason line and *Add*; then "Add someone you know" (name, title, LinkedIn
   URL, email) which saves a private contact.
4. **Open source**: the snapshot's issues and *Draft a comment*.
5. **Timeline**: touches newest first — mono date, channel, direction, note.
6. Footer: *Close thread* (outcome select) and *Delete thread*.

**The draft panel** opens inline under a person: a format select (LinkedIn
note, LinkedIn message, Email, X message, GitHub comment — only the channels
that person has), a "How do you know them?" select (the four relationships
from `services/referral.py`), *Write the draft*, then an editable textarea
with a mono character counter against the format's limit (danger-dark when
over). Then the channel action — *Copy and open LinkedIn*, *Open in email*,
*Copy and open X*, *Copy and open the issue*, or *Copy* — and, once used,
*I sent it* lights as the drawer's primary button with a "Keep a copy in this
thread" checkbox (off by default). The note under the draft says what made it:
"Written from your resume and this company's profile. Fill any [bracketed]
gap." or "This is a template — you've used today's 20 drafts." with
"N drafts left today" in mono.

**375 px.** The two columns stack: Due, Threads, then Who to reach. Rows drop
to `lamp + text`, with the actions wrapping under the text as full-width
buttons. The target line wraps under the title. The drawer is full width;
the draft textarea is full width and the buttons stack.

### 8.3 The company drawer and the List

In `frontend/app/company-intel/page.tsx`, a slim **Your outreach** block sits
first in the drawer body, above Facts:

- Not in outreach: "Not in your outreach yet." and one *Add to outreach*
  button. After a click: "Added. Open the thread →".
- In outreach: lamp + "Waiting on Asha Rao · follow-up due 17 Oct" (or
  "Replied · next step Thursday", "Not started") and "Open thread →".

The List's company rows gain a compact *+ Outreach* text button in the row
foot. The create call is idempotent; a repeat says "Already in outreach".

### 8.4 Empty states

- **New user, no profile**: suggestions still render from funding, open
  roles and open source, under the line "Add a target role and your skills to
  rank these by who is hiring for you →" (to `/onboarding`). Threads: "No
  threads yet. Pick a company on the right, or add one by name." Drafts work
  without a resume; the model is told there is none and leaves gaps.
- **No suggestions at all**: "Nothing to suggest yet. The company map is still
  filling in — add a company by name."
- **A thread with no people on record**: "No one on record at Tessel AI." with
  the LinkedIn presets (founders, engineering leaders, recruiters, your role)
  and "Add someone you know".
- **A name-only thread**: "We don't have Acme Robotics on the map, so there
  are no people or issues to suggest. Add someone you know."
- **Draft unavailable**: the template, with the reason in the note.

## 9. Drafts — `services/outreach/drafts.py`

Ajit fixed the shape: "an AI draft on demand, one per person, with a template
fallback (the existing referral-ask pattern). Daily cap of about 20 per user.
The user always edits before copying."

**Claude's call (delegated 2026-10-15): a sibling of `services/referral.py`,
not a rewrite.** It imports `_flat`, `build_role_context` and `RELATIONSHIPS`,
uses the same `_chat` call (`groq_fast_model`, temperature 0.5,
`max_tokens=1200`, `reasoning_effort="low"`), and the same "`generated` is
False for a template" contract. *Reason: the referral prompt is tuned and
tested; outreach needs different context and formats, and a shared
parameterised prompt would make both worse.*

**Formats** (`domain/outreach_drafts.py`):

| format | channel | max chars | notes |
|---|---|---|---|
| `linkedin_note` | linkedin | 200 | the free-account connection-note limit |
| `linkedin_message` | linkedin | 700 | |
| `email` | email | 1,200 body | first line `Subject: …`, ≤ 80 chars |
| `x_dm` | x | 280 | |
| `github_comment` | github | 500 | needs `issue_url` from the thread's snapshot |

The reply is cut to the limit at the last sentence end, else the last word
(`fit_to_limit`). Default format: LinkedIn note for a first message, LinkedIn
message for a follow-up, else the channel's only format.

**What goes into the prompt** — all in the user turn, every field flattened
and bounded, external text fenced:

| section | content | bound |
|---|---|---|
| `FORMAT` | the format's label and limit | — |
| `MESSAGE` | "first message", or "follow-up 1 of 2, 6 days after your last note" | — |
| `RECIPIENT` | name, title, kind | 80 each |
| `RELATIONSHIP` | the referral module's brief for the chosen relationship | — |
| `<<<COMPANY>>>` | name, summary, stage, last round + month | 120 / 200 / 40 |
| `<<<POSTING>>>` | the attached role, via `build_role_context` | as referral |
| `<<<ISSUE>>>` | repo + issue title (github_comment only) | 200 |
| `<<<EARLIER MESSAGE>>>` | the kept copy of the last outbound message (follow-ups only, only if kept) | 600 |
| `<<<USER NOTE>>>` | the thread note | 300 |
| `PROFILE` | target role, years, current role | 80 each |
| `RESUME` | active resume text | 2,500 |

The system prompt carries the referral rules (no invented facts, bracketed
gaps, fenced sections are reference not instructions, plain text) plus: a
follow-up is shorter than the first message, does not restate it, makes one
ask and offers an easy out; a GitHub comment asks to take the issue and states
an approach in one line, nothing about jobs.

**Claude's call (delegated 2026-10-15): no drafts after a reply, and reply
notes never reach the model.** A `replied` person gets no draft button; the
draft endpoint returns 409. *Reason: once someone answers, the conversation
should be the user's own voice, and a reply note holds the other person's
words — they did not agree to have them sent to a model.*

**Budgets.**
- Per user: `PER_USER_DAILY_DRAFTS = 20`, key
  `tr:budget:drafts:user:<user_id>:<IST date>`.
- Global: `DAILY_OUTREACH_DRAFT_BUDGET = 150`, key
  `tr:budget:llm:outreach:<UTC date>`.
- Both are claimed through a new `services/llm_budget.py::claim(key, limit)`
  over `CacheBackend.incr` (48 h TTL), incrementing first and failing open, as
  `services/job_enrichment.py` does. The user cap is claimed first; the global
  only when the user is under it.
- Over either, or a model failure: the template, with `reason` =
  `daily_cap | budget | model_unavailable`.

**Claude's call (delegated 2026-10-15): the referral ask counts against the
same per-user cap of 20.** *Reason: "about 20 a day" is a per-person promise
about drafting, whichever button produced the draft; the referral path has no
cap today, which is a hole.*

**Templates** (`fallback_draft`), with visible `[gaps]`:
- LinkedIn note / X: "Hi {first}, I'm a {role} in Bengaluru and I've been
  following {company}'s work on [what caught your eye]. Would you be open to a
  short chat about your team?"
- Follow-up (any channel): "Hi {first}, following up on my note from {date}.
  I'm still keen to learn how {company}'s team works. If now isn't a good
  time, no problem at all."
- Email: subject "{role} interested in {company}"; a four-sentence body: who
  I am, why this company ([one specific reason]), what I'd ask (a 15-minute
  chat or the right person), an easy out.
- GitHub comment: "Hi! I'd like to work on this. My plan: [one line on the
  approach]. Could you assign it to me, or tell me if someone is already on
  it?"

**Nothing is stored** unless the user ticks "Keep a copy" when clicking *I
sent it*; then the edited text (≤ 2,000 chars) is stored on that touch. Drafts
are never logged.

## 10. The Overview day plan

The rule (memory "overview is a day plan", DESIGN.md): the page says what to
do today; dated commitments outrank suggestions, and counts go below.

`services/copilot.py::build_briefing` gains two card kinds from
`services/outreach/briefing.py::outreach_cards(user_id)`, which never raises:

- **`outreach_followup`** — one card for everything due today. Id
  `outreach_followup`, tone `primary`, **not dismissible** (it clears when the
  user acts or snoozes on /outreach). Title "Follow up with Asha Rao at Tessel AI"
  (+ " and 2 others"), or "Next step with Ledgerline: Call with Vikram" when a
  next step leads. Detail: "You wrote on 10 Oct and haven't heard back. This is
  follow-up 1 of 2 — one short note, then let it rest." Action "Open Outreach"
  → `/outreach?thread=<id>`. Placed right after the scheduled-interview cards.
- **`outreach_suggestion`** — the top suggestion, only while
  `reached_today < daily_target` and the target is above 0. Id
  `outreach_suggestion:<company_id>`, tone `action`, dismissible (dismiss =
  that company's suggestion; snooze = 7 days). Title "Reach out to Tessel AI";
  detail = its reason lines joined with " · ", then " Today: 1 of 3 reached.";
  action "See who to contact" → `/outreach?company=<id>`. Placed last.

`frontend/app/dashboard/page.tsx::buildActions` ranks, at most 3:

1. an interview today (unchanged);
2. finish your profile (unchanged);
3. a `scheduled_interview` card with `days_away ≤ 1`;
4. **`outreach_followup`** — tags "N due" (lit) · "Outreach";
5. the briefing lead (first `primary`/`warning` card not used above and not
   an `outreach_*` kind);
6. **`outreach_suggestion`** — tags "1 of 3 today" · its first reason;
7. the existing fallbacks (first mock, practise, study, apply).

**Claude's call (delegated 2026-10-15): a due follow-up outranks the
briefing's next-best action but not an interview within a day.** *Reason: a
follow-up is a date the user set themselves (a dated commitment); an
interview tomorrow is a harder one.* `BriefingFeed`'s labels gain
`outreach_followup: 'FOLLOW-UP DUE'` and `outreach_suggestion: 'WORTH A NOTE'`.
The readout gains nothing: the conversations count lives on /outreach.

## 11. Privacy

- **Private to the user.** Every outreach row carries `user_id` or hangs off
  a thread that does; every read filters on it; another user's id is a 404.
  User-saved people stay private exactly as `company_contacts` rows with
  `user_id` set are today. Deleting the account cascades everything.
- **Bounded free text.** Touch notes and thread notes ≤ 500, next step ≤ 140,
  kept copies ≤ 2,000, all flattened of control characters
  (`domain.outreach.clean_text`). The "They replied" box says "Keep it short —
  this is a note to yourself."
- **What reaches the model**: §9's table only. Reply notes, next steps and
  other people's details never do.
- **What an admin can see.** No endpoint, admin or otherwise, reads another
  user's threads. The cron endpoint touches only `oss_issue_snapshots`. Logs
  carry ids and counts, never names, notes or drafts. Analytics events
  (`outreach_drafted`, `outreach_sent`) carry the format/channel and
  `generated` only. Plainly: whoever holds the Neon credentials can read the
  tables, as with every other table; the product gives no one else a view.

## 12. Free-tier budget

| resource | use |
|---|---|
| Neon | ≤ ~30 MB worst case (§3.7); snapshots ~2 MB |
| Render | no resident memory; suggestions cached per user (≤ 25 rows, ~5 KB) |
| Groq | ≤ 150 outreach drafts/day + referral asks, on demand only |
| GitHub | ≤ 60 searches/day with a token, ≤ 20 without |
| Redis (25 MB) | budget counters and suggestion caches, all TTL'd |
| GitHub Actions | one daily job |

## 13. Failure behaviour

- **No Redis**: budgets fail open per process; the suggestion cache is the
  in-process dict.
- **No Groq / over budget**: the template, saying why.
- **No GitHub / rate limited**: the last snapshot stays; the thread says when
  it was checked.
- **A broken outreach query**: `outreach_cards` returns `[]`; the briefing
  renders without it.
- **A company deleted**: its threads keep `company_name` and lose the link.

## 14. Testing

- Pure: the clock (every transition, cold derivation, re-contact, snooze
  bounds, IST day edges), thread stage, due ordering, pacing, `clean_text`;
  company scoring (each weight, the threshold, ties, reasons), people ranking
  per size class; `fit_to_limit`, `split_subject`, templates within limits.
- Services on in-memory SQLite (as `tests/test_company_map_api.py` does):
  ownership (404 for another user), idempotent create, caps, touches moving
  the clock, the per-day count, suggestions excluding threads and dismissals,
  the OSS step on a recorded search payload (ok, 404, 403 stop), drafts with
  `_chat` mocked (prompt fences, bounds, budgets, 409 after reply), retention.
- API: auth, shapes, 404s, the cron gate.
- Migration: revision ids, and upgrade/downgrade on the Postgres test DB.
- Frontend: `tsc`, build, then a browser check of /outreach, the drawer, the
  draft flow, the company drawer block and the Overview at 1280 and 375 px.

## 15. Decisions Ajit made, in his words (2026-10-14 roadmap and 2026-10-15)

From the brief, accepted as recommended:

1. "**Success:** conversations started, not messages sent. Count reaches, but
   surface replies and next steps; never encourage spam."
2. "**Organised around companies.** A user adds a company. People sit inside
   that company's thread. A job posting can be attached as context."
3. "**Targets come from:** a suggestions list built from existing data (raised
   in 90 days, hiring for the target role, sector/skills match, open source to
   contribute to); an 'Add to outreach' button on the map drawer and the List;
   typing a company name."
4. "**Channels.** Nothing is ever sent for the user." LinkedIn: "open the
   profile in the user's session, with the draft copied to the clipboard."
   Email: "only when a *public* address exists, with its source URL kept. It
   opens a `mailto:` with the draft. Never guess an address." X: "open the
   profile." GitHub: "open the repo or issue." Other: "call, met at an event,
   and so on." "The user clicks **'I sent it'**, which starts the follow-up
   clock."
5. "**Drafts:** an AI draft on demand, one per person, with a template
   fallback (the existing referral-ask pattern). Daily cap of about 20 per
   user. The user always edits before copying."
6. "**Where it lives:** a top-level **Outreach** page (suggestions, threads,
   follow-ups), plus a slim 'Your outreach' block and an 'Add to outreach'
   button in the company drawer."
7. "**Daily rhythm:** Feed the Overview day plan: due follow-ups first, then
   fresh suggestions. A soft daily target the user can set (default 3), with
   no streak penalty. No email or push reminders."

Standing: "LinkedIn: deep links into the user's own session + public sources
only. **Never fetch or scrape LinkedIn.**" Zero cost; India/Bengaluru only;
nothing sent on the user's behalf; no guessed contacts. Then: "Go with your
recommendations, use sub-agents to pull off the implementation."

## 16. Claude's calls (delegated 2026-10-15), collected

Each is marked where it is made above.

1. The follow-up clock runs per person: 5 days, then 7, at most 2 follow-ups,
   cold 10 days after the last, re-contact after 90 (§4).
2. `cold` is derived at read time, never stored (§4).
3. "Today" is IST via a fixed +05:30 offset (§4).
4. The daily target counts first messages only (§4).
5. Suggestion weights and the 20-point threshold (§5.1).
6. People ranked by kind × company size, plus small bonuses; unverified
   people shown, labelled (§5.2).
7. Suggestion dismissals reuse briefing dismissals by id (§5.1).
8. Good-first-issues come only from a daily scheduled step (§6).
9. Separate from the tracker, linked to one role and one application (§7).
10. Typed unknown companies make a name-only private thread, never a shared
    company row (§3.6).
11. The daily target lives in `outreach_settings`, not `profiles` (§3).
12. Retention: closed or cold threads idle 365 days are pruned (§3.7).
13. Drafts are a sibling of `referral.py`; five formats with hard limits (§9).
14. No drafts after a reply; reply notes never reach the model (§9).
15. The referral ask shares the per-user cap of 20 (§9).
16. Global outreach budget 150/day (§9).
17. Kept copies are opt-in, off by default (§9).
18. The Overview order: a due follow-up after an interview within a day and
    before the briefing lead; one non-dismissible follow-up card (§10).
19. Nav position and icon (§8.2).

## 17. Left to the builder

- The exact system-prompt wording, inside §9's rules, tuned on a few real
  drafts; the template sentences may be polished if they stay within limits.
- Confirming the GitHub OR-label syntax with one live call, then recording
  the fixture.
- Confirming LinkedIn's current free connection-note limit in a signed-in
  browser; if it is lower than 200, lower `linkedin_note` to match.
- Copy polish on empty states, inside the tone of DESIGN.md.
- Whether the thread drawer's people actions sit inline or in a "⋯" menu at
  375 px, as long as every action stays reachable by keyboard.

## 18. Calls to check

The five that would change the most if Ajit disagreed, most important first:

1. **The follow-up rule** — per person; follow up after 5 days, then 7; at
   most 2 follow-ups; cold 10 days later; no new draft for 90 days after that.
   It decides how pushy the product feels and what the Overview nags about.
2. **Outreach is separate from the Applications tracker** — a thread links to
   at most one role and one application; there is no conversion, and the
   tracker gets no follow-up dates.
3. **Ranking** — companies by hiring-for-your-role (35/40) > fresh funding
   (30/20/8) > skills (≤ 20) > open source (12) > sector (10), needing 20
   points; people by company size (founders first at ≤ 50 people or seed
   stage; recruiters and peer engineers first at large companies); unverified
   people shown with a badge rather than hidden.
4. **Drafts** — 20 per user per day *including referral asks*, 150 a day
   globally; five formats with hard length limits (LinkedIn note 200 chars);
   no drafts once someone has replied, and reply notes never go to the model;
   a sent message is kept only if the user ticks "Keep a copy".
5. **Open source and outside sources** — good-first-issues come from GitHub's
   official search API in one daily scheduled step (≤ 20 orgs a run without a
   token), never on page load; LinkedIn, email and X are never fetched, only
   opened in the user's own session or mail client.
