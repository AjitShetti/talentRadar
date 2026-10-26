'use client'

import { apiIsWaking, markApiAlive } from '@/lib/api-warmup'
import { clearPersistedState } from '@/lib/persistent-state'

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const WAKING_MESSAGE = 'The server is still starting up — give it a few seconds and try again.'
const TOKEN_KEY = 'talentradar_token'
const EMAIL_KEY = 'talentradar_email'

export type Job = { id: string; title: string; company?: string | null; company_name?: string | null; location_raw?: string | null; is_remote?: boolean; skills?: string[]; source_url?: string | null; platform?: string | null; salary_raw?: string | null; match_score?: number | null; description_clean?: string | null; liveness?: Liveness | null }
// Is the posting still open, and on what evidence — see domain/liveness.py.
// Absent when we hold no stored row for the role yet.
export type LivenessState = 'closed' | 'reposted' | 'ageing' | 'verified_open' | 'open_unverified' | 'unknown'
export type Liveness = { state: LivenessState; headline: string; evidence: string[]; open_days: number | null }
// The Role Dossier (services/dossier.py). Every section but `role` may be null: each comes from a different part of the system and the page renders whatever arrived.
export type DossierRole = { id: string; title: string; company_id?: string | null; company?: string | null; location?: string | null; is_remote: boolean; seniority?: string | null; employment_type?: string | null; salary_raw?: string | null; skills: string[]; description: string; source_url?: string | null; platform?: string | null }
export type DossierFit = { available: false; reason: 'no_resume' | 'no_role_skills' } | { available: true; matched_skills: string[]; missing_skills: string[]; coverage_percentage: number }
export type DossierWayIn = { company_id?: string | null; careers_url?: string | null; contacts: CompanyContact[] }
export type DossierReadiness = { sessions: number; last_score: number | null; last_at: string | null; application: { id: string; status: string } | null }
export type Dossier = { role: DossierRole; liveness: Liveness | null; fit: DossierFit | null; way_in: DossierWayIn | null; readiness: DossierReadiness | null }
// One of the dashboard's daily top-3 picks for a Profile.target_roles entry —
// computed by the APScheduler job in api/main.py (services/job_matching.py).
export type JobMatch = { id: string; title: string; company: string; location: string; is_remote: boolean; skills: string[]; salary_raw?: string | null; source_url?: string | null; posted_at?: string | null; matched_role: string }
export type Application = { id: string; job_id?: string | null; status: string; notes?: string | null; applied_at?: string | null; created_at: string; job?: Job | null }
// What a semantic search did about going out to the job boards. Present so a
// thinner-than-usual result set is explainable in the UI rather than silent:
// `reason` says why sourcing did or did not run, and `sources_stats` carries
// per-source counts and latencies for the fan-out.
export type SourceStat = { latency_ms?: number; count?: number; status?: string }
export type Sourcing = { sourced?: boolean; reason?: string; live_count?: number | null; indexed_count?: number | null; sources_stats?: Record<string, SourceStat> }
export type InterviewState = Record<string, unknown>
export type InterviewScore = { correctness: number; clarity: number; depth: number; answer_summary?: string | null; tip?: string | null; scored?: boolean; verbal_ack?: string | null }
export type AtsResult = { ats_score: number; missing_skills: string[]; matched_skills: string[]; suggestions: string[]; reasoning: string }
export type TailorResult = { candidate_name: string; latex_content: string; pdf_base64: string | null; filename: string | null }
export type SavedResume = { id: string; extracted_text: string; filename: string | null; updated_at: string | null }
export type ResumeSectionType = 'summary' | 'education' | 'experience' | 'projects' | 'skills' | 'certifications' | 'custom'
export type ResumeLink = { label: string; url: string }
export type ResumePersonal = { full_name: string; headline: string; email: string; phone: string; location: string; links: ResumeLink[] }
// Shape varies by the owning section's type — e.g. education uses school/degree,
// experience uses title/company, skills uses category/items. Kept as one loose
// type (rather than a discriminated union) since a section's items are only ever
// read/written alongside their own section, where the relevant fields are known.
export type ResumeItem = {
  text?: string
  school?: string; degree?: string
  title?: string; company?: string
  name?: string; tech?: string; link?: string
  category?: string; items?: string[]
  issuer?: string; date?: string
  heading?: string
  location?: string; dates?: string
  bullets?: string[]
}
export type ResumeSection = { id: string; type: ResumeSectionType; title: string; visible: boolean; order: number; items: ResumeItem[] }
export type ResumeDocument = { schema_version?: number; personal: ResumePersonal; sections: ResumeSection[] }
export type CompiledResume = { latex_content: string; pdf_base64: string | null; filename: string | null; compile_error?: string | null }
export type TargetJob = { id: string; title: string; company_name: string | null; description: string | null; location_raw: string | null }
export type SkillFocusItem = { title: string; detail: string }
// Resume vs target roles: `kind` decides what the dashboard panel is showing —
// the skills the target roles ask for and the resume doesn't show, or, when it
// already covers them, how the resume itself could read better.
export type SkillsFocus = { status: 'ok' | 'no_target_roles' | 'no_resume'; kind: 'missing_skills' | 'resume_improvements' | null; headline: string; items: SkillFocusItem[]; target_roles: string[]; resume_filename: string | null; resume_updated_at: string | null; analysis: string | null }
export type CompanyCard = { id: string; name: string; domain?: string | null; website_url?: string | null; logo_url?: string | null; tier?: string | null; tier_label?: string | null; industry?: string | null; description?: string | null; hq_city?: string | null; hq_country?: string | null; office_cities: string[]; employee_count_range?: string | null; founded_year?: number | null; github_org?: string | null; careers_url?: string | null; tech_stack: string[]; open_roles: number; summary?: string | null; summary_source?: SummarySource | null; stage?: string | null; sector?: string | null; area?: string | null; geo_precision?: GeoPrecision | null; slug?: string | null }
// Where a company's one-line summary came from (services/company_map): shown beside it in small type.
export type SummarySource = 'curated' | 'site' | 'wikidata' | 'yc' | 'github_org' | 'llm'
// How well we know where a company is. `city` means no pin: it appears in the List only.
export type GeoPrecision = 'exact' | 'street' | 'area' | 'city'
export type CompanyDirectory = { companies: CompanyCard[]; total: number; offset: number; limit: number; city?: string | null }
export type FacetOption = { value: string; label: string; count: number }
export type CompanyFacets = { city?: string | null; cities: string[]; tiers: FacetOption[]; industries: Array<{ value: string; count: number }>; industries_total?: number; stages: FacetOption[]; sectors: FacetOption[]; areas: FacetOption[]; sizes: FacetOption[]; total: number }
export type GithubRepo = { name: string; full_name: string; description: string | null; html_url: string; language: string | null; stars: number; forks: number; topics: string[]; pushed_at: string | null }
export type GithubOrg = { org: string; name: string; html_url: string; avatar_url: string | null; description: string | null; blog: string | null; location: string | null; public_repos: number; followers: number; top_repos: GithubRepo[]; top_languages: string[] }
export type CompanyContact = { id: string; kind: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; notes?: string | null; source_url?: string | null; x_url?: string | null; github_url?: string | null; profile_url?: string | null; source_kind?: string | null; verified: boolean; is_curated: boolean; created_at?: string | null }
export type ContactCandidate = { kind: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; source_url?: string | null; source_title?: string | null; verified: boolean }
export type ContactDiscovery = { company?: string | null; careers_url?: string | null; candidates: ContactCandidate[]; queries: string[]; available: boolean; message?: string | null }
export type CompanyRole = { id: string; title: string; location?: string | null; is_remote?: boolean; seniority?: string | null; salary_raw?: string | null; skills: string[]; source_url?: string | null; posted_at?: string | null }
// A founder, leader or GitHub org member that a public source named (services/company_map/read.py::people_for). It always has `source_url`, and `verified` stays false. `linkedin_search_url` is a server-built people-search link, present only when no profile link is stored; the server never requests it.
export type CompanyPerson = CompanyContact & { linkedin_search_url?: string | null }
export type CompanyPeople = { founders: CompanyPerson[]; leaders: CompanyPerson[]; engineers: CompanyPerson[] }
// A "Find on LinkedIn" preset. The server builds it (domain/linkedin_links.py); the client neither builds nor requests one.
export type LinkedInLink = { label: string; url: string }
export type NewsCategory = 'funding' | 'acquisition' | 'launch' | 'other'
export type CompanyNewsItem = { id: string; url: string; title: string; source?: string | null; snippet?: string | null; published_at?: string | null; category: NewsCategory; round?: string | null; amount_text?: string | null; amount_usd?: number | null; investors: string[]; company_id?: string | null; company_name?: string | null }
export type CompanyDetail = CompanyCard & { linkedin_url?: string | null; github?: GithubOrg | null; tech_stack_curated: string[]; tech_stack_from_postings: string[]; culture_summary?: string | null; contacts: CompanyContact[]; jobs: CompanyRole[]; has_profile: boolean; address?: string | null; x_url?: string | null; last_funding_at?: string | null; employee_count?: number | null; is_bigger?: boolean; people: CompanyPeople; linkedin_links: LinkedInLink[]; news: CompanyNewsItem[] }
// The on-demand founder lookup. `available: false` carries the reason in `message`. A person found is stored, so the caller re-reads the company.
export type FounderDiscovery = { available: boolean; people: Array<{ name: string; kind: string; source_url: string; source_kind: string; title?: string | null }>; message?: string | null }
// GET /company-intel/map. Each row is positional, to keep the payload small (services/company_map/read.py::MAP_COLUMNS). A `city` row carries a deterministic approximate point near the city centre, so `lat`/`lng` are null only for a row that has none at all.
export type MapRow = [id: string, name: string, lat: number | null, lng: number | null, precision: GeoPrecision, tier: string | null, stage: string | null, sector: string | null, area: string | null, size: string | null, open_roles: number, raised_90d: boolean]
// `pinned` counts only exact, street and area rows; `approximate` counts the `city` rows placed near the centre (absent on an older API).
export type MapPayload = { generated_at: string; city: string; total: number; pinned: number; approximate?: number; rows: MapRow[] }
export type MapCompany = { id: string; name: string; lat: number | null; lng: number | null; precision: GeoPrecision; tier: string | null; stage: string | null; sector: string | null; area: string | null; size: string | null; openRoles: number; raised90d: boolean }
/** Must match services/company_map/read.py::MAP_COLUMNS. A row of any other length comes from a different contract. */
const MAP_ROW_LENGTH = 12
export function decodeMapRows(rows: MapRow[]): MapCompany[] {
  // A row that is not exactly 12 long would otherwise shift every field after the gap, so skip it.
  return rows
    .filter(row => Array.isArray(row) && row.length === MAP_ROW_LENGTH)
    .map(([id, name, lat, lng, precision, tier, stage, sector, area, size, openRoles, raised90d]) =>
      ({ id, name, lat, lng, precision, tier, stage, sector, area, size, openRoles, raised90d }))
}
/** A `city` row the server placed near the centre: on the map, but not a real location. */
export const isApproximate = (row: MapCompany): boolean => row.precision === 'city' && row.lat != null && row.lng != null
/** A row with a real position: exact, street or area precision (what `MapPayload.pinned` counts). */
export const isPinned = (row: MapCompany): boolean => row.precision !== 'city' && row.lat != null && row.lng != null
export type CompanyListFilters = { city?: string; tier?: string; industry?: string; q?: string; hasOpenRoles?: boolean; stage?: string; sector?: string; area?: string; size?: string; raisedWithinDays?: number; pinned?: boolean; limit?: number; offset?: number }
export type NewContact = { kind?: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; notes?: string | null; source_url?: string | null; verified?: boolean }
// A dated commitment on the tracker — the only thing on the overview the user
// cannot reschedule by ignoring it, so it leads the page.
export type AgendaItem = { application_id: string; job_id: string | null; role: string; company: string; status: string; scheduled_at: string; days_away: number; when_label: string }
export type ActivityItem = { application_id: string; role: string; company: string; from_status: string | null; to_status: string; note: string | null; created_at: string | null; days_ago: number | null }
export type BriefingAction = { label: string; href: string; style: string }
export type BriefingCard = { id: string; kind: string; title: string; detail: string; tone: string; actions: BriefingAction[]; meta: Record<string, unknown>; dismissible: boolean }
export type Briefing = { generated_at: string; headline: string; cards: BriefingCard[]; hidden_count: number; stats: { total_applications?: number; saved?: number; applied?: number; interviews?: number; onboarding_completed?: boolean } }
export type ChatJob = { id: string; title: string; company?: string | null; location?: string | null; is_remote?: boolean; skills?: string[]; source_url?: string | null; score?: number | null }
export type ChatReply = { intent: string; reply: string; jobs: ChatJob[]; data?: unknown; error?: string | null }
export type AgentMemory = { id: string; memory_type: string; content: string; metadata?: Record<string, unknown> | null; created_at?: string | null }
export type LearningTask = { skill_name: string; title: string; description?: string | null; resources?: string[] | null; priority?: number | null }
export type LearningPlan = { tasks: LearningTask[]; total: number }
// Outreach (services/outreach, api/routers/outreach.py). Each shape is what the route returns, built with explicit keys on the server.
export type OutreachChannelName = 'linkedin' | 'email' | 'x' | 'github' | 'call' | 'event' | 'other'
// A way to reach a person: a stored LinkedIn profile (direct) or a search link, or a mailto.
export type OutreachChannel = { channel: 'linkedin' | 'email' | 'x' | 'github'; url: string; label: string; direct: boolean; source_host: string | null }
export type PersonState = 'planned' | 'waiting' | 'replied' | 'cold' | 'dropped'
export type OutreachPerson = { id: string; contact_id: string | null; name: string; title: string | null; kind: string; state: PersonState; verified: boolean; user_saved: boolean; source_url: string | null; source_host: string | null; channels: OutreachChannel[]; first_sent_at: string | null; last_outbound_at: string | null; follow_ups_sent: number; next_follow_up_at: string | null; replied_at: string | null; draft: 'first' | 'follow_up' | null }
// A company contact the thread does not hold yet, ranked for the company's size. `reason` is a short phrase from the server.
export type SuggestedPerson = { contact_id: string; name: string; title: string | null; kind: string; verified: boolean; user_saved: boolean; source_host: string | null; reason: string; score: number }
export type OutreachTouch = { id: string; person_id: string | null; person_name: string | null; channel: OutreachChannelName; direction: 'out' | 'in'; kind: 'first' | 'follow_up' | 'reply' | 'other'; note: string | null; body: string | null; happened_at: string }
// The open-source snapshot for a company: good-first-issues, refreshed by the scheduled step, never on a page load.
export type IssueSnapshot = { org: string; status: string; total_count: number; issues: Array<{ repo: string; number: number; title: string; url: string; labels: string[]; updated_at: string | null; comments: number }>; fetched_at: string }
export type ThreadStage = 'planned' | 'waiting' | 'replied' | 'cold' | 'closed'
export type OutreachOutcome = 'referred' | 'interviewing' | 'offer' | 'not_now' | 'not_a_fit' | 'other'
export type OutreachSource = 'suggestion' | 'drawer' | 'list' | 'typed' | 'overview'
export type ThreadSummary = { id: string; company_id: string | null; company_name: string; company_summary: string | null; stage: ThreadStage; outcome: OutreachOutcome | null; people_count: number; lead: { name: string; state: PersonState } | null; due: boolean; next_due_at: string | null; last_touch_at: string | null; job: { id: string | null; title: string | null } | null; application: { id: string; status: string | null } | null; source: OutreachSource; created_at: string }
export type ThreadDetail = ThreadSummary & { note: string | null; next_step: string | null; next_step_at: string | null; closed_at: string | null; company: { stage: string | null; sector: string | null; area: string | null; last_funding_at: string | null; linkedin_url: string | null; github_org: string | null } | null; people: OutreachPerson[]; suggested_people: SuggestedPerson[]; touches: OutreachTouch[]; issues: IssueSnapshot | null; careers_url: string | null; linkedin_links: LinkedInLink[]; pacing_note: string | null }
export type DueItem = { thread_id: string; person_id: string | null; kind: 'follow_up' | 'next_step'; due_at: string; company_name: string; person_name: string | null; follow_up_number: number | null; last_outbound_at: string | null; next_step: string | null }
export type OutreachHome = { target: { daily: number; reached_today: number }; conversations_started: number; due: DueItem[]; threads: ThreadSummary[] }
export type Suggestion = { company_id: string; name: string; summary: string | null; area: string | null; stage: string | null; sector: string | null; score: number; reasons: string[]; top_person: { name: string; title: string | null; kind: string } | null }
export type Suggestions = { suggestions: Suggestion[]; profile_complete: boolean }
// One stored company matched by name, for "Add a company". Returned as a bare array.
export type OutreachCompanyMatch = { id: string; name: string; summary: string | null; area: string | null }
export type DraftFormat = 'linkedin_note' | 'linkedin_message' | 'email' | 'x_dm' | 'github_comment'
// `reason` is null for a model draft; otherwise the template was used and says why. `subject` is set only for email.
export type OutreachDraft = { format: DraftFormat; message: string; subject: string | null; generated: boolean; reason: 'daily_cap' | 'budget' | 'model_unavailable' | null; remaining_today: number; max_chars: number }
// The PATCH body. An absent key is left alone; `close` closes the thread with an outcome.
export type OutreachThreadPatch = { note?: string | null; next_step?: string | null; next_step_at?: string | null; job_id?: string | null; application_id?: string | null; close?: { outcome: OutreachOutcome }; reopen?: boolean }
// The daily pack (services/outreach/pack.py). An address says where it came from: saved by the user, found on a cited page,
// or guessed from the company's format (`learned_from` names the found address the format came from). A guess is never stored.
export type PackAddress = { value: string; basis: 'saved' | 'found' | 'guessed'; source_host: string | null; learned_from: string | null }
export type PackItemStatus = 'new' | 'sent' | 'skipped'
// `message` is null until it is written (POST .../write); `reason` says why a template was used instead of the model.
export type PackItem = { id: string; kind: 'first' | 'follow_up'; person: { name: string | null; title: string | null; kind: string | null }; channel: 'email' | 'linkedin'; address: PackAddress | null; linkedin_url: string | null; format: DraftFormat; subject: string | null; message: string | null; generated: boolean; reason: string | null; status: PackItemStatus; thread_id: string | null }
export type PackCompany = { company_id: string | null; name: string; summary: string | null; reasons: string[]; roles: Array<{ job_id: string; title: string }>; applied: boolean; items: PackItem[] }
export type OutreachPack = { day: string; companies: PackCompany[]; follow_ups: PackItem[]; counts: { companies: number; messages_ready: number } }

