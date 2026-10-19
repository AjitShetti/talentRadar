/**
 * Practice clocks. Every time on a Practice page is shown in IST, the clock the schedule is set
 * in (challenge Fri 18:00, contest Sun 08:00), whatever zone the browser is in.
 */

const IST = 'Asia/Kolkata'

// FlapText lays its characters out in a flex row, which collapses ordinary spaces; a
// non-breaking space survives, so "31 h 12 m" keeps its gaps.
export const NBSP = String.fromCharCode(0xa0)

function istParts(iso: string | null | undefined) {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: IST, weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date)
  const pick = (type: string) => parts.find(part => part.type === type)?.value ?? ''
  return { weekday: pick('weekday'), day: pick('day'), month: pick('month'), time: `${pick('hour')}:${pick('minute')}` }
}

/** "08:41" */
export const clock = (iso: string | null | undefined) => istParts(iso)?.time ?? ''

/** "Sun 08:00 IST" */
export function weekdayTime(iso: string | null | undefined) {
  const p = istParts(iso)
  return p ? `${p.weekday} ${p.time} IST` : ''
}

/** "Fri 23 Oct 18:00 IST" */
export function dayTime(iso: string | null | undefined) {
  const p = istParts(iso)
  return p ? `${p.weekday} ${p.day} ${p.month} ${p.time} IST` : ''
}

/** "09 OCT" — the IST date a challenge opened. */
export function shortDate(iso: string | null | undefined) {
  const p = istParts(iso)
  return p ? `${p.day} ${p.month}`.toUpperCase() : ''
}

/** "2 d 14 h" (days on) or "31 h 12 m"; below an hour, "41 m". */
export function duration(ms: number, withDays: boolean) {
  const minutes = Math.max(0, Math.floor(ms / 60000))
  const days = Math.floor(minutes / 1440)
  if (withDays && days > 0) return `${days} d ${Math.floor((minutes % 1440) / 60)} h`
  const hours = Math.floor(minutes / 60)
  return hours > 0 ? `${hours} h ${minutes % 60} m` : `${minutes} m`
}
