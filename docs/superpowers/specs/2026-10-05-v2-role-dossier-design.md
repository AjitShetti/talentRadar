# TalentRadar v2 — the Role Dossier

Status: approved and built 2026-10-05 · branch `v2` · see "As built" at the end
Supersedes the scope in `docs/v2-plan.md` (Zcode's proposal). That document's
diagnosis is kept; its feature list is not.

## 1. What v2 is for

v1 works and nobody uses it. Zcode's diagnosis is right on the two points that
matter: there is no reason to switch to it (six adequate features, none
tellable) and no way to arrive at it (launch was a URL; the first thing a
stranger sees is a 40-second wake screen).

v2 therefore has one job: **give a stranger one tellable reason to come, on a
page that loads instantly, and one loop to stay for.**

The tellable reason:

> Apply to fewer roles. Know each one is open, know how you get in, and walk
> in prepared.

This is the opposite bet from auto-apply, which is where the market is turning
sour in 2026: mass applications are being flagged and deprioritised by ATS
vendors, response rates on cold applications are in the low single digits,
and referrals convert several times better than cold applies. India adds its
own pressure — fresher and services hiring is down sharply, product companies
are referral-driven, and "is this posting even real" is the loudest complaint.

Success for v2, measured from the first week it is live:

- A public role page reaches first paint without waiting on the Render API.
- A visitor who lands on a public page can tell, in one screen, whether that
  role is open and what we know about it.
- ≥ 30% of new signups open a dossier and start a prep within 24 hours.
- North star: **prepared applications per week** — an application whose role
  had a completed prep session before status moved to `applied`.

## 2. What I am taking from Zcode's plan, and what I am not

Kept: the "posting → interview" thesis, job-anchored prep, the kill list
(no auto-apply, no stealth-scraper expansion, Copilot frozen, market-trend
dashboards hidden), public pages as the acquisition surface.

Changed:

| Zcode's plan | v2 | Why |
|---|---|---|
| 0–100 "freshness score" on every job | A **liveness verdict with its evidence** — no number | We cannot defend a number. See §3. PRODUCT.md principle 1 forbids implying knowledge we do not have. |
| "Both blocks ~80% pre-built" | Liveness is ~0% built | `ats_scraper.py` writes `posted_at=datetime.now(UTC)` for every live row; `greenhouse.py` prefers `updated_at` over `first_published`; retention deletes the only history we have. There are no trustworthy dates in the index today. |
| Paste any job URL → score | Paste URL → verdict **only where we can verify** (Greenhouse / Lever / Ashby, or a role already in our index) | LinkedIn/Naukri URLs cannot be fetched reliably and have no history with us. An honest "we can't verify this one, here is what to check" beats a made-up score. |
| Paid Render as the cold-start fix | Public pages statically regenerated on Vercel (ISR) | The visitor never touches Render. Paying $7/mo is still worth doing, but v2 must not depend on it. |
| Nothing about referrals | **Way in**: referral path on every dossier | Referrals are the highest-converting channel and the one India's product companies actually hire through. Company Intel already stores contacts with provenance. |
| Razorpay, ₹499 Pro, OA mode, weekly digest, scorecards, press report — 10+ weeks | Deferred to v2.1 | Billing before users is premature. Each is a separate project; none is needed to test the thesis. |

## 3. Liveness: evidence, not a score

### What we can actually observe

| Signal | Source | Available for |
|---|---|---|
| `source_posted_at` — when the employer first published | ATS API (`first_published`, `createdAt`, `publishedAt`) | ATS rows |
| `source_updated_at` — last edit at source | ATS API (`updated_at`) | Greenhouse |
| `first_seen_at` / `last_seen_at` — when *we* first and last saw it | our own writes | all rows |
| `last_verified_at` + `closed_at` — result of re-fetching the posting | ATS JSON API re-check | ATS rows |
| `times_reposted` — same company + normalised title reappearing under a new external id | `role_sightings` (below) | all rows |

### The verdict

A pure function, `domain/liveness.py::assess(...) -> LivenessVerdict`, no I/O,
no LLM. It returns a `state`, a one-line `headline`, and the list of
`evidence` lines that produced it. The UI always shows the evidence.

| State | Rule (first match wins) |
|---|---|
| `closed` | `closed_at` is set — the source returned 404/410 or dropped the posting |
| `reposted` | `times_reposted ≥ 2` within 120 days |
| `ageing` | open, and `source_posted_at` (or `first_seen_at` when absent) > 45 days ago |
| `verified_open` | `last_verified_at` within 72 hours and none of the above |
| `open_unverified` | seen by us within 7 days but the source cannot be re-checked (boards) |
| `unknown` | anything else |

Thresholds are module constants. There is no weighting and no score; if we
later want one it is a new function over the same evidence.

### Data changes (migration `013`)

`jobs` gains: `source_posted_at`, `source_updated_at`, `first_seen_at`
(default `now()`), `last_seen_at`, `last_verified_at`, `closed_at` — all
nullable `timestamptz`. `posted_at` stays and keeps its current meaning for
ranking; ingestion stops writing `now()` into it and writes
`source_posted_at` when the source has one, else leaves it null.

New table `role_sightings` — the history that must outlive retention:

```
company_id uuid, title_key text, first_seen_at, last_seen_at,
times_seen int, times_reposted int, last_external_id text
PRIMARY KEY (company_id, title_key)
```

One small row per (company, normalised title). `job_retention.py` never
touches it. A repost is recorded when a sighting arrives with an
`external_id` different from `last_external_id` more than 14 days after
`last_seen_at`.

### Re-verification

`services/liveness.py::reverify_batch(limit)` re-fetches ATS postings oldest-
`last_verified_at` first, sets `last_verified_at` or `closed_at`. Bounded per
run (default 150), concurrency 5, one retry, never raises. It is triggered by
`POST /api/v1/ingest/reverify` (admin token) from a GitHub Actions cron — the
same pattern as `keepalive.yml`, because there is no Celery worker. Roles a
user has saved or applied to are verified first. Opening a dossier whose
verification is older than 72 hours triggers a single on-demand re-check.

## 4. The Role Dossier

One page per role, `/roles/[id]`, replacing the job-detail drawer as the place
every "open this job" link goes. Four sections, each answering one question,
each backed by something that already exists:

1. **Is it open?** — liveness verdict and evidence (§3).
2. **Is it for me?** — match breakdown from the existing `/match` scorer
   against the saved resume: matched skills, missing skills, experience fit.
3. **What's my way in?** — the company's logged contacts from Company Intel
   with their provenance labels, the existing "discover contacts" action, and
   a **referral ask** drafted for one chosen contact (one LLM call, on demand,
   grounded in the JD + the user's resume; never sent by us, copied by the
   user). No contact is ever invented. No contact → the section says so and
   offers the careers page.
4. **Am I ready?** — "Prepare for this role": starts an Interview Lab session
   anchored to this JD (below). Shows the last prep score for this role if
   one exists.

Then the actions: Tailor resume (existing `/resumes/tailor`, pre-filled with
this job), Save / Mark applied (existing tracker).

`GET /api/v1/roles/{job_id}/dossier` composes this in `services/dossier.py`
from existing services. Each section is fetched independently and may be
absent; one failing section never fails the page.

### JD-anchored prep

- `interview_sessions` gains nullable `job_id` (FK `jobs.id`, `SET NULL`).
- `agents/interview/` gains a `role_context` field in state: role title,
  company, top skills, a ≤ 1,500-character JD excerpt, and the company's
  top languages from `services.github.org_snapshot` when available.
  Question and scoring prompts receive it. The JD excerpt is delimited and
  the prompt instructs the model to treat it as reference material, not
  instructions — a posting is untrusted text.
- Session end returns the existing score plus `gaps`: JD skills the answers
  scored weakest on. The dossier links those into Resume Studio's tailor.
- No new LLM call on the search path. Prep costs what an Interview Lab
  session costs today.

## 5. The public surface

All statically generated on Vercel with ISR, so first paint never waits on
Render:

- `/r/[slug]` — public role page: title, company, location, liveness verdict
  with evidence, skills. CTA: "Prepare for this role" → signup → dossier.
  Only ATS-sourced roles (data we may republish: public ATS APIs) get public
  pages. `revalidate` 6 hours. `JobPosting` structured data, with
  `validThrough` set when `closed`.
- `/hiring/[company]` — a company's open India roles, with how long each has
  been open and how many were reposted.
- `/check` — paste a posting URL. Greenhouse/Lever/Ashby URLs are fetched and
  verified live; a URL already in our index returns its verdict; anything
  else returns "we can't verify this source" plus the manual checklist.
  Rate-limited 10/min per IP, no login.
- `/` — landing rewritten around the one promise, with a live `/check` box
  and three real role pages as the proof.
- `sitemap.xml` generated from the public roles list.

Backend: a new unauthenticated router `api/routers/public.py`
(`/api/v1/public/roles`, `/public/roles/{slug}`, `/public/companies/{slug}`,
`/public/check`), read-only, cached through `CacheBackend`, strictly
rate-limited. It exposes no user data and no board-scraped rows.

Analytics: PostHog (free tier) from the frontend only — `public_role_view`,
`check_submitted`, `signup`, `dossier_opened`, `prep_started`,
`prep_completed`, `referral_drafted`, `resume_tailored`, `marked_applied`.
Key from env; absent key means no analytics, never an error.

## 6. Constraints carried over from v1

- 512 MB instance, 0.5 GB database. `role_sightings` is the only new table;
  the six new columns are nullable timestamps.
- No per-posting LLM call on the live path. Liveness is LLM-free end to end.
- Nothing may require the cache, the vector store, GitHub, or PostHog.
- India-only. Public pages are India roles only.
- mypy strict, ruff, routers thin, logic in `services/` and `domain/`.
- Feature names stay: Interview Lab, Resume Studio, Company Intel, Career
  Copilot, Overview, Find roles. "Role Dossier" is the one new name.

## 7. Build order

Each milestone ships on its own and is a separate brief for Zcode under
`docs/v2/briefs/`.

| # | Milestone | Ships |
|---|---|---|
| M1 | Liveness foundation | migration 013, honest source dates, `role_sightings`, `domain/liveness.py`, `services/liveness.py`, reverify endpoint + cron. Backend only. |
| M2 | Liveness in the product | verdict on search cards and job detail, "hide closed" default and filter, stale-application nudge in the Overview when a tracked role closes. |
| M3 | Role Dossier + JD-anchored prep | `/roles/[id]`, dossier endpoint, `job_id` on sessions, `role_context` in the interview graph, gaps → tailor. |
| M4 | Public surface | public router, `/r`, `/hiring`, `/check`, landing rewrite, sitemap, analytics. |
| M5 | Way in | referral-ask draft on the dossier. |

M1 → M2 → M3 are sequential. M4 depends only on M1 and can run alongside M3.
M5 depends on M3.

## 8. Testing

- `domain/liveness.py`: table-driven unit tests, one per state and per
  boundary (45 days, 72 hours, repost window).
- Source date mapping: extend the fixture contract tests so each ATS source
  asserts `source_posted_at` comes from the fixture, never from the clock.
- `role_sightings`: repost detection, and a retention test proving a prune
  leaves sightings intact.
- `reverify_batch`: mocked HTTP — 200, 404, timeout, malformed; never raises.
- Dossier: each section absent in turn still returns 200.
- Public router: no auth needed, no user fields in any response, board rows
  excluded, rate limit enforced.
- Interview: `role_context` reaches the prompt; a JD containing instructions
  does not change the system prompt.

## 9. Not in v2

Auto-apply. Payments and plans. OA practice mode. Email digest. Shareable
scorecards. The "State of Ghost Jobs" report (it becomes possible, and worth
doing, about eight weeks after M1 has been collecting sightings). New
scrapers.

## 10. Decisions for Ajit

1. **Direction** — Role Dossier (open? / for me? / way in? / ready?) as the
   v2 spine, with evidence-based liveness instead of a score.
2. **Render Starter, ~$7/mo** — recommended yes: it removes the wake screen
   inside the app and lets the reverify cron run reliably. v2 is designed to
   work without it.
3. **PostHog** as the analytics provider (free tier, a third-party script on
   the site).

## 11. As built

Approved 2026-10-05 with one override: **the whole system must cost nothing**,
so decision 2 (Render Starter) is withdrawn. Everything runs on the existing
free tiers; the reverify cron runs from GitHub Actions.

Differences from the design above:

- Built in one pass by Claude rather than as briefs for a second agent; there
  is no `docs/v2/briefs/`.
- **Interview "gaps"** are not derived from interview answers. Mapping answers
  to JD skills needs a model call per session; the dossier instead shows the
  deterministic resume-vs-role skill comparison, and the end-of-session
  response carries `job_id` so the UI can return to the dossier.
- **`role_context` is stored on the session row** (migration 014) instead of
  being rebuilt each turn, so a pruned job cannot change a running interview.
- **No `JobPosting` structured data** on public pages. Google requires the
  full description, which we deliberately do not republish.
- **Analytics** posts to PostHog's capture endpoint with `sendBeacon` rather
  than loading the PostHog script, so there is no third-party JavaScript.
- **Overview nudge** fires only for *saved* roles that close; a posting coming
  down after you applied is ordinary and says nothing about your application.
- **No response cache** on the public endpoints: the frontend's static
  regeneration already is the cache.
- Found and fixed on the way: `job_retention.prune_stale_jobs` had never
  deleted a row on a real database (asyncpg rejected an integer bound into a
  string concatenation and the error was swallowed).

Known gaps, in priority order:

1. Live-scraped ATS rows have no description (the board list endpoints omit
   it), so a role-anchored interview for such a row knows only the title,
   company and skills. The Greenhouse per-posting endpoint returns the content
   and is already called by the re-check; capturing it there is the fix.
2. Resume Studio and Company Intel take no deep link, so the dossier's
   "Tailor my resume" and "Find or add a contact" open those pages without the
   role or company preselected.
3. `/hiring/[slug]` pages are not listed in the sitemap.

