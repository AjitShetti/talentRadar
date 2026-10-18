// Pure display helpers for /outreach, the thread drawer and the company-drawer block.
// No fetching, no storage, no sending: a channel action only builds a link the
// browser opens in a new tab. Types come from lib/api.ts, which mirrors the server.
import type { DraftFormat, OutreachChannelName, OutreachOutcome, OutreachPerson, PersonState, ThreadStage, ThreadSummary } from '@/lib/api'
import { externalHref } from '@/lib/safe-url'

export const STAGE_LABEL: Record<ThreadStage, string> = {
  planned: 'Not started',
  waiting: 'Waiting',
  replied: 'Replied',
  cold: 'Cold',
  closed: 'Closed',
}

export const STAGE_TABS = ['all', 'planned', 'waiting', 'replied', 'cold', 'closed'] as const

export const KIND_LABEL: Record<string, string> = {
  founder: 'Founder',
  leader: 'Leadership',
  engineer: 'Engineer',
  recruiter: 'Recruiter',
  referral: 'Someone you know',
  careers_inbox: 'Careers inbox',
  other: 'Contact',
}

export type OutreachTone = 'due' | 'replied' | 'waiting' | 'idle'

// The clock's two constants, as in domain/outreach.py. The server already sends the
// derived state, so these only keep a page that has been left open honest.
export const MAX_FOLLOW_UPS = 2
const COLD_AFTER_DAYS = 10
const DAY_MS = 24 * 60 * 60 * 1000

// "Today" is a calendar day in India, whatever timezone the browser is in.
const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// A timestamp or a YYYY-MM-DD date to epoch ms. A date-time with no zone is read as UTC,
// which is what the server means by it. NaN when it is not a date at all.
function toMs(iso: string): number {
  return Date.parse(/T[\d:.]+$/.test(iso) ? `${iso}Z` : iso)
}

/** "10 Oct", the day it is in India at `iso`. Empty when `iso` is not a date. */
export function dayMonth(iso: string): string {
  const ms = toMs(iso)
  if (Number.isNaN(ms)) return ''
  const day = new Date(ms + IST_OFFSET_MS)
  return `${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`
}

/** "10 OCT", the mono stamp on a row. */
export function stamp(iso: string): string {
  return dayMonth(iso).toUpperCase()
}

// waiting turns cold once both nudges have gone unanswered for COLD_AFTER_DAYS, as the server derives it.
function shownState(p: OutreachPerson, now: Date): PersonState {
  if (p.state !== 'waiting' || p.follow_ups_sent < MAX_FOLLOW_UPS || !p.last_outbound_at) return p.state
  const last = toMs(p.last_outbound_at)
  return !Number.isNaN(last) && now.getTime() >= last + COLD_AFTER_DAYS * DAY_MS ? 'cold' : p.state
}

/** The lamp for a thread row. Amber only when something is owed. */
export function threadTone(t: ThreadSummary): OutreachTone {
  if (t.due) return 'due'
  if (t.stage === 'replied') return 'replied'
  if (t.stage === 'waiting') return 'waiting'
  return 'idle'
}

/** The lamp for a person: due once a waiting person's follow-up date has passed. */
export function personTone(p: OutreachPerson, now: Date = new Date()): OutreachTone {
  const state = shownState(p, now)
  if (state === 'replied') return 'replied'
  if (state !== 'waiting') return 'idle'
  const next = p.next_follow_up_at ? toMs(p.next_follow_up_at) : Number.NaN
  return !Number.isNaN(next) && next <= now.getTime() ? 'due' : 'waiting'
}

/** The one line under a person: where they stand and what happens next. */
export function personLine(p: OutreachPerson, now: Date = new Date()): string {
  switch (shownState(p, now)) {
    case 'planned':
      return 'Not written to yet'
    case 'waiting': {
      const wrote = p.first_sent_at ? `Wrote ${dayMonth(p.first_sent_at)}` : 'Wrote'
      if (!p.next_follow_up_at) return `${wrote} · no reply after ${MAX_FOLLOW_UPS} follow-ups`
      return `${wrote} · follow-up ${p.follow_ups_sent + 1} of ${MAX_FOLLOW_UPS} due ${dayMonth(p.next_follow_up_at)}`
    }
    case 'replied':
      return p.replied_at ? `Replied ${dayMonth(p.replied_at)}` : 'Replied'
    case 'cold':
      return 'No reply — resting'
    case 'dropped':
      return 'Taken out of the plan'
  }
}

/**
 * A mailto: link that opens the user's own mail client with the draft in it.
 * Nothing is sent from here. The address is encoded too, so a stored value
 * cannot add a recipient or a header with "?", "&" or ",".
 */
export function mailtoHref(email: string, subject: string | null, body: string): string {
  const to = encodeURIComponent(email.trim()).replace(/%40/g, '@')
  const parts: string[] = []
  if (subject) parts.push(`subject=${encodeURIComponent(subject)}`)
  parts.push(`body=${encodeURIComponent(body)}`)
  return `mailto:${to}?${parts.join('&')}`
}

/** The button label for a format. The app copies and opens; the user sends. */
export function channelActionLabel(fmt: DraftFormat): string {
  switch (fmt) {
    case 'linkedin_note':
    case 'linkedin_message':
      return 'Copy and open LinkedIn'
    case 'email':
      return 'Open in email'
    case 'x_dm':
      return 'Copy and open X'
    case 'github_comment':
      return 'Copy and open the issue'
  }
}