// Events (api/schemas/event_schemas.py): each type copies its server model key for key.
export type EventFormat = 'hackathon' | 'conference' | 'demo' | 'workshop' | 'talk' | 'mixer' | 'meetup' | 'hiring' | 'other'
export type EventSource = 'luma' | 'gdg' | 'eventbrite' | 'user'
export type EventSaveStatus = 'saved' | 'going'
export type EventCompanyRef = { id: string; name: string }
// What a list row and the drawer share. A null `ends_at` means start + 2 h.
type EventBase = { id: string; title: string; starts_at: string; ends_at: string | null; is_online: boolean; venue_name: string | null; area: string | null; lat: number | null; lng: number | null; geo_precision: 'exact' | 'area' | 'none' | null; is_free: boolean | null; organiser_name: string | null; format: EventFormat | null; topics: string[]; source: EventSource; url: string | null; my_status: EventSaveStatus | null; is_cancelled: boolean }
export type EventCard = EventBase & { companies: EventCompanyRef[] }
export type EventMy = { status: EventSaveStatus; note: string | null }
export type EventAlsoOn = { source: EventSource; url: string }
// A person the caller met at an event. Private to the caller.
export type EventContact = { id: string; event_id: string; name: string; title: string | null; company_name: string | null; company_id: string | null; linkedin_url: string | null; x_url: string | null; github_url: string | null; email: string | null; note: string | null; created_at: string | null; linkedin_search_url: string | null }
export type EventDetail = EventBase & { companies: Array<EventCompanyRef & { role: 'organiser' | 'host' }>; snippet: string | null; reasons: string[]; hosts: string[]; also_on: EventAlsoOn[]; my: EventMy | null; people: EventContact[] }
export type EventCounts = { upcoming: number; this_week: number; going: number }
export type EventSourceStatus = { source: 'luma' | 'gdg' | 'eventbrite'; last_read_at: string | null; status: string }
export type EventPage = { total: number; events: EventCard[]; counts: EventCounts; sources: EventSourceStatus[] }
// The writable fields of a person met. Only `name` is required when one is added.
type EventContactFields = { title?: string | null; company_name?: string | null; linkedin_url?: string | null; x_url?: string | null; github_url?: string | null; email?: string | null; note?: string | null }
export type EventContactIn = EventContactFields & { name: string }
export type EventContactPatch = EventContactFields & { name?: string }
export type ManualEventIn = { title: string; starts_at: string; ends_at?: string | null; venue_name?: string | null; is_online?: boolean; url?: string | null }
export type ImportReason = 'unrecognised' | 'luma_not_indexed' | 'eventbrite_off' | 'not_readable' | 'daily_cap' | 'not_in_bengaluru'
// POST /events/import: the event, or `needs_details` when the link did not resolve (the client then opens the manual form).
export type ImportResult = { event: EventCard } | { needs_details: true; reason: ImportReason }
// What Outreach reads (services/events/met.py): one person met, with the event it was met at.
export type MetAtEvent = { source_ref: string; name: string; title: string | null; company_name: string | null; company_id: string | null; linkedin_url: string | null; x_url: string | null; github_url: string | null; email: string | null; note: string | null; event_id: string; event_title: string; event_url: string; met_on: string; created_at: string; context_line: string }
export type EventListParams = { when?: 'today' | 'week' | 'weekend' | 'month'; from?: string; to?: string; mode?: 'in_person' | 'online' | 'any'; topic?: string; format?: EventFormat; hidePaid?: boolean; saved?: 'all' | 'saved' | 'going'; companyId?: string; q?: string; limit?: number; offset?: number }

