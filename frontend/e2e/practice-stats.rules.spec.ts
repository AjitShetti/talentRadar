import { expect, test } from '@playwright/test'
import { heatColumns, heatLevel, isNewToPractice, monthMarks, scoreLine } from '../lib/practice-stats'
import type { ChallengesOverview, PracticeDayCell, PracticeHistory } from '../lib/api'

/**
 * What the Practice page's graph and standing must get right (spec 4.7, 4.8), run on
 * `lib/practice-stats.ts` itself. It imports the pure rules and uses no `page`.
 */

const day = (over: Partial<PracticeDayCell> = {}): PracticeDayCell => ({
  day: '2026-11-02', done: false, difficulty: null, topic: null, kind: null, contest: false, ...over,
})

/** `count` consecutive days from `first` (YYYY-MM-DD). */
function run(first: string, count: number): PracticeDayCell[] {
  const start = new Date(`${first}T00:00:00Z`).getTime()
  return Array.from({ length: count }, (_, i) => day({ day: new Date(start + i * 86_400_000).toISOString().slice(0, 10) }))
}

const history = (over: Partial<PracticeHistory> = {}): PracticeHistory => ({
  days: [],
  counts: { this_month: 0, last_30: 0, longest_run: 0, current_streak: 0 },
  topics: [],
  solved: { easy: 0, medium: 0, hard: 0, design: 0, total: 0 },
  available: { easy: null, medium: null, hard: null, design: 40 },
  ...over,
})

test('shade follows difficulty, and a contest is darkest', () => {
  expect(heatLevel(day({ done: true, difficulty: 'easy', kind: 'dsa' }))).toBe(1)
  expect(heatLevel(day({ done: true, difficulty: 'medium', kind: 'dsa' }))).toBe(2)
  expect(heatLevel(day({ done: true, difficulty: 'medium', kind: 'design' }))).toBe(2)
  expect(heatLevel(day({ done: true, difficulty: 'hard', kind: 'dsa' }))).toBe(3)
  expect(heatLevel(day({ contest: true }))).toBe(3)
  expect(heatLevel(day({ done: true, difficulty: 'easy', kind: 'dsa', contest: true }))).toBe(3)
  expect(heatLevel(day({}))).toBe(0)
})

test('columns are Monday-first weeks of seven', () => {
  // 2026-11-04 is a Wednesday, so two empty cells lead; 2026-11-10 is a Tuesday, so five trail.
  const columns = heatColumns(run('2026-11-04', 7))
  expect(columns).toHaveLength(2)
  for (const column of columns) expect(column).toHaveLength(7)
  expect(columns[0].slice(0, 2).every(cell => cell === null)).toBe(true)
  expect(columns[0][2]?.day).toBe('2026-11-04')
  expect(columns[1][0]?.day).toBe('2026-11-09')
  expect(columns[1].slice(2).every(cell => cell === null)).toBe(true)
})

test('a year fits in 53 or 54 columns and no day is lost', () => {
  const days = run('2025-11-03', 53 * 7)
  const columns = heatColumns(days)
  expect(columns.flat().filter(Boolean)).toHaveLength(53 * 7)
  expect(columns.length).toBeGreaterThanOrEqual(53)
  expect(columns.length).toBeLessThanOrEqual(54)
})

test('no days means no columns', () => {
  expect(heatColumns([])).toEqual([])
})

test('a month is marked over the column where it starts', () => {
  // Four Monday-first weeks from 19 Oct: 1 Nov is the Sunday of the second, so a quarter across.
  const marks = monthMarks(run('2026-10-19', 28))
  expect(marks.map(mark => mark.label)).toEqual(['Nov'])
  expect(marks[0].leftPercent).toBe(25)
})

test('a new person has nothing solved and no contest', () => {
  expect(isNewToPractice(history({ days: run('2026-11-01', 3) }))).toBe(true)
})

test('a person with only a contest entry is not new', () => {
  expect(isNewToPractice(history({ days: [day({ contest: true })] }))).toBe(false)
})

test('a person with a solved problem is not new', () => {
  expect(isNewToPractice(history({ solved: { easy: 1, medium: 0, hard: 0, design: 0, total: 1 } }))).toBe(false)
})

test('the score line is judged totals, oldest first', () => {
  const past: ChallengesOverview['past'] = [
    { slug: 'c', title: 'C', track: 'backend', opens_at: '2026-10-23T12:30:00Z', status: 'judged', total: 74.5, held: false },
    { slug: 'b', title: 'B', track: 'backend', opens_at: '2026-10-16T12:30:00Z', status: 'missed', total: null, held: false },
    { slug: 'q', title: 'Q', track: 'backend', opens_at: '2026-10-12T12:30:00Z', status: 'queued', total: 50, held: false },
    { slug: 'a', title: 'A', track: 'backend', opens_at: '2026-10-09T12:30:00Z', status: 'judged', total: 61, held: false },
  ]
  expect(scoreLine(past)).toEqual([61, 74.5])
})
