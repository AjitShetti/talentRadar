import { expect, test } from '@playwright/test'
import {
  KINDS, LABEL_MAX, MAX_BLOCKS, blockMinutes, currentBlock, istDayKey, newBlockId, routineMarks, routineProblem, starterRoutine,
  type Routine, type RoutineBlock,
} from '../lib/routine'
import {
  clearDay, mergeMarks, nextMark, onRoutine, publishRoutine, resetArrival, routineFor, routineIsFresh, routineSnapshot, tickLocally, type DayMark,
} from '../lib/day-line'

/**
 * The routine's rules, run on `lib/routine.ts` and the routine half of `lib/day-line.ts` with no
 * browser. Its sentences are domain/routine.py's, word for word: the page shows the server's
 * words after a save, so a rule the page checks first has to read the same.
 */

// 2026-11-01 14:00 IST.
const NOW = Date.parse('2026-11-01T08:30:00Z')
const OWNER = 'a@example.test'

// `publishRoutine` stamps the time it was read with `Date.now()`; hold that still so a run near IST midnight cannot disagree with itself.
const realNow = Date.now
test.beforeEach(() => { Date.now = () => NOW })
test.afterEach(() => { Date.now = realNow })

let counter = 0
const sequential = () => `b${++counter}`

const block = (id: string, at: string, kind: RoutineBlock['kind'] = 'apply', label = KINDS[kind].label): RoutineBlock => ({ id, at, kind, label })

const routine = (over: Partial<Routine> = {}): Routine => ({
  has_routine: true, blocks: [], done_today: [], day: '2026-11-01', tour_seen: true, onboarding_completed: true, ...over,
})

const mark = (id: string, at: number, extra: Partial<DayMark> = {}): DayMark => ({ id, at, time: '00:00', label: id, href: '/x', ...extra })

test('the kinds name their link and their default label', () => {
  expect(Object.keys(KINDS)).toEqual(['apply', 'connect', 'applications', 'practice', 'mock', 'events', 'custom'])
  expect(Object.values(KINDS).map(kind => kind.href)).toEqual(['/search', '/outreach', '/applications', '/practice', '/interview', '/events', null])
  expect(Object.values(KINDS).map(kind => kind.label)).toEqual([
    'Find roles and apply', 'Reach out to people', 'Follow up on applications', "Solve today's problem", 'Mock interview', 'Check events', '',
  ])
  expect(Object.values(KINDS).map(kind => kind.name)).toEqual(['Apply', 'Connect', 'Applications', 'Practice', 'Mock interview', 'Events', 'Your own'])
  expect([MAX_BLOCKS, LABEL_MAX]).toEqual([8, 60])
})

test('each starter routine has its times and kinds, named by default, with an id apiece', () => {
  const shape = (goal: Parameters<typeof starterRoutine>[0]) => starterRoutine(goal, sequential).map(b => [b.at, b.kind, b.label])
  expect(shape('apply')).toEqual([
    ['09:00', 'apply', 'Find roles and apply'], ['09:45', 'connect', 'Reach out to people'], ['18:00', 'applications', 'Follow up on applications'],
  ])
  expect(shape('prepare')).toEqual([['19:00', 'mock', 'Mock interview'], ['22:00', 'practice', "Solve today's problem"]])
  expect(shape('both')).toEqual([
    ['09:00', 'apply', 'Find roles and apply'], ['09:45', 'connect', 'Reach out to people'], ['19:00', 'mock', 'Mock interview'], ['22:00', 'practice', "Solve today's problem"],
  ])
  const made = starterRoutine('both')
  expect(new Set(made.map(b => b.id)).size).toBe(made.length)
  expect(routineProblem(made)).toBeNull()
})

test('a block id is twelve hex characters, and a starter takes the id maker it is given', () => {
  expect(newBlockId()).toMatch(/^[0-9a-f]{12}$/)
  expect(newBlockId()).not.toBe(newBlockId())
  expect(starterRoutine('prepare', () => 'fixed').map(b => b.id)).toEqual(['fixed', 'fixed'])
})

test('a time of day reads as minutes since midnight, and anything else as nothing', () => {
  expect(blockMinutes('00:00')).toBe(0)
  expect(blockMinutes('09:45')).toBe(585)
  expect(blockMinutes('23:59')).toBe(1439)
  for (const bad of ['', '9:00', '24:00', '09:60', '09:00:00', 'nine']) expect(Number.isNaN(blockMinutes(bad))).toBe(true)
})

test('an IST day key rolls over at IST midnight, not UTC midnight', () => {
  expect(istDayKey(NOW)).toBe('2026-11-01')
  expect(istDayKey(Date.parse('2026-11-01T18:29:00Z'))).toBe('2026-11-01')
  expect(istDayKey(Date.parse('2026-11-01T18:30:00Z'))).toBe('2026-11-02')
})

test('a valid routine has no problem, and an empty one has none either', () => {
  expect(routineProblem([block('a', '09:00'), block('b', '09:01', 'custom', 'Stretch')])).toBeNull()
  expect(routineProblem([])).toBeNull()
})