// Practice (services/practice, api/routers/practice.py). A question is a pointer: the server never stores its statement, and the page links out to leetcode.com.
export type PracticeFeel = 'easy' | 'right' | 'hard'
export type PracticeQuestion = { id: number; slug: string; title: string; difficulty: 'easy' | 'medium' | 'hard'; topics: string[]; url: string }
export type Contest = { starts_at: string; ends_at: string; show: boolean; live: boolean; url: string }
export type PracticeToday = { day: string; catalogue: 'ok' | 'empty'; catalogue_size: number; track: string; track_label: string; question: PracticeQuestion | null; reason: string; relaxed: string[]; done_at: string | null; feel: PracticeFeel | null; swaps_left: number; leetcode_daily_url: string; contest: Contest }
export type PracticeHistory = { days: Array<{ day: string; done: boolean; difficulty: string | null; topic: string | null }>; counts: { this_month: number; last_30: number; longest_run: number }; topics: Array<{ topic: string; count: number }> }
export type ChallengeBrief = { slug: string; title: string; track: string; hours: 24 | 48; summary: string; context: string[]; must_have: string[]; nice_to_have: string[]; constraints: string[]; deliverables: string[]; judge_focus: string[]; bengaluru_context: string }
export type AttemptStatus = 'started' | 'submitted' | 'queued' | 'judged' | 'rejected' | 'judge_failed' | 'missed'
export type Writeup = { decisions: string; tradeoffs: string; ai_usage: string; next_steps: string }
export type Attempt = { status: AttemptStatus; started_at: string; deadline_at: string; proof_code: string; repo_url: string | null; writeup: Writeup | null; video_url: string | null; submitted_at: string | null; editable_until: string; total: number | null; held: boolean }
export type Challenge = { slug: string; opens_at: string; closes_at: string; last_start_at: string; brief: ChallengeBrief; attempt: Attempt | null }
export type ChallengesOverview = { current: Challenge | null; next_opens_at: string | null; past: Array<{ slug: string; title: string; track: string; opens_at: string; status: AttemptStatus | null; total: number | null; held: boolean }> }
export type Criterion = 'thinking' | 'tradeoffs' | 'ai_usage' | 'build_process' | 'meets_brief' | 'code_quality'
// none: nothing judged yet · open: the form is offered · closed: the seven days are over · reviewed: a person decided this result · pending / done: an appeal in progress / read · gave_up / refused: the second reading could not be made (the first score stands)
export type AppealState = 'none' | 'open' | 'closed' | 'reviewed' | 'pending' | 'done' | 'gave_up' | 'refused'
export type AttemptResult = { status: AttemptStatus; held: boolean; reject_reason: string | null; total: number | null; scores: Record<Criterion, number> | null; weights: Record<Criterion, number>; notes: (Record<Criterion, string> & { strengths: string[]; improvements: string[]; summary: string }) | null; signals: Record<string, number | boolean> | null; snapshot_sha: string | null; snapshot_url: string | null; appeal: { available: boolean; used: boolean; until: string | null; state: AppealState }; rejudged: boolean }
export type BoardRow = { rank: number; name: string; total: number; hours_used: number | null; repo_url?: string | null; you?: boolean }
export type ChallengeBoard = { provisional: boolean; total_entries: number; queued: number; held: number; rows: BoardRow[]; me: BoardRow | null }
export type OverallBoard = { window_challenges: number; rows: Array<{ rank: number; name: string; score: number; entries: number; you?: boolean }>; me: { rank: number; name: string; score: number; entries: number } | null }
export type PracticeSettings = { display_name: string | null; show_repo: boolean; contest_reminder: boolean; handle: string }

