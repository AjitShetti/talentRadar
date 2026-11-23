import { externalHref } from './safe-url'

/**
 * What an event says about entry, and where "Register" goes (spec 2026-11-23, 4.3 and 4.5).
 * `price` is the source's own wording ("₹500", "₹500.00–₹1,500.00"); a free event has none.
 */
type Entry = { is_free: boolean | null; price?: string | null }

/** Free, the price, Paid, or "Entry not stated": the drawer always says one. */
export function entryLabel(event: Entry): string {
  return entryCode(event) ?? 'Entry not stated'
}

/** The row's short code: only what the source stated, else nothing. */
export function entryCode(event: Entry): string | null {
  if (event.is_free === true) return 'Free'
  if (event.price) return event.price
  if (event.is_free === false) return 'Paid'
  return null
}

/** The registration page when the source names one on another site, else the event page. */
export function registerHref(event: { register_url?: string | null; url: string | null }): string | null {
  return externalHref(event.register_url) ?? externalHref(event.url)
}
