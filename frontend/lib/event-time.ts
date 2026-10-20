/**
 * Event times, in IST.
 *
 * IST has no daylight saving, so a fixed offset is exact. Everything a person
 * reads about an event is IST, as in domain/event_time.py. Pure functions: no
 * DOM, no React, so the events page and the company drawer read one definition.
 */

export const IST_OFFSET_MS = 330 * 60_000
export const DAY_MS = 86_400_000
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const pad = (n: number) => String(n).padStart(2, '0')

export type IstParts = { day: number; month: number; weekday: number; hour: number; minute: number }

export function ist(ms: number): IstParts {
  const t = new Date(ms + IST_OFFSET_MS)
  return { day: t.getUTCDate(), month: t.getUTCMonth(), weekday: t.getUTCDay(), hour: t.getUTCHours(), minute: t.getUTCMinutes() }
}

/** Whole IST days since the epoch: two instants on one IST date share it. */
export const istDay = (ms: number) => Math.floor((ms + IST_OFFSET_MS) / DAY_MS)
export const clock = (p: IstParts) => `${pad(p.hour)}:${pad(p.minute)}`
export const dayLabel = (p: IstParts) => `${WEEKDAYS[p.weekday]} ${p.day} ${MONTHS[p.month]}`

export const startMs = (event: { starts_at: string }) => Date.parse(event.starts_at)

/** The end the source gave, or null. A null end is never shown as if it were known. */
export function knownEndMs(event: { starts_at: string; ends_at: string | null }): number | null {
  if (!event.ends_at) return null
  const end = Date.parse(event.ends_at)
  return Number.isFinite(end) && end > startMs(event) ? end : null
}

/** Readers assume a null end means start + 2 h (domain.event_time.effective_end). */
export const effectiveEndMs = (event: { starts_at: string; ends_at: string | null }) => knownEndMs(event) ?? startMs(event) + 2 * 3_600_000

/** "Thu 15 Oct · 18:30–20:00 IST", across days "Sat 24 Oct 09:30 – Sun 25 Oct 17:00 IST", start only when there is no end. */
export function whenLabel(event: { starts_at: string; ends_at: string | null }): string {
  const start = ist(startMs(event))
  const endMs = knownEndMs(event)
  if (endMs === null) return `${dayLabel(start)} · ${clock(start)} IST`
  const end = ist(endMs)
  if (istDay(endMs) === istDay(startMs(event))) return `${dayLabel(start)} · ${clock(start)}–${clock(end)} IST`
  return `${dayLabel(start)} ${clock(start)} – ${dayLabel(end)} ${clock(end)} IST`
}
