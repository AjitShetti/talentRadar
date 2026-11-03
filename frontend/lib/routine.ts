import type { DayMark } from '@/lib/day-line'

/**
 * A user's daily routine: the blocks they set for the day and what the page does with
 * them. Pure rules, so e2e/routine.rules.spec.ts can run them with no browser.
 *
 * `routineProblem` is domain/routine.py::validate_blocks in TypeScript, sentence for
 * sentence: the page says what is wrong before it asks, and after a save it shows the
 * server's own words, which have to read the same. Change one side, change the other.
 * Nothing here sends a reminder or does the work.
 */

export type RoutineKind = 'apply' | 'connect' | 'applications' | 'practice' | 'mock' | 'events' | 'custom'

/** `at` is a clock time in IST, "HH:MM", 24 h. */
export type RoutineBlock = { id: string; at: string; kind: RoutineKind; label: string }

/** What every /routine route answers (api/routers/routine.py). `done_today` is today's ticks only. */
export type Routine = {
  has_routine: boolean
  blocks: RoutineBlock[]
  done_today: string[]
  day: string
  tour_seen: boolean
  onboarding_completed: boolean
}

export type RoutineGoal = 'apply' | 'prepare' | 'both'

/**
 * `name` is the option in the editor, `label` what a block of that kind says when its name is
 * left empty, `href` where the day line sends the user. A block of the user's own has no
 * default name and no page.
 */
export const KINDS: Record<RoutineKind, { name: string; label: string; href: string | null }> = {
  apply: { name: 'Apply', label: 'Find roles and apply', href: '/search' },
  connect: { name: 'Connect', label: 'Reach out to people', href: '/outreach' },
  applications: { name: 'Applications', label: 'Follow up on applications', href: '/applications' },
  practice: { name: 'Practice', label: "Solve today's problem", href: '/practice' },
  mock: { name: 'Mock interview', label: 'Mock interview', href: '/interview' },
  events: { name: 'Events', label: 'Check events', href: '/events' },
  custom: { name: 'Your own', label: '', href: null },
}

export const MAX_BLOCKS = 8
export const LABEL_MAX = 60

/** A day is an IST day, as in domain/event_time.py and lib/day-line.ts: a fixed +05:30. */
const IST_OFFSET_MS = 5.5 * 3_600_000

/** The IST calendar day `ms` falls on, "YYYY-MM-DD": what `Routine.day` is compared with. */
export const istDayKey = (ms: number): string => new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 10)

/** Twelve hex characters: inside the server's id rule (1 to 36 of letters, digits, `_`, `-`), and made here because the page makes the block. */
export const newBlockId = (): string => crypto.randomUUID().replace(/-/g, '').slice(0, 12)

const STARTERS: Record<RoutineGoal, Array<[at: string, kind: RoutineKind]>> = {
  apply: [['09:00', 'apply'], ['09:45', 'connect'], ['18:00', 'applications']],
  prepare: [['19:00', 'mock'], ['22:00', 'practice']],
  both: [['09:00', 'apply'], ['09:45', 'connect'], ['19:00', 'mock'], ['22:00', 'practice']],
}

/** A routine to start from, for what the person came to do. Each block is named by its kind's default. */
export const starterRoutine = (goal: RoutineGoal, makeId: () => string = newBlockId): RoutineBlock[] =>
  STARTERS[goal].map(([at, kind]) => ({ id: makeId(), at, kind, label: KINDS[kind].label }))

// ASCII only, as on the server: nothing from another script counts as a digit or a letter.
const TIME = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/
const ID = /^[A-Za-z0-9_-]{1,36}$/

/** Minutes since midnight for an "HH:MM" time, or NaN for anything else. */
export function blockMinutes(at: string): number {
  if (!TIME.test(at)) return Number.NaN
  return Number(at.slice(0, 2)) * 60 + Number(at.slice(3))
}

const CANNOT_SAVE = 'A block could not be saved. Reload the page and try again.'

/** What the server stores as the name: trimmed, runs of whitespace one space. */
const cleanLabel = (label: string) => label.split(/\s+/).filter(Boolean).join(' ')

/** A block as the editor holds it: `kind` may not be picked yet, so it is a string here and checked. */
type DraftBlock = { id: string; at: string; kind: string; label: string }

/** The problem with one block on its own, in the order the server looks. */
function blockProblem(block: DraftBlock): string | null {
  if (!ID.test(block.id)) return CANNOT_SAVE
  if (!TIME.test(block.at)) return 'Give each block a time from 00:00 to 23:59, like 09:00.'
  if (!Object.prototype.hasOwnProperty.call(KINDS, block.kind)) return 'Pick what each block is for.'
  const label = cleanLabel(block.label)
  if (!label && block.kind === 'custom') return 'Your own block needs a name.'
  // The server counts characters, not UTF-16 units: an emoji is one.
  if (Array.from(label).length > LABEL_MAX) return `A block's name can be at most ${LABEL_MAX} characters.`
  return null
}

/**
 * The sentence for what stops these blocks being saved, or null when they can be. The same
 * rules, in the same order, as domain/routine.py::validate_blocks: too many, then each block
 * in turn, then a repeated id or a repeated time.
 */
export function routineProblem(blocks: readonly DraftBlock[]): string | null {
  if (blocks.length > MAX_BLOCKS) return `A routine holds at most ${MAX_BLOCKS} blocks.`
  for (const block of blocks) {
    const problem = blockProblem(block)
    if (problem) return problem
  }
  const ids = new Set<string>()
  const times = new Set<string>()
  for (const block of blocks) {
    if (ids.has(block.id)) return CANNOT_SAVE
    if (times.has(block.at)) return `Two blocks are at ${block.at}. Move one of them.`
    ids.add(block.id)
    times.add(block.at)
  }
  return null
}

/**
 * The block to do now: the latest one at or before `nowMinute` (an IST minute of the day)
 * that is not ticked. An earlier block left undone stays the answer until it is ticked
 * or a later block begins.
 */
export function currentBlock(blocks: readonly RoutineBlock[], done: readonly string[], nowMinute: number): RoutineBlock | null {
  let current: RoutineBlock | null = null
  let currentAt = -1
  for (const block of blocks) {
    const at = blockMinutes(block.at)
    if (!(at <= nowMinute) || done.includes(block.id)) continue
    if (at > currentAt) { current = block; currentAt = at }
  }
  return current
}

/**
 * The routine as marks on the day line, in time order. A block of ours links to its page; one
 * of the user's own, or of a kind this page does not know, links to the Overview.
 */
export function routineMarks(blocks: readonly RoutineBlock[], done: readonly string[]): DayMark[] {
  const marks: DayMark[] = []
  for (const block of blocks) {
    const at = blockMinutes(block.at)
    if (Number.isNaN(at)) continue
    marks.push({
      id: `routine:${block.id}`,
      at,
      time: block.at,
      label: block.label,
      href: (KINDS as Partial<Record<string, { href: string | null }>>)[block.kind]?.href ?? '/dashboard',
      routine: true,
      done: done.includes(block.id),
    })
  }
  return marks.sort((a, b) => a.at - b.at)
}
