import { expect, test } from '@playwright/test'
import type { BriefingCard } from '../lib/api'
import {
  applicationNotes, clearDay, clockLabel, dayFor, dayIsFresh, daySnapshot, forgetNote, istMinutes, labelFlips, labelledMarks, markForAction, marksFromCards, nextMark, publishBriefing, untilLabel, type DayMark,
} from '../lib/day-line'

/**
 * The day line's honesty rules, run on `lib/day-line.ts` itself with no browser:
 * a mark is only something the briefing dated for today (IST), and nothing else
 * is ever drawn on the line.
 */

// 2026-11-01 14:00 IST.
const NOW = Date.parse('2026-11-01T08:30:00Z')

const card = (over: Partial<BriefingCard> & { id: string; kind: string }): BriefingCard => ({
  title: 'Title', detail: '', tone: 'action', actions: [{ label: 'Open', href: '/somewhere', style: 'primary' }], meta: {}, dismissible: true, ...over,
} as BriefingCard)

const mark = (id: string, at: number, label = 'Thing'): DayMark => ({ id, at, time: clockLabel(at), label, href: '/x' })

test('IST minutes roll over at IST midnight, not UTC midnight', () => {
  expect(istMinutes(NOW)).toBe(14 * 60)
  expect(istMinutes(Date.parse('2026-11-01T18:30:00Z'))).toBe(0)
  expect(istMinutes(Date.parse('2026-11-01T18:29:00Z'))).toBe(1439)
})

test('a dated card for today becomes a mark at its IST time, linking where the card links', () => {
  const marks = marksFromCards([card({
    id: 'practice_deadline:a1', kind: 'practice_deadline', meta: { deadline_at: '2026-11-01T10:30:00Z' },
  })], NOW)
  expect(marks).toEqual([{ id: 'practice_deadline:a1', at: 16 * 60, time: '16:00', label: 'Take-home due', href: '/somewhere' }])
})

test('an interview card makes no mark and no row note: its time is when the status moved', () => {
  const interview = card({
    id: 'scheduled_interview:a1', kind: 'scheduled_interview', meta: { scheduled_at: '2026-11-01T10:30:00Z', company: 'Northwind', days_away: 0 },
  })
  expect(marksFromCards([interview], NOW)).toEqual([])
  expect(applicationNotes([interview]).size).toBe(0)
})

test('a card with no clock time, a card for another IST day, and an unreadable time make no mark', () => {
  const marks = marksFromCards([
    card({ id: 'f', kind: 'outreach_followup', meta: { count: 2 } }),
    card({ id: 'p', kind: 'practice_today', meta: {} }),
    // 00:10 IST on the 2nd is the 1st in UTC: it is tomorrow here.
    card({ id: 'tomorrow', kind: 'practice_deadline', meta: { deadline_at: '2026-11-01T18:40:00Z' } }),
    card({ id: 'junk', kind: 'practice_contest', meta: { starts_at: 'soon' } }),
    card({ id: 'missing', kind: 'event_today', meta: {} }),
  ], NOW)
  expect(marks).toEqual([])
})

test('a cancelled event is not on the day', () => {
  const marks = marksFromCards([
    card({ id: 'event_today:gone', kind: 'event_today', tone: 'warning', title: 'Meetup was cancelled by the organiser', meta: { starts_at: '2026-11-01T12:00:00Z', status: 'cancelled' } }),
    card({ id: 'event_soon:gone', kind: 'event_soon', meta: { starts_at: '2026-11-01T12:00:00Z', status: 'cancelled' } }),
  ], NOW)
  expect(marks).toEqual([])
})

test('an event loses the card title\'s "Tonight:" and marks come back in time order', () => {
  const marks = marksFromCards([
    card({ id: 'b', kind: 'practice_deadline', meta: { deadline_at: '2026-11-01T17:00:00Z' } }),
    card({ id: 'a', kind: 'event_today', title: 'Tonight: GDG Bengaluru', meta: { starts_at: '2026-11-01T13:00:00Z' } }),
  ], NOW)
  expect(marks.map(m => [m.id, m.label])).toEqual([['a', 'GDG Bengaluru'], ['b', 'Take-home due']])
})

test('what is next is the first mark strictly after now, and the countdown reads as time', () => {
  const marks = [mark('a', 600), mark('b', 900)]
  expect(nextMark(marks, 600)?.id).toBe('b')
  expect(nextMark(marks, 900)).toBeNull()
  expect(untilLabel(134)).toBe('2h 14m')
  expect(untilLabel(125)).toBe('2h 05m')
  expect(untilLabel(9)).toBe('9m')
})