/** A refusal from the API. `status` lets a caller tell "not found" or "conflict" from a failure; `message` is the server's own words. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function token() { return typeof window === 'undefined' ? null : localStorage.getItem(TOKEN_KEY) }

/**
 * Read the `exp` claim without verifying the signature.
 *
 * The server is the only thing that decides whether a token is valid; this is
 * purely so the UI does not render a whole authenticated page from a token it
 * can already see has expired. Tokens last 7 days, so before this check a
 * returning user got the full dashboard shell followed by a wall of failed
 * requests instead of a redirect to sign in.
 */
function tokenExpiry(raw: string): number | null {
  try {
    const payload = raw.split('.')[1]
    if (!payload) return null
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const exp = (JSON.parse(json) as { exp?: number }).exp
    return typeof exp === 'number' ? exp * 1000 : null
  } catch { return null }
}

export function signedIn() {
  const raw = token()
  if (!raw) return false
  const expiresAt = tokenExpiry(raw)
  // A token we cannot parse is left to the server to reject.
  if (expiresAt !== null && expiresAt <= Date.now()) { signOut(); return false }
  return true
}
export function currentEmail() { return typeof window === 'undefined' ? null : localStorage.getItem(EMAIL_KEY) }
export function signOut() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); clearPersistedState() }

