'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import AddCompany, { AddChoice } from '@/components/outreach/AddCompany'
import { track } from '@/lib/analytics'
import { api, MetAtEvent } from '@/lib/api'
import { alreadyRecorded, findMet, metCompany, MetCompany, metPersonFields, metTouch, personNamed } from '@/lib/outreach-met'

type Props = {
  /** `event_contact:<uuid>`, from /outreach?add= (already checked by `metRef`). */
  reference: string
  /** The person is in a thread with the meeting on its timeline: open that thread on them. */
  onDone: (threadId: string, personId: string) => void
  /** Leave without importing. */
  onCancel: () => void
}

type Phase =
  | { kind: 'finding' }
  | { kind: 'missing' }
  | { kind: 'company'; met: MetAtEvent }
  | { kind: 'working'; met: MetAtEvent }
  | { kind: 'failed'; met: MetAtEvent | null; message: string }

// GET /events/people-met returns oldest first, at most this many a page (services/events/met.py::MAX_LIMIT).
const PAGE_SIZE = 500
const MAX_PAGES = 6

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)
const statusOf = (err: unknown): number | null =>
  typeof err === 'object' && err !== null && 'status' in err && typeof (err as { status: unknown }).status === 'number' ? (err as { status: number }).status : null

/** The row this link names. `since` is inclusive, so a page that cannot move forward ends the search. */
async function findPersonMet(reference: string): Promise<MetAtEvent | null> {
  let since: string | undefined
  for (let page = 0; page < MAX_PAGES; page++) {
    const rows = await api.events.peopleMet(since, PAGE_SIZE)
    const found = findMet(rows, reference)
    if (found) return found
    const last = rows[rows.length - 1]
    if (rows.length < PAGE_SIZE || !last || last.created_at === since) return null
    since = last.created_at
  }
  return null
}

/**
 * A thread with the company (found if it exists), the person in it (once), and the meeting on their
 * timeline (once). Every step reads before it writes, so running it twice changes nothing the second time.
 * Nothing is sent: the touch records a conversation that already happened.
 */
async function importMet(met: MetAtEvent, company: MetCompany): Promise<{ threadId: string; personId: string }> {
  const { thread, created } = await api.outreach.create({ ...company, source: 'typed' })
  if (created) track('outreach_added', { source: 'typed' })

  let detail = await api.outreach.thread(thread.id)
  let person = personNamed(detail.people, met.name)
  if (!person) {
    try {
      detail = await api.outreach.addPerson(thread.id, metPersonFields(met))
    } catch (err) {
      const status = statusOf(err)
      if (status === 409) detail = await api.outreach.thread(thread.id) // added meanwhile, from another tab
      else if (status === 400 || status === 422) detail = await api.outreach.addPerson(thread.id, metPersonFields(met, false)) // an address or link the server will not take
      else throw err
    }
    person = personNamed(detail.people, met.name)
  }
  if (!person) throw new Error('Could not add this person to the thread.')

  if (!alreadyRecorded(detail.touches, person.id, met)) {
    await api.outreach.touch(thread.id, metTouch(person.id, met))
  }
  return { threadId: thread.id, personId: person.id }
}

// "Start a thread" on a person met at an event (/outreach?add=event_contact:<uuid>): reads the person from
// the user's own people met (api.events.peopleMet), asks for the company only when none was typed, and
// opens the thread on them.
export default function MetPersonImport({ reference, onDone, onCancel }: Props) {
  const [phase, setPhase] = useState<Phase>({ kind: 'finding' })
  const started = useRef(false)

  const run = useCallback(async (met: MetAtEvent, company: MetCompany) => {
    setPhase({ kind: 'working', met })
    try {
      const done = await importMet(met, company)
      onDone(done.threadId, done.personId)
    } catch (err) {
      setPhase({ kind: 'failed', met, message: messageOf(err, 'Could not add this person to your outreach.') })
    }
  }, [onDone])

  const find = useCallback(async () => {
    setPhase({ kind: 'finding' })
    try {
      const met = await findPersonMet(reference)
      if (!met) { setPhase({ kind: 'missing' }); return }
      const company = metCompany(met)
      if (company) await run(met, company)
      else setPhase({ kind: 'company', met })
    } catch (err) {
      setPhase({ kind: 'failed', met: null, message: messageOf(err, 'Could not read the people you met.') })
    }
  }, [reference, run])

  // Once per link: a development build mounts effects twice, and two runs at once could record the meeting twice.
  useEffect(() => {
    if (started.current) return
    started.current = true
    void find()
  }, [find])

  async function chooseCompany(met: MetAtEvent, choice: AddChoice): Promise<boolean> {
    await run(met, 'companyId' in choice ? { company_id: choice.companyId } : { company_name: choice.companyName })
    return false
  }

  return <section className="or-section or-met" role="status" aria-live="polite">
    <div className="or-head"><h2>Person you met</h2><span className="or-rule" /></div>
    {phase.kind === 'finding' && <div className="loading-state"><Loader2 className="spin" /> Finding them in the people you met&hellip;</div>}
    {phase.kind === 'working' && <div className="loading-state"><Loader2 className="spin" /> Adding {phase.met.name} to your outreach&hellip;</div>}
    {phase.kind === 'missing' && <>
      <p>That person is no longer in the people you met. They may have been removed from the event.</p>
      <div className="or-met-actions"><button type="button" className="outline-button" onClick={onCancel}>Dismiss</button></div>
    </>}
    {phase.kind === 'company' && <>
      <p>Which company does {phase.met.name} work at? A thread belongs to a company.</p>
      <AddCompany onAdd={choice => chooseCompany(phase.met, choice)} />
      <div className="or-met-actions"><button type="button" className="outline-button" onClick={onCancel}>Cancel</button></div>
    </>}
    {phase.kind === 'failed' && <>
      <p className="form-error">{phase.message}</p>
      <div className="or-met-actions">
        <button type="button" className="outline-button" onClick={() => { const { met } = phase; if (met) { const company = metCompany(met); if (company) void run(met, company); else setPhase({ kind: 'company', met }) } else void find() }}>Try again</button>
        <button type="button" className="outline-button" onClick={onCancel}>Dismiss</button>
      </div>
    </>}
  </section>
}