test('two blocks at one minute, a blank custom name and nine blocks read as the server says them', () => {
  expect(routineProblem([block('a', '09:00'), block('b', '09:00', 'connect')])).toBe('Two blocks are at 09:00. Move one of them.')
  expect(routineProblem([block('a', '09:00', 'custom', '   ')])).toBe('Your own block needs a name.')
  const nine = Array.from({ length: 9 }, (_, i) => block(`id${i}`, `0${i}:00`))
  expect(routineProblem(nine)).toBe('A routine holds at most 8 blocks.')
  expect(routineProblem(nine.slice(0, 8))).toBeNull()
})

test('a name is cut to sixty characters counted as the server counts them, after its spaces are collapsed', () => {
  expect(routineProblem([block('a', '09:00', 'custom', 'x'.repeat(60))])).toBeNull()
  expect(routineProblem([block('a', '09:00', 'custom', 'x'.repeat(61))])).toBe("A block's name can be at most 60 characters.")
  // Runs of spaces are one space, and the ends are trimmed, before the length is read.
  expect(routineProblem([block('a', '09:00', 'custom', `  ${'x '.repeat(30)}  `)])).toBeNull()
  // Sixty characters that are surrogate pairs are sixty characters, not a hundred and twenty.
  expect(routineProblem([block('a', '09:00', 'custom', '\u{1F600}'.repeat(60))])).toBeNull()
  expect(routineProblem([block('a', '09:00', 'custom', '\u{1F600}'.repeat(61))])).toBe("A block's name can be at most 60 characters.")
  // A kind of ours with no name takes its default, so it is never a problem.
  expect(routineProblem([block('a', '09:00', 'practice', '')])).toBeNull()
})

test('a bad id, time or kind reads as the server says it, one block at a time and before any repeat is looked for', () => {
  const cannot = 'A block could not be saved. Reload the page and try again.'
  expect(routineProblem([block('', '09:00')])).toBe(cannot)
  expect(routineProblem([block('has space', '09:00')])).toBe(cannot)
  expect(routineProblem([block('x'.repeat(37), '09:00')])).toBe(cannot)
  expect(routineProblem([block('a', '09:00'), block('a', '10:00')])).toBe(cannot)
  const time = 'Give each block a time from 00:00 to 23:59, like 09:00.'
  for (const at of ['', '9:00', '24:00', '09:60', '09:00 ']) expect(routineProblem([block('a', at)])).toBe(time)
  expect(routineProblem([{ id: 'a', at: '09:00', kind: '', label: '' }])).toBe('Pick what each block is for.')
  expect(routineProblem([{ id: 'a', at: '09:00', kind: 'nap', label: 'Nap' }])).toBe('Pick what each block is for.')
  // The first block that is wrong is the one named, even when a repeat comes earlier in the list.
  expect(routineProblem([block('a', '09:00'), block('b', '09:00'), block('c', '')])).toBe(time)
})

test('the block to do now is the latest one at or before now that is not ticked', () => {
  const blocks = [block('a', '09:00'), block('b', '09:45', 'connect'), block('c', '18:00', 'applications')]
  expect(currentBlock(blocks, [], 8 * 60 + 59)).toBeNull()
  expect(currentBlock(blocks, [], 9 * 60)?.id).toBe('a')
  expect(currentBlock(blocks, [], 10 * 60)?.id).toBe('b')
  // A ticked block is skipped: the latest block still to do, not the next one.
  expect(currentBlock(blocks, ['b'], 10 * 60)?.id).toBe('a')
  expect(currentBlock(blocks, ['a', 'b'], 10 * 60)).toBeNull()
  expect(currentBlock(blocks, ['a'], 18 * 60)?.id).toBe('c')
  // Order in the list does not matter.
  expect(currentBlock([...blocks].reverse(), [], 10 * 60)?.id).toBe('b')
  expect(currentBlock([], [], 600)).toBeNull()
})

test('a routine draws a mark per block, linking where its kind goes, and a block of the user\'s own goes to the Overview', () => {
  const marks = routineMarks([
    block('a', '09:00'), block('b', '09:45', 'connect'), block('c', '12:30', 'custom', 'Stretch'), block('d', '19:00', 'mock'),
  ], ['b', 'gone'])
  expect(marks).toEqual([
    { id: 'routine:a', at: 540, time: '09:00', label: 'Find roles and apply', href: '/search', routine: true, done: false },
    { id: 'routine:b', at: 585, time: '09:45', label: 'Reach out to people', href: '/outreach', routine: true, done: true },
    { id: 'routine:c', at: 750, time: '12:30', label: 'Stretch', href: '/dashboard', routine: true, done: false },
    { id: 'routine:d', at: 1140, time: '19:00', label: 'Mock interview', href: '/interview', routine: true, done: false },
  ])
  expect(routineMarks([], [])).toEqual([])
})

test('a kind this page does not know still gets a mark, linking to the Overview', () => {
  const unknown = { id: 'a', at: '09:00', kind: 'meditate', label: 'Sit' } as unknown as RoutineBlock
  expect(routineMarks([unknown], []).map(m => [m.id, m.href])).toEqual([['routine:a', '/dashboard']])
})

