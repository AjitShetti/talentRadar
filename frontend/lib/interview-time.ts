/**
 * The interview time a person enters on a tracker row. It is typed and read back in IST,
 * whatever zone the browser is in, so what they entered is what every surface says.
 * Pure, so e2e/interview-time.rules.spec.ts can run it with no browser.
 */

const IST_OFFSET_MS = 5.5 * 3_600_000

/** What a datetime-local field sends the API: the same wall clock, with its offset stated. */
export const interviewAtFromInput = (value: string) => value ? `${value.slice(0, 16)}:00+05:30` : null

/** A stored time as a datetime-local field shows it, in IST. Empty when there is none. */
export function inputFromInterviewAt(iso: string | null | undefined) {
  const ms = iso ? Date.parse(iso) : NaN
  return Number.isNaN(ms) ? '' : new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 16)
}

/** A role that has reached an offer or closed has no interview to plan; the Overview drops it too. */
const FINISHED = new Set(['offer', 'rejected', 'withdrawn'])

/** The field is on a row at interview stage, and stays on a live one that already has a time. */
export const asksInterviewTime = (status: string, interviewAt: string | null | undefined) =>
  status === 'interview' || (Boolean(interviewAt) && !FINISHED.has(status))
