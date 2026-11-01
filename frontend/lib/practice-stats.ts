import type { ChallengesOverview, PracticeDayCell, PracticeHistory } from './api'

/**
 * The rules behind the Practice page's graph and standing (spec 4.7, 4.8). Pure, with no React, so
 * `e2e/practice-stats.rules.spec.ts` can run them with no browser.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** A week of the graph, Monday first. `null` pads a week the window starts or ends inside. */
export type HeatColumn = Array<PracticeDayCell | null>

/** Easy lightest; medium and system design middle; hard and a contest entry darkest. */
export function heatLevel(day: PracticeDayCell): 0 | 1 | 2 | 3 {
  if (day.contest) return 3
  if (!day.done) return 0
  if (day.kind === 'design') return 2
  if (day.difficulty === 'hard') return 3
  return day.difficulty === 'easy' ? 1 : 2
}

/** The days as Monday-first weeks of seven, padded with `null` at both ends. */
export function heatColumns(days: PracticeDayCell[]): HeatColumn[] {
  if (!days.length) return []
  const lead = (new Date(`${days[0].day}T00:00:00Z`).getUTCDay() + 6) % 7
  const cells: HeatColumn = [...Array.from({ length: lead }, () => null), ...days]
  while (cells.length % 7) cells.push(null)
  const columns: HeatColumn[] = []
  for (let i = 0; i < cells.length; i += 7) columns.push(cells.slice(i, i + 7))
  return columns
}

/** A label over the first column that holds the first of a month. */
export function monthMarks(days: PracticeDayCell[]): Array<{ label: string; leftPercent: number }> {
  const columns = heatColumns(days)
  const marks: Array<{ label: string; leftPercent: number }> = []
  columns.forEach((column, index) => {
    const first = column.find(cell => cell?.day.endsWith('-01'))
    if (first) marks.push({ label: MONTHS[Number(first.day.slice(5, 7)) - 1], leftPercent: (index / columns.length) * 100 })
  })
  return marks
}

/** Nothing solved and no contest entry: today's problem goes above the empty graph. */
export function isNewToPractice(history: PracticeHistory): boolean {
  return history.solved.total === 0 && !history.days.some(day => day.contest)
}

/** The person's judged contest totals, oldest first. */
export function scoreLine(past: ChallengesOverview['past']): number[] {
  return past
    .filter(item => item.status === 'judged' && item.total != null)
    .sort((a, b) => a.opens_at.localeCompare(b.opens_at))
    .map(item => item.total as number)
}