/** The two letters on a row's mark: the first letters of the first two words, or the first two letters of one word. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (!words.length) return '?'
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)
  return letters.toUpperCase()
}

// Which thread the /outreach page has open, and what the drawer shows first.
// It lives in the URL (?thread=&person=&draft=1 or &reply=1) so the Overview can deep-link into it.
export type ThreadTarget = { threadId: string; personId: string | null; mode: 'draft' | 'reply' | null }

/** The /outreach URL that opens `target`. */
export function threadHref(target: ThreadTarget): string {
  const q = new URLSearchParams({ thread: target.threadId })
  if (target.personId) q.set('person', target.personId)
  if (target.mode === 'draft') q.set('draft', '1')
  if (target.mode === 'reply') q.set('reply', '1')
  return `/outreach?${q.toString()}`
}

/** The inverse of threadHref: null when the URL names no thread. */
export function parseThreadTarget(params: { get(name: string): string | null }): ThreadTarget | null {
  const threadId = params.get('thread')
  if (!threadId) return null
  return {
    threadId,
    personId: params.get('person') || null,
    mode: params.get('draft') === '1' ? 'draft' : params.get('reply') === '1' ? 'reply' : null,
  }
}

// The server's bounds on free text (domain/outreach.py), so a field can say how much is left.
export const NOTE_MAX = 500
export const NEXT_STEP_MAX = 140
export const BODY_MAX = 2000

// Drafts a user may write in one IST day (PER_USER_DAILY_DRAFTS). The referral ask counts against the same cap.
export const DAILY_DRAFTS = 20

export const OUTCOMES: ReadonlyArray<{ value: OutreachOutcome; label: string }> = [
  { value: 'referred', label: 'Got a referral' },
  { value: 'interviewing', label: 'Interviewing' },
  { value: 'offer', label: 'Offer' },
  { value: 'not_now', label: 'Not now' },
  { value: 'not_a_fit', label: 'Not a fit' },
  { value: 'other', label: 'Other' },
]

export const CHANNEL_LABEL: Record<OutreachChannelName, string> = {
  linkedin: 'LinkedIn',
  email: 'Email',
  x: 'X',
  github: 'GitHub',
  call: 'Call',
  event: 'Event',
  other: 'Other',
}

// Company codes as the map names them. The map page keeps its own copy, which a page file cannot export.
const COMPANY_STAGE_LABEL: Record<string, string> = {
  bootstrapped: 'Bootstrapped', pre_seed: 'Pre-seed', seed: 'Seed', series_a: 'Series A', series_b: 'Series B',
  series_c_plus: 'Series C+', public: 'Public', acquired: 'Acquired',
}
const COMPANY_SECTOR_LABEL: Record<string, string> = {
  ai: 'AI', saas: 'SaaS', fintech: 'Fintech', consumer: 'Consumer', d2c: 'D2C', deeptech: 'Deeptech', edtech: 'Edtech',
  healthtech: 'Healthtech', logistics: 'Logistics & mobility', gaming: 'Gaming', devtools: 'Developer tools',
  it_services: 'IT services', other: 'Other',
}

const spaced = (code: string) => code.replace(/_/g, ' ')

export const companyStageLabel = (code: string): string => COMPANY_STAGE_LABEL[code] ?? spaced(code)
export const companySectorLabel = (code: string): string => COMPANY_SECTOR_LABEL[code] ?? spaced(code)

// The formats a draft can take, in the order domain/outreach_drafts.py::FORMATS lists them.
export type FormatSpec = { key: DraftFormat; channel: 'linkedin' | 'email' | 'x' | 'github'; label: string; maxChars: number }

export const FORMATS: readonly FormatSpec[] = [
  { key: 'linkedin_note', channel: 'linkedin', label: 'LinkedIn note', maxChars: 200 },
  { key: 'linkedin_message', channel: 'linkedin', label: 'LinkedIn message', maxChars: 700 },
  { key: 'email', channel: 'email', label: 'Email', maxChars: 1200 },
  { key: 'x_dm', channel: 'x', label: 'X message', maxChars: 280 },
  { key: 'github_comment', channel: 'github', label: 'GitHub comment', maxChars: 500 },
]

export const formatSpec = (key: DraftFormat): FormatSpec => FORMATS.find(f => f.key === key) ?? FORMATS[0]

/** The formats a person can be written to in, given the channels held for them, in FORMATS order. */
export function formatsForChannels(channels: Iterable<string>): FormatSpec[] {
  const wanted = new Set(channels)
  return FORMATS.filter(f => wanted.has(f.channel))
}

/** The format to open on: the short note for a first LinkedIn message, the longer one for a follow-up. */
export function defaultFormat(specs: readonly FormatSpec[], kind: 'first' | 'follow_up'): DraftFormat | null {
  const linkedin = specs.filter(f => f.channel === 'linkedin')
  if (linkedin.length) return (kind === 'first' ? linkedin[0] : linkedin[linkedin.length - 1]).key
  return specs[0]?.key ?? null
}

/** The channel a touch with this person is recorded on: the first way we can reach them. */
export const touchChannel = (p: OutreachPerson): OutreachChannelName => p.channels[0]?.channel ?? 'other'

/** A link that is safe to render or open: an http(s) URL, or a mailto: naming one plain address. Otherwise null. */
export function channelHref(url: string): string | null {
  return /^mailto:[^\s?#&,;<>"]+$/i.test(url) ? url : externalHref(url)
}