/** The one path every API call takes: auth header, cold-start message, 401 and 429 handling. Resolves to an ok response. */
async function send(path: string, options: RequestInit = {}, authenticated = false): Promise<Response> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (authenticated) {
    const accessToken = token()
    if (!accessToken) throw new Error('Please sign in to use this feature.')
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers })
  } catch (err) {
    // "Failed to fetch" during a cold start reads as a broken app.
    if (apiIsWaking()) throw new Error(WAKING_MESSAGE)
    throw err
  }
  if ((response.status === 502 || response.status === 503) && apiIsWaking()) throw new Error(WAKING_MESSAGE)
  if (response.status < 500) markApiAlive()
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: unknown; message?: string }
    if (response.status === 401) {
      // Clearing the token was not enough: the page had already rendered, so
      // the user sat looking at a broken screen. Send them to sign in and
      // remember where they were.
      signOut()
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`
      }
      throw new Error('Your session has expired. Please sign in again.')
    }
    if (response.status === 429) {
      throw new Error('You are going a bit fast for us — wait a moment and try again.')
    }
    throw new ApiError(errorMessage(body.detail) || body.message || `Request failed (${response.status})`, response.status)
  }
  return response
}

async function request<T>(path: string, options: RequestInit = {}, authenticated = false): Promise<T> {
  const response = await send(path, options, authenticated)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

/**
 * FastAPI sends `detail` as a string for HTTPException but as a list of
 * `{loc, msg}` objects for a 422 — which `new Error(list)` rendered as
 * "[object Object]". Pydantic prefixes validator messages with "Value error, ".
 */
function errorMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => (item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : ''))
      .map(message => message.replace(/^Value error, /, ''))
      .filter(Boolean)
      .join(' ')
  }
  return ''
}

/** GET /events/{id}/ics as a Blob. A plain link cannot send the Bearer token the endpoint needs, so this goes through `send`. */
function eventIcs(id: string): Promise<Blob> {
  return send(`/api/v1/events/${encodeURIComponent(id)}/ics`, {}, true).then(response => response.blob())
}

/** Saves an event's .ics file. The Blob reaches the browser through an object URL, which is revoked once the download has started. */
async function saveEventIcs(id: string, filename = 'event.ics'): Promise<void> {
  const url = URL.createObjectURL(await eventIcs(id))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const api = {
  auth: {
    async login(email: string, password: string) {
      const result = await request<{ access_token: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
      localStorage.setItem(TOKEN_KEY, result.access_token); localStorage.setItem(EMAIL_KEY, email)
      return result
    },
    signup: (email: string, password: string) => request('/api/auth/signup', { method: 'POST', body: JSON.stringify({ email, password }) }),
  },
  dashboard: () => request<Record<string, unknown>>('/api/v1/dashboard/overview', {}, true),
  search: {
    semantic: (query: string, platforms: string[] = [], includeClosed = false) => request<{ results: Job[]; total_found: number; summary?: string; sourcing?: Sourcing; closed_hidden?: number }>('/api/v1/search/semantic', { method: 'POST', body: JSON.stringify({ query, platforms: platforms.length ? platforms : undefined, include_closed: includeClosed || undefined, limit: 30 }) }),
    structured: (query: string, filters: { location?: string; remote?: boolean; experience?: string; platforms?: string[] } = {}) => request<{ jobs: Job[]; total: number }>('/api/v1/search/structured', { method: 'POST', body: JSON.stringify({ query, location: filters.location || undefined, is_remote: filters.remote || undefined, experience: filters.experience || undefined, platforms: filters.platforms?.length ? filters.platforms : undefined, india_only: true, limit: 30 }) }),
  },
  applications: {
    list: () => request<{ applications: Application[]; total: number }>('/api/v1/applications/', {}, true),
    create: (job_id: string, status = 'saved', notes?: string) => request<Application>('/api/v1/applications/', { method: 'POST', body: JSON.stringify({ job_id, status, notes }) }, true),
    update: (id: string, payload: { status?: string; notes?: string }) => request<Application>(`/api/v1/applications/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, true),
    remove: (id: string) => request<void>(`/api/v1/applications/${id}`, { method: 'DELETE' }, true),
  },
  profile: {
    get: () => request<{ onboarding_completed: boolean; profile: Record<string, unknown> | null }>('/api/v1/profile/', {}, true),
    save: (payload: Record<string, unknown>) => request<{ profile: Record<string, unknown> }>('/api/v1/profile/', { method: 'POST', body: JSON.stringify(payload) }, true),
  },
  resumes: {
    me: () => request<SavedResume | null>('/api/v1/resumes/me', {}, true),
    targetJobs: () => request<TargetJob[]>('/api/v1/resumes/target-jobs', {}, true),
    extractText: (file: File) => { const form = new FormData(); form.append('resume_file', file); return request<SavedResume>('/api/v1/resumes/extract-text', { method: 'POST', body: form }, true) },
    analyze: (payload: { resume_text: string; job_description: string; job_title?: string }) => request<AtsResult>('/api/v1/resumes/analyze', { method: 'POST', body: JSON.stringify(payload) }, true),
    tailor: (payload: { resume_text: string; job_description: string; job_title?: string }) => request<TailorResult>('/api/v1/resumes/tailor', { method: 'POST', body: JSON.stringify(payload) }, true),
    coverLetter: (payload: { resume_text: string; job_description: string; job_title: string; company: string; tone?: string }) => request<{ content: string; tone: string }>('/api/v1/resumes/cover-letter', { method: 'POST', body: JSON.stringify(payload) }, true),
    gaps: (payload: Record<string, unknown>) => request<Record<string, unknown>>('/api/v1/resumes/gaps', { method: 'POST', body: JSON.stringify(payload) }, true),
    document: {
      get: () => request<ResumeDocument>('/api/v1/resumes/document', {}, true),
      save: (document: ResumeDocument) => request<ResumeDocument>('/api/v1/resumes/document', { method: 'PUT', body: JSON.stringify({ document }) }, true),
      compile: (document: ResumeDocument) => request<CompiledResume>('/api/v1/resumes/document/compile', { method: 'POST', body: JSON.stringify({ document }) }, true),
    },
  },
  roles: {
    dossier: (jobId: string) => request<Dossier>(`/api/v1/roles/${encodeURIComponent(jobId)}/dossier`, {}, true),
    // Returns a draft for the user to edit and send themselves; generated=false means a template was returned, and `reason` says why (null for a model draft).
    referralAsk: (jobId: string, payload: { recipient_name?: string; recipient_title?: string; relationship?: string }) => request<{ message: string; generated: boolean; reason: 'daily_cap' | 'model_unavailable' | null }>(`/api/v1/roles/${encodeURIComponent(jobId)}/referral-ask`, { method: 'POST', body: JSON.stringify(payload) }, true),
  },
  interview: {
    // track is the round style (technical | coding | system_design | behavioral); topic is free text.
    // jobId anchors the session to a posting ("Prepare for this role"): the interviewer is given that role's title, skills and description.
    start: (track: string, topic: string, difficulty: string, voice_mode = false, jobId?: string | null) => request<{ session_id: string; question: string; question_index: number; max_questions: number; agent_state: InterviewState }>('/api/v1/interview/sessions/start', { method: 'POST', body: JSON.stringify({ track, topic, difficulty, voice_mode, job_id: jobId || undefined }) }, true),
    topics: () => request<{ for_you: string[]; recent: string[]; popular: string[] }>('/api/v1/interview/topics', {}, true),
    answer: (session_id: string, answer: string, agent_state: InterviewState) => request<{ question: string; question_index: number; is_followup: boolean; max_questions: number; session_complete: boolean; agent_state: InterviewState; score: InterviewScore }>('/api/v1/interview/sessions/answer', { method: 'POST', body: JSON.stringify({ session_id, answer, agent_state }) }, true),
    end: (session_id: string, agent_state: InterviewState) => request<{ closing_message: string; final_score: { total_score: number; correctness: number; clarity: number; depth: number; questions_answered: number } }>('/api/v1/interview/sessions/end', { method: 'POST', body: JSON.stringify({ session_id, agent_state }) }, true),
    history: () => request<{ sessions: Array<{ id: string; track: string; topic?: string | null; difficulty: string; total_score?: number; completed: boolean; created_at: string }> }>('/api/v1/interview/sessions/history', {}, true),
    // Sent as multipart so the browser's MediaRecorder blob reaches Groq Whisper
    // untouched; provider='browser_fallback' means the caller should use its own
    // Web Speech transcript instead.
    transcribe: (blob: Blob, filename = 'answer.webm') => { const form = new FormData(); form.append('audio', new File([blob], filename, { type: blob.type || 'audio/webm' })); return request<{ transcript: string; confidence: number | null; provider: string }>('/api/v1/interview/voice/transcribe', { method: 'POST', body: form }, true) },
  },
  career: { weaknesses: () => request<Record<string, unknown>>('/api/v1/career/weaknesses', {}, true), recommend: () => request<LearningPlan>('/api/v1/career/recommend', { method: 'POST', body: JSON.stringify({ persist: true }) }, true) },
  company: {
    directory: (filters: CompanyListFilters = {}) => {
      const params = new URLSearchParams()
      if (filters.city) params.set('city', filters.city)
      if (filters.tier) params.set('tier', filters.tier)
      if (filters.industry) params.set('industry', filters.industry)
      if (filters.q) params.set('q', filters.q)
      if (filters.hasOpenRoles) params.set('has_open_roles', 'true')
      if (filters.stage) params.set('stage', filters.stage)
      if (filters.sector) params.set('sector', filters.sector)
      if (filters.area) params.set('area', filters.area)
      if (filters.size) params.set('size', filters.size)
      if (filters.raisedWithinDays) params.set('raised_within_days', String(filters.raisedWithinDays))
      if (filters.pinned !== undefined) params.set('pinned', String(filters.pinned))
      if (filters.limit) params.set('limit', String(filters.limit))
      if (filters.offset) params.set('offset', String(filters.offset))
      return request<CompanyDirectory>(`/api/v1/company-intel/?${params.toString()}`, {}, true)
    },
    // Every listed company in the city as positional rows. The same for every user, so the server caches it for an hour.
    map: (city = 'Bengaluru') => request<MapPayload>(`/api/v1/company-intel/map?city=${encodeURIComponent(city)}`, {}, true),
    // Ids of companies with an open role matching the user's target role. Empty without one; the map works without it.
    mapForMe: () => request<{ hiring_for_you: string[] }>('/api/v1/company-intel/map/for-me', {}, true),
    news: (limit = 10, category?: NewsCategory) => request<CompanyNewsItem[]>(`/api/v1/company-intel/news?limit=${limit}${category ? `&category=${category}` : ''}`, {}, true),
    facets: (city?: string) => request<CompanyFacets>(`/api/v1/company-intel/facets${city ? `?city=${encodeURIComponent(city)}` : ''}`, {}, true),
    detail: (id: string) => request<CompanyDetail>(`/api/v1/company-intel/${encodeURIComponent(id)}`, {}, true),
    search: (name: string) => request<Record<string, unknown>>(`/api/v1/company-intel/resolve?name=${encodeURIComponent(name)}`, {}, true),
    discoverContacts: (id: string) => request<ContactDiscovery>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts/discover`, { method: 'POST' }, true),
    discoverFounders: (id: string) => request<FounderDiscovery>(`/api/v1/company-intel/${encodeURIComponent(id)}/people/discover`, { method: 'POST' }, true),
    addContact: (id: string, payload: NewContact) => request<CompanyContact>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts`, { method: 'POST', body: JSON.stringify(payload) }, true),
    removeContact: (id: string, contactId: string) => request<void>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts/${encodeURIComponent(contactId)}`, { method: 'DELETE' }, true),
  },
  agent: {
    nextAction: () => request<Record<string, unknown>>('/api/v1/agent/next-action', {}, true),
    briefing: () => request<Briefing>('/api/v1/agent/briefing', {}, true),
    starters: () => request<{ starters: string[] }>('/api/v1/agent/starters', {}, true),
    chat: (message: string, history: Array<{ role: string; content: string }> = []) => request<ChatReply>('/api/v1/agent/chat', { method: 'POST', body: JSON.stringify({ message, history: history.slice(-8) }) }, true),
    dismissCard: (card_id: string, snooze_days?: number) => request<void>('/api/v1/agent/cards/dismiss', { method: 'POST', body: JSON.stringify({ card_id, snooze_days }) }, true),
    memories: () => request<{ memories: AgentMemory[]; count: number }>('/api/v1/agent/memories', {}, true),
    remember: (content: string, memory_type = 'preference') => request<AgentMemory>('/api/v1/agent/memories', { method: 'POST', body: JSON.stringify({ content, memory_type }) }, true),
    forget: (id: string) => request<void>(`/api/v1/agent/memories/${encodeURIComponent(id)}`, { method: 'DELETE' }, true),
  },
  outreach: {
    // The page: today's target, what is due, and every thread.
    home: () => request<OutreachHome>('/api/v1/outreach', {}, true),
    settings: {
      get: () => request<{ daily_target: number }>('/api/v1/outreach/settings', {}, true),
      save: (daily_target: number) => request<{ daily_target: number }>('/api/v1/outreach/settings', { method: 'PUT', body: JSON.stringify({ daily_target }) }, true),
    },
    // Ranked from stored facts; no model. `limit` must be 1 to 25 (the server answers anything else with a 422).
    suggestions: (limit = 10) => request<Suggestions>(`/api/v1/outreach/suggestions?limit=${limit}`, {}, true),
    dismissSuggestion: (companyId: string) => request<void>(`/api/v1/outreach/suggestions/${encodeURIComponent(companyId)}/dismiss`, { method: 'POST' }, true),
    searchCompanies: (q: string) => request<OutreachCompanyMatch[]>(`/api/v1/outreach/companies/search?q=${encodeURIComponent(q)}`, {}, true),
    // The caller's thread with this company (open or closed), or null.
    forCompany: (companyId: string) => request<{ thread: ThreadSummary | null }>(`/api/v1/outreach/companies/${encodeURIComponent(companyId)}`, {}, true),
    // 201 when a thread is opened, 200 with the existing one when it was already there (`created` says which).
    create: (payload: { company_id?: string; company_name?: string; job_id?: string; source: OutreachSource }) => request<{ thread: ThreadSummary; created: boolean }>('/api/v1/outreach/threads', { method: 'POST', body: JSON.stringify(payload) }, true),
    thread: (id: string) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}`, {}, true),
    update: (id: string, payload: OutreachThreadPatch) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }, true),
    remove: (id: string) => request<void>(`/api/v1/outreach/threads/${encodeURIComponent(id)}`, { method: 'DELETE' }, true),
    addPerson: (id: string, payload: { contact_id?: string | null; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null }) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}/people`, { method: 'POST', body: JSON.stringify(payload) }, true),
    // Snooze a follow-up by 1 to 14 days, or drop the person. Exactly one of the two.
    updatePerson: (id: string, personId: string, payload: { snooze_days?: number; state?: 'dropped' }) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}/people/${encodeURIComponent(personId)}`, { method: 'PATCH', body: JSON.stringify(payload) }, true),
    removePerson: (id: string, personId: string) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}/people/${encodeURIComponent(personId)}`, { method: 'DELETE' }, true),
    // Records what the user did. Nothing is sent; a closed thread takes touches too.
    touch: (id: string, payload: { person_id?: string | null; channel: OutreachChannelName; direction: 'out' | 'in'; note?: string | null; body?: string | null; happened_at?: string | null; next_step?: string | null; next_step_at?: string | null }) => request<ThreadDetail>(`/api/v1/outreach/threads/${encodeURIComponent(id)}/touches`, { method: 'POST', body: JSON.stringify(payload) }, true),
    // The only outreach call that may use the model. Nothing is sent or stored.
    draft: (id: string, payload: { person_id?: string; format: DraftFormat; relationship?: string; issue_url?: string }) => request<OutreachDraft>(`/api/v1/outreach/threads/${encodeURIComponent(id)}/draft`, { method: 'POST', body: JSON.stringify(payload) }, true),
    // Today's pack. The first read of the IST day builds it; no read calls the model, and nothing is ever sent for the user.
    pack: {
      get: () => request<OutreachPack>('/api/v1/outreach/pack', {}, true),
      seen: (itemIds: string[]) => request<void>('/api/v1/outreach/pack/seen', { method: 'POST', body: JSON.stringify({ item_ids: itemIds }) }, true),
      // One model call for all of a company's unwritten messages; written ones come back as they are.
      writeCompany: (companyId: string) => request<{ items: PackItem[] }>(`/api/v1/outreach/pack/companies/${encodeURIComponent(companyId)}/write`, { method: 'POST' }, true),
      writeItem: (itemId: string) => request<{ item: PackItem }>(`/api/v1/outreach/pack/items/${encodeURIComponent(itemId)}/write`, { method: 'POST' }, true),
      // Records what the user says they sent (the text they edited), opening the thread on the first one.
      sent: (itemId: string, payload: { message: string; subject: string | null }) => request<{ item: PackItem }>(`/api/v1/outreach/pack/items/${encodeURIComponent(itemId)}/sent`, { method: 'POST', body: JSON.stringify(payload) }, true),
      skip: (itemId: string) => request<void>(`/api/v1/outreach/pack/items/${encodeURIComponent(itemId)}/skip`, { method: 'POST' }, true),
      applied: (companyId: string, jobId: string) => request<{ application_id: string; applied: boolean }>(`/api/v1/outreach/pack/companies/${encodeURIComponent(companyId)}/applied`, { method: 'POST', body: JSON.stringify({ job_id: jobId }) }, true),
    },
  },
  events: {
    list: (params: EventListParams = {}) => {
      const query = new URLSearchParams()
      if (params.when) query.set('when', params.when)
      if (params.from) query.set('from', params.from)
      if (params.to) query.set('to', params.to)
      if (params.mode) query.set('mode', params.mode)
      if (params.topic) query.set('topic', params.topic)
      if (params.format) query.set('format', params.format)
      if (params.hidePaid) query.set('hide_paid', 'true')
      if (params.saved) query.set('saved', params.saved)
      if (params.companyId) query.set('company_id', params.companyId)
      if (params.q) query.set('q', params.q)
      if (params.limit) query.set('limit', String(params.limit))
      if (params.offset) query.set('offset', String(params.offset))
      return request<EventPage>(`/api/v1/events?${query.toString()}`, {}, true)
    },
    detail: (id: string) => request<EventDetail>(`/api/v1/events/${encodeURIComponent(id)}`, {}, true),
    // An omitted note leaves the note the user already has; an empty one clears it.
    save: (id: string, status: EventSaveStatus, note?: string) => request<EventMy>(`/api/v1/events/${encodeURIComponent(id)}/save`, { method: 'PUT', body: JSON.stringify({ status, note }) }, true),
    unsave: (id: string) => request<void>(`/api/v1/events/${encodeURIComponent(id)}/save`, { method: 'DELETE' }, true),
    ics: eventIcs,
    downloadIcs: saveEventIcs,
    importLink: (url: string) => request<ImportResult>('/api/v1/events/import', { method: 'POST', body: JSON.stringify({ url }) }, true),
    manual: (payload: ManualEventIn) => request<EventDetail>('/api/v1/events/manual', { method: 'POST', body: JSON.stringify(payload) }, true),
    // Only an event the caller added by hand; any other event is a 404.
    deleteEvent: (id: string) => request<void>(`/api/v1/events/${encodeURIComponent(id)}`, { method: 'DELETE' }, true),
    people: (id: string) => request<EventContact[]>(`/api/v1/events/${encodeURIComponent(id)}/people`, {}, true),
    addPerson: (id: string, payload: EventContactIn) => request<EventContact>(`/api/v1/events/${encodeURIComponent(id)}/people`, { method: 'POST', body: JSON.stringify(payload) }, true),
    updatePerson: (personId: string, payload: EventContactPatch) => request<EventContact>(`/api/v1/events/people/${encodeURIComponent(personId)}`, { method: 'PATCH', body: JSON.stringify(payload) }, true),
    deletePerson: (personId: string) => request<void>(`/api/v1/events/people/${encodeURIComponent(personId)}`, { method: 'DELETE' }, true),
    // Oldest first. `since` is inclusive, `limit` is 1 to 500 (default 100); Outreach pages through them.
    peopleMet: (since?: string, limit?: number) => {
      const query = new URLSearchParams()
      if (since) query.set('since', since)
      if (limit) query.set('limit', String(limit))
      const qs = query.toString()
      return request<MetAtEvent[]>(`/api/v1/events/people-met${qs ? `?${qs}` : ''}`, {}, true)
    },
  },
  practice: {
    today: () => request<PracticeToday>('/api/v1/practice/today', {}, true),
    // Marking done is idempotent. The one-tap rating is optional and rides in the same request.
    done: (feel?: PracticeFeel) => request<PracticeToday>('/api/v1/practice/today/done', { method: 'POST', body: feel ? JSON.stringify({ feel }) : undefined }, true),
    feel: (feel: PracticeFeel) => request<PracticeToday>('/api/v1/practice/today/feel', { method: 'PATCH', body: JSON.stringify({ feel }) }, true),
    undo: () => request<PracticeToday>('/api/v1/practice/today/done', { method: 'DELETE' }, true),
    swap: () => request<PracticeToday>('/api/v1/practice/today/swap', { method: 'POST' }, true),
    history: (weeks = 12) => request<PracticeHistory>(`/api/v1/practice/history?weeks=${weeks}`, {}, true),
    challenges: () => request<ChallengesOverview>('/api/v1/practice/challenges', {}, true),
    challenge: (slug: string) => request<Challenge>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}`, {}, true),
    start: (slug: string) => request<Challenge>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}/start`, { method: 'POST' }, true),
    submit: (slug: string, payload: { repo_url: string; writeup: Writeup; video_url?: string | null }) => request<Challenge>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}/submission`, { method: 'PUT', body: JSON.stringify(payload) }, true),
    result: (slug: string) => request<AttemptResult>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}/result`, {}, true),
    appeal: (slug: string, reason: string) => request<AttemptResult>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}/appeal`, { method: 'POST', body: JSON.stringify({ reason }) }, true),
    board: (slug: string) => request<ChallengeBoard>(`/api/v1/practice/challenges/${encodeURIComponent(slug)}/leaderboard`, {}, true),
    overall: () => request<OverallBoard>('/api/v1/practice/leaderboard', {}, true),
    settings: () => request<PracticeSettings>('/api/v1/practice/settings', {}, true),
    saveSettings: (payload: Pick<PracticeSettings, 'display_name' | 'show_repo' | 'contest_reminder'>) => request<PracticeSettings>('/api/v1/practice/settings', { method: 'PUT', body: JSON.stringify(payload) }, true),
  },
}