test('a row finds its mark by card id, and only by card id', () => {
  const marks = [mark('practice_deadline:x', 600), mark('event_today:e1', 900)]
  expect(markForAction('briefing-practice_deadline:x', marks)?.at).toBe(600)
  expect(markForAction('interview-today', marks)).toBeNull()
  expect(markForAction('onboard', marks)).toBeNull()
})

test('labels that would overlap give way, earliest first, and the later mark keeps its place', () => {
  const marks = [mark('a', 600, 'Interview, Northwind'), mark('b', 610, 'Take-home due'), mark('c', 1200, 'Contest')]
  const shown = labelledMarks(marks, 1200)
  expect(shown.has('a')).toBe(true)
  expect(shown.has('b')).toBe(false)
  expect(shown.has('c')).toBe(true)
})

test('a label stays on the line: it flips near the end, is capped, and is left off when it fits neither way', () => {
  // 20:30 on an 800px line, with a title far wider than the cap.
  const long = mark('a', 1230, 'A very long event title that goes on for a good while longer')
  expect(labelFlips(long, 800)).toBe(true)
  expect(labelledMarks([long], 800).has('a')).toBe(true)
  // Early in the day there is room to the right, whatever the hour says.
  expect(labelFlips(mark('b', 600), 800)).toBe(false)
  // Not measured yet: nothing is claimed to fit.
  expect(labelledMarks([mark('c', 600)], 0).size).toBe(0)
})

test('past midnight IST yesterday\'s marks are not shown, and another account\'s never are', () => {
  const briefing = { generated_at: '', headline: '', hidden_count: 0, stats: {}, cards: [card({ id: 'p', kind: 'practice_deadline', meta: { deadline_at: new Date(Date.now() + 60_000).toISOString() } })] }
  publishBriefing(briefing, 'a@example.test')
  const at = daySnapshot().at
  expect(dayFor(daySnapshot(), 'a@example.test', at).owner).toBe('a@example.test')
  expect(dayFor(daySnapshot(), 'b@example.test', at).owner).toBeNull()
  expect(dayFor(daySnapshot(), null, at).owner).toBeNull()
  // A read that lands after signing out is stored against nobody, and still shows to nobody.
  publishBriefing(briefing, null)
  expect(dayFor(daySnapshot(), null, daySnapshot().at).owner).toBeNull()
  publishBriefing(briefing, 'a@example.test')
  const tomorrow = dayFor(daySnapshot(), 'a@example.test', at + 86_400_000)
  expect(tomorrow.marks).toEqual([])
  expect(tomorrow.owner).toBeNull()
  clearDay()
  expect(daySnapshot().owner).toBeNull()
})

test('a read stops being fresh after five minutes, for another account, and at IST midnight', () => {
  const briefing = { generated_at: '', headline: '', hidden_count: 0, stats: {}, cards: [] }
  publishBriefing(briefing, 'a@example.test')
  const at = daySnapshot().at
  expect(dayIsFresh('a@example.test', at)).toBe(true)
  expect(dayIsFresh('a@example.test', at + 6 * 60_000)).toBe(false)
  expect(dayIsFresh('b@example.test', at)).toBe(false)
  const untilMidnight = (1440 - istMinutes(at)) * 60_000
  expect(dayIsFresh('a@example.test', at + untilMidnight)).toBe(false)
  clearDay()
})

test('a briefing read on an earlier IST day does not replace today\'s, and a moved role loses its note', () => {
  const stale = card({ id: 'stale_application:a3', kind: 'stale_application' })
  publishBriefing({ generated_at: new Date().toISOString(), headline: '', hidden_count: 0, stats: {}, cards: [stale] }, 'a@example.test')
  expect(daySnapshot().notes.get('a3')).toBe('Gone quiet')
  publishBriefing({ generated_at: new Date(Date.now() - 86_400_000).toISOString(), headline: '', hidden_count: 0, stats: {}, cards: [] }, 'a@example.test')
  expect(daySnapshot().notes.get('a3')).toBe('Gone quiet')
  forgetNote('a3')
  expect(daySnapshot().notes.size).toBe(0)
  clearDay()
})

test('a row is only told what a card says about that application', () => {
  const notes = applicationNotes([
    card({ id: 'stale_application:a3', kind: 'stale_application' }),
    card({ id: 'saved_backlog', kind: 'saved_backlog' }),
  ])
  expect(notes.get('a3')).toBe('Gone quiet')
  expect(notes.size).toBe(1)
})
