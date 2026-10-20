// Pure rules for "Start a thread" on a person met at an event: /outreach?add=event_contact:<uuid>.
// No fetching and no storage. The page reads GET /events/people-met (api.events.peopleMet), finds
// the row this link names, and turns it into a thread, a person and one touch on the channel
// "event". The server holds no idempotency key for a touch, so `source_ref` is honoured here:
// a person already in the thread with the same touch is never added or recorded twice.
import type { MetAtEvent, OutreachPerson, OutreachTouch } from '@/lib/api'

const REF = /^event_contact:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** The `add` query value when it names a person met (`event_contact:<uuid>`); null for anything else. */
export function metRef(value: string | null): string | null {
  return value !== null && REF.test(value) ? value : null
}

/** The person this link names among the rows people-met returned, or null. */
export function findMet(rows: readonly MetAtEvent[], ref: string): MetAtEvent | null {
  return rows.find(row => row.source_ref.toLowerCase() === ref.toLowerCase()) ?? null
}

/** A company on the map (by id) or a typed name for the thread; null when the user never said where they work. */
export type MetCompany = { company_id: string } | { company_name: string }

export function metCompany(met: MetAtEvent): MetCompany | null {
  if (met.company_id) return { company_id: met.company_id }
  const name = met.company_name?.trim()
  return name ? { company_name: name } : null
}

// How the server compares names inside a thread: case, spacing and Unicode form do not make two people.
const nameKey = (name: string) => name.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()

/** The person already in the thread under this name, or null. */
export function personNamed(people: readonly OutreachPerson[], name: string): OutreachPerson | null {
  const key = nameKey(name)
  return people.find(person => nameKey(person.name) === key) ?? null
}

/**
 * What the thread keeps of a person met. Outreach stores a title, an email and a LinkedIn profile; the
 * X and GitHub links Events holds have nowhere to go, and nothing is guessed. `links: false` leaves out
 * the two the server may refuse (an address or a link it will not accept), so a bad one cannot stop the add.
 */
export function metPersonFields(met: MetAtEvent, links = true): { name: string; title?: string; email?: string; linkedin_url?: string } {
  return {
    name: met.name,
    ...(met.title ? { title: met.title } : {}),
    ...(links && met.email ? { email: met.email } : {}),
    ...(links && met.linkedin_url ? { linkedin_url: met.linkedin_url } : {}),
  }
}

/** Has this meeting already been recorded for this person? Same person, channel "event" and the same line. */
export function alreadyRecorded(touches: readonly OutreachTouch[], personId: string, met: MetAtEvent): boolean {
  return touches.some(touch => touch.person_id === personId && touch.channel === 'event' && touch.note === met.context_line)
}

const DAY_MS = 24 * 60 * 60 * 1000
// domain/outreach.py: a touch is never dated in the future and at most this many days back.
const BACKDATE_MAX_DAYS = 30
const IST = '+05:30'

/**
 * When the meeting happened: the start of the event's day in India, as an instant. The server refuses a
 * touch dated in the future or more than 30 days back, so for an event outside that window this is
 * undefined and the touch is dated now; its note still names the day.
 */
export function metHappenedAt(metOn: string, now: Date = new Date()): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(metOn)) return undefined
  const at = new Date(`${metOn}T00:00:00${IST}`)
  if (Number.isNaN(at.getTime())) return undefined
  const age = now.getTime() - at.getTime()
  return age >= 0 && age <= BACKDATE_MAX_DAYS * DAY_MS ? at.toISOString() : undefined
}

/** The touch recorded for a person met: a conversation that happened, never a message sent. */
export function metTouch(personId: string, met: MetAtEvent, now: Date = new Date()) {
  return {
    person_id: personId,
    channel: 'event' as const,
    direction: 'in' as const,
    note: met.context_line,
    happened_at: metHappenedAt(met.met_on, now),
  }
}
