// Pure display helpers for /outreach, the thread drawer and the company-drawer block.
// No fetching, no storage, no sending: a channel action only builds a link the
// browser opens in a new tab. Types come from lib/api.ts, which mirrors the server.
import type { DraftFormat, OutreachPerson, PersonState, ThreadStage, ThreadSummary } from '@/lib/api'

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
const MAX_FOLLOW_UPS = 2
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