test('at one minute the briefing\'s mark comes first, and the rest are in time order', () => {
  const merged = mergeMarks(
    [mark('briefing:late', 900), mark('briefing:tie', 585)],
    [mark('routine:early', 540, { routine: true }), mark('routine:tie', 585, { routine: true }), mark('routine:night', 1320, { routine: true })],
  )
  expect(merged.map(m => m.id)).toEqual(['routine:early', 'briefing:tie', 'routine:tie', 'briefing:late', 'routine:night'])
  expect(mergeMarks([], [])).toEqual([])
  expect(mergeMarks([mark('b', 5)], []).map(m => m.id)).toEqual(['b'])
})

test('a routine shows to the account that read it, and to nobody else', () => {
  clearDay()
  publishRoutine(routine({ blocks: [block('a', '09:00')] }), OWNER)
  expect(routineFor(OWNER, NOW)?.blocks.map(b => b.id)).toEqual(['a'])
  expect(routineFor('b@example.test', NOW)).toBeNull()
  expect(routineFor(null, NOW)).toBeNull()
  // A read that lands after signing out is stored against nobody, and shows to nobody.
  publishRoutine(routine(), null)
  expect(routineFor(null, NOW)).toBeNull()
  expect(routineFor(OWNER, NOW)).toBeNull()
  clearDay()
})

test('a routine read on an earlier IST day is not today\'s, and stops being fresh after five minutes', () => {
  clearDay()
  publishRoutine(routine({ day: '2026-10-31' }), OWNER)
  expect(routineFor(OWNER, NOW)).toBeNull()
  publishRoutine(routine(), OWNER)
  expect(routineFor(OWNER, NOW)).not.toBeNull()
  // A tomorrow that has begun in IST makes today's routine yesterday's.
  expect(routineFor(OWNER, Date.parse('2026-11-01T18:30:00Z'))).toBeNull()

  clearDay()
  publishRoutine(routine(), OWNER)
  const at = routineSnapshot().at
  expect(at).toBe(NOW)
  expect(routineIsFresh(OWNER, at)).toBe(true)
  expect(routineIsFresh(OWNER, at + 4 * 60_000)).toBe(true)
  expect(routineIsFresh(OWNER, at + 6 * 60_000)).toBe(false)
  expect(routineIsFresh('b@example.test', at)).toBe(false)
  expect(routineIsFresh(null, at)).toBe(false)
  clearDay()
  expect(routineIsFresh(OWNER, at)).toBe(false)
})

test('a tick made on the page shows at once, and is undone the same way', () => {
  clearDay()
  const day = istDayKey(NOW)
  publishRoutine(routine({ day, blocks: [block('a', '09:00'), block('b', '10:00')], done_today: ['a'] }), OWNER)
  const heard: string[][] = []
  const stop = onRoutine(() => heard.push([...(routineSnapshot().routine?.done_today ?? [])]))
  tickLocally('b', true)
  tickLocally('b', true)
  tickLocally('a', false)
  stop()
  // The second tick of 'b' changes nothing, so nobody is told.
  expect(heard).toEqual([['a', 'b'], ['b']])
  expect(routineFor(OWNER, NOW)?.done_today).toEqual(['b'])
  clearDay()
  // With nothing read there is nothing to tick, and nobody is told.
  const quiet = onRoutine(() => { throw new Error('told about nothing') })
  tickLocally('a', true)
  quiet()
})

test('a tick leaves the routine it was made on as it was', () => {
  clearDay()
  const day = istDayKey(NOW)
  const before = routine({ day, blocks: [block('a', '09:00')], done_today: [] })
  publishRoutine(before, OWNER)
  tickLocally('a', true)
  expect(before.done_today).toEqual([])
  expect(routineFor(OWNER, NOW)?.done_today).toEqual(['a'])
  clearDay()
})

test('signing in again and clearing the day both empty the routine as well as the briefing', () => {
  const day = istDayKey(NOW)
  publishRoutine(routine({ day }), OWNER)
  expect(routineFor(OWNER, NOW)).not.toBeNull()
  clearDay()
  expect(routineSnapshot().owner).toBeNull()
  expect(routineSnapshot().routine).toBeNull()
  expect(routineFor(OWNER, NOW)).toBeNull()

  publishRoutine(routine({ day }), OWNER)
  resetArrival()
  expect(routineSnapshot().routine).toBeNull()
  expect(routineFor(OWNER, NOW)).toBeNull()
})

test('clearing the day tells whoever is listening to the routine', () => {
  publishRoutine(routine({ day: istDayKey(NOW) }), OWNER)
  let told = 0
  const stop = onRoutine(() => { told += 1 })
  clearDay()
  stop()
  expect(told).toBe(1)
})

test('a block ticked off ahead of time is not what comes next', () => {
  const marks = [mark('a', 600, { routine: true, done: true }), mark('b', 900, { routine: true })]
  expect(nextMark(marks, 500)?.id).toBe('b')
  expect(nextMark([mark('a', 600, { routine: true, done: true })], 500)).toBeNull()
})
