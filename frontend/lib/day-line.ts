import type { Briefing, BriefingCard } from '@/lib/api'
import { istDayKey, type Routine } from '@/lib/routine'

/**
 * The day line's rules and its small shared store. Pure rules first, so
 * e2e/day-line.rules.spec.ts can run them with no browser.
 *
 * A mark is only ever something the briefing dated. An outreach follow-up is due
 * "from 00:00 IST" and has no clock time, so it gets no mark, and a day with
 * nothing dated is a line with only the time on it.
 */

/** A day is an IST day here, as in domain/outreach.py and lib/practice-time.ts: a fixed +05:30. */
const IST_OFFSET_MS = 5.5 * 3_600_000
const DAY_MS = 86_400_000
export const DAY_MINUTES = 1440

export const istMinutes = (ms: number) => Math.floor((((ms + IST_OFFSET_MS) % DAY_MS) + DAY_MS) % DAY_MS / 60_000)
export const istDay = (ms: number) => Math.floor((ms + IST_OFFSET_MS) / DAY_MS)

export const clockLabel = (minutes: number) => {
  const whole = Math.max(0, Math.min(DAY_MINUTES - 1, Math.round(minutes)))
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`
}

/** `routine` marks one from the user's own routine (lib/routine.ts), drawn apart from the briefing's; `done` is that block's tick today. */
export type DayMark = { id: string; at: number; time: string; label: string; href: string; routine?: true; done?: boolean }

const text = (value: unknown) => typeof value === 'string' ? value : ''

/**
 * The field holding a card's time, and what the line calls it. Event cards title
 * themselves "Tonight: <event>". An interview card carries only the time the user
 * entered (`interview_at`); a role at interview stage with no time has a card of another kind.
 */
const TIMED: Record<string, { field: string; label: (card: BriefingCard) => string }> = {
  event_today: { field: 'starts_at', label: card => card.title.replace(/^[^:]*:\s*/, '') },
  event_soon: { field: 'starts_at', label: card => card.title.replace(/^[^:]*:\s*/, '') },
  practice_deadline: { field: 'deadline_at', label: () => 'Take-home due' },
  practice_contest: { field: 'starts_at', label: () => 'Weekly contest' },
  scheduled_interview: { field: 'scheduled_at', label: card => `Interview, ${text(card.meta.company)}` },
}

/** What today holds, in time order. A cancelled event is not on the day any more. */
export function marksFromCards(cards: BriefingCard[], nowMs: number): DayMark[] {
  const marks: DayMark[] = []
  for (const card of cards) {
    const timed = TIMED[card.kind]
    if (!timed || text(card.meta.status) === 'cancelled' || (card.kind === 'event_today' && card.tone === 'warning')) continue
    const ms = Date.parse(text(card.meta[timed.field]))
    if (Number.isNaN(ms) || istDay(ms) !== istDay(nowMs)) continue
    const at = istMinutes(ms)
    marks.push({ id: card.id, at, time: clockLabel(at), label: timed.label(card), href: card.actions?.[0]?.href || '/dashboard' })
  }
  return marks.sort((a, b) => a.at - b.at)
}

/** The next thing today, or null when nothing is left. */
export const nextMark = (marks: DayMark[], now: number) => marks.find(mark => mark.at > now) ?? null

export function untilLabel(minutes: number) {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return hours > 0 ? `${hours}h ${String(rest).padStart(2, '0')}m` : `${rest}m`
}

/** The mark an Up next row stands for: a briefing row carries its card id. */
export function markForAction(actionKey: string, marks: DayMark[]): DayMark | null {
  const id = actionKey.replace(/^briefing-/, '')
  return marks.find(mark => mark.id === id) ?? null
}

/** The widest a label is drawn, in px: `.day-mark span` cuts it off there. */
const LABEL_MAX = 240
const labelSize = (mark: DayMark) => Math.min(LABEL_MAX, (mark.time.length + mark.label.length + 1) * 6.8)

/** A label runs right from its mark, unless that would take it off the end of the line. */
export const labelFlips = (mark: DayMark, width: number) => (mark.at / DAY_MINUTES) * width + labelSize(mark) > width

/** Which labels fit along a line this wide: an earlier label wins, a later one keeps its stem and its link. */
export function labelledMarks(marks: DayMark[], width: number): Set<string> {
  const shown = new Set<string>()
  let lastRight = -Infinity
  for (const mark of marks) {
    const left = (mark.at / DAY_MINUTES) * width
    const size = labelSize(mark)
    const from = labelFlips(mark, width) ? left - size : left
    if (from < 0 || from < lastRight + 12) continue
    shown.add(mark.id)
    lastRight = from + size
  }
  return shown
}

/**
 * What a row should say, from the card about its application ("<kind>:<application id>"):
 * a stale one has gone quiet, and one with an entered interview time today says when.
 */
export function applicationNotes(cards: BriefingCard[], nowMs: number): Map<string, string> {
  const notes = new Map<string, string>()
  for (const card of cards) {
    const [kind, id] = card.id.split(':')
    if (!id) continue
    if (kind === 'stale_application') notes.set(id, 'Gone quiet')
    if (kind === 'scheduled_interview') {
      const ms = Date.parse(text(card.meta.scheduled_at))
      if (!Number.isNaN(ms) && istDay(ms) === istDay(nowMs)) notes.set(id, `Interview today, ${clockLabel(istMinutes(ms))}`)
    }
  }
  return notes
}

/* ── the store: one briefing read shared by the line and whatever page is under it ── */

type Snapshot = { owner: string | null; at: number; marks: DayMark[]; notes: Map<string, string> }

const EMPTY: Snapshot = { owner: null, at: 0, marks: [], notes: new Map() }
/** The briefing is deterministic and cheap, but a route change should not ask for it again. */
const FRESH_MS = 5 * 60_000

let snapshot = EMPTY
const listeners = new Set<() => void>()
const notify = () => listeners.forEach(listener => listener())

export const daySnapshot = () => snapshot
export const onDaySnapshot = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }

/** What may be shown: nothing when signed out, another account's marks never, and yesterday's never as today's. */
export const dayFor = (day: Snapshot, owner: string | null, nowMs: number) =>
  owner !== null && day.owner === owner && istDay(day.at) === istDay(nowMs) ? day : EMPTY

export const dayIsFresh = (owner: string | null, nowMs = Date.now()) =>
  dayFor(snapshot, owner, nowMs) !== EMPTY && nowMs - snapshot.at < FRESH_MS

export function publishBriefing(briefing: Briefing, owner: string | null) {
  const now = Date.now()
  // A page left open past midnight may hand back the briefing it read yesterday.
  const read = Date.parse(briefing.generated_at)
  if (!Number.isNaN(read) && istDay(read) !== istDay(now)) return
  snapshot = { owner, at: now, marks: marksFromCards(briefing.cards, now), notes: applicationNotes(briefing.cards, now) }
  notify()
}

/** A role that just moved: what the briefing said about it no longer holds. */
export function forgetNote(applicationId: string) {
  if (!snapshot.notes.has(applicationId)) return
  const notes = new Map(snapshot.notes)
  notes.delete(applicationId)
  snapshot = { ...snapshot, notes }
  notify()
}

/**
 * The briefing's marks and the routine's, in time order. At one minute the briefing's comes
 * first (the sort is stable, and the briefing's marks are listed before the routine's).
 */
export const mergeMarks = (briefing: DayMark[], routine: DayMark[]): DayMark[] =>
  [...briefing, ...routine].sort((a, b) => a.at - b.at)

/* ── the routine: its own read, beside the briefing's, because it comes from its own route ── */

type RoutineSnapshot = { owner: string | null; at: number; routine: Routine | null }

const NO_ROUTINE: RoutineSnapshot = { owner: null, at: 0, routine: null }

let routineStore = NO_ROUTINE
const routineListeners = new Set<() => void>()
const notifyRoutine = () => routineListeners.forEach(listener => listener())

export const routineSnapshot = () => routineStore
export const onRoutine = (listener: () => void) => { routineListeners.add(listener); return () => { routineListeners.delete(listener) } }

export function publishRoutine(routine: Routine, owner: string | null) {
  routineStore = { owner, at: Date.now(), routine }
  notifyRoutine()
}

/** What may be shown: nothing when signed out, another account's routine never, and yesterday's ticks never as today's. */
export function routineFor(owner: string | null, nowMs: number): Routine | null {
  const { routine } = routineStore
  if (owner === null || routineStore.owner !== owner || !routine) return null
  return routine.day === istDayKey(nowMs) ? routine : null
}

export const routineIsFresh = (owner: string | null, nowMs = Date.now()) =>
  routineFor(owner, nowMs) !== null && nowMs - routineStore.at < FRESH_MS

/** A tick made on the page, shown before the server has answered. The reply, or a reload, is what settles it. */
export function tickLocally(id: string, done: boolean) {
  const { routine } = routineStore
  if (!routine || routine.done_today.includes(id) === done) return
  const done_today = done ? [...routine.done_today, id] : routine.done_today.filter(ticked => ticked !== id)
  routineStore = { ...routineStore, routine: { ...routine, done_today } }
  notifyRoutine()
}

export function clearDay() {
  snapshot = EMPTY
  notify()
  routineStore = NO_ROUTINE
  notifyRoutine()
}

/** The mark being pointed at, from the line or from a row: each lights the other. */
let lit: string | null = null
const litListeners = new Set<() => void>()
export const dayLit = () => lit
export const onDayLit = (listener: () => void) => { litListeners.add(listener); return () => { litListeners.delete(listener) } }
export function setDayLit(next: string | null) {
  if (next === lit) return
  lit = next
  litListeners.forEach(listener => listener())
}

/* ── the arrival: played once a session, and again after each sign-in ── */

const ARRIVED_KEY = 'tr.day.arrived'
let arrivedHere = false

export function arrivalPending() {
  if (arrivedHere) return false
  try { return sessionStorage.getItem(ARRIVED_KEY) === null } catch { return true }
}

export function markArrived() {
  arrivedHere = true
  try { sessionStorage.setItem(ARRIVED_KEY, '1') } catch { /* storage blocked: the module flag holds until a reload */ }
}

/** Called by sign-in and sign-up on their way to the app, so the first Overview after them plays it. */
export function resetArrival() {
  arrivedHere = false
  clearDay()
  try { sessionStorage.removeItem(ARRIVED_KEY) } catch { /* storage blocked */ }
}
