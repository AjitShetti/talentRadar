import type { OutreachPack, PackAddress, PackItem } from './api'

/**
 * Pure rules for today's outreach pack (components/outreach/TodayPack.tsx).
 *
 * Nothing here sends anything: an email opens in the user's own mail client through a
 * mailto link, and "I sent it" only records what the user says they did.
 */

// The thread draft's mailto rule (RFC 6068: each field encoded on its own, CRLF line breaks), shared.
export { mailtoHref } from './outreach'

export type AddressBadge = { label: 'found' | 'likely, unverified' | 'saved'; hint: string }

/** What the badge beside an address says: where it came from, and on what evidence. A guess always says so. */
export function addressBadge(address: PackAddress): AddressBadge {
  if (address.basis === 'found') return { label: 'found', hint: address.source_host ? `on ${address.source_host}` : '' }
  if (address.basis === 'saved') return { label: 'saved', hint: 'you saved it' }
  return {
    label: 'likely, unverified',
    hint: address.learned_from ? `format learned from ${address.learned_from}` : "from the company's email format",
  }
}

/** An item still waiting on the user whose text has not been written yet. */
export const unwritten = (item: PackItem) => item.status === 'new' && item.message === null

/** Companies, in pack order, that still have a message to write. */
export const companiesToWrite = (pack: OutreachPack): string[] =>
  pack.companies.filter(c => c.company_id !== null && c.items.some(unwritten)).map(c => c.company_id as string)

/** Due follow-ups whose text has not been written yet. */
export const followUpsToWrite = (pack: OutreachPack): string[] => pack.follow_ups.filter(unwritten).map(i => i.id)

/** Every item the page shows, for marking them seen. Skipped items are not shown. */
export const shownItemIds = (pack: OutreachPack): string[] =>
  [...pack.companies.flatMap(c => c.items), ...pack.follow_ups].filter(i => i.status !== 'skipped').map(i => i.id)
