'use client'

import Link from 'next/link'
import { MouseEvent, useEffect, useRef, useState } from 'react'
import { track } from '@/lib/analytics'
import { api, ThreadSummary } from '@/lib/api'
import { STAGE_LABEL, threadHref, threadLead, threadTone } from '@/lib/outreach'

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)
const openHref = (thread: ThreadSummary) => threadHref({ threadId: thread.id, personId: null, mode: null })

type State =
  | { status: 'loading' }
  | { status: 'error' }
  // `added` is true only for the thread this drawer has just opened.
  | { status: 'ready'; thread: ThreadSummary | null; added: boolean }

/**
 * The slim "Your outreach" block that sits first in the company drawer: whether this
 * company is in the user's outreach and, when it is, where the thread stands.
 * Adding records a plan only; nothing is sent.
 */
export default function CompanyOutreachBlock({ companyId }: { companyId: string }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  // The drawer can move to another company while a request is out; its answer is then not ours to show.
  const currentId = useRef(companyId)
  useEffect(() => { currentId.current = companyId })

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' }); setError('')
    api.outreach.forCompany(companyId)
      .then(({ thread }) => { if (!cancelled) setState({ status: 'ready', thread, added: false }) })
      .catch(() => { if (!cancelled) setState({ status: 'error' }) })
    return () => { cancelled = true }
  }, [companyId, attempt])

  async function add() {
    if (adding) return
    const target = companyId
    setAdding(true); setError('')
    try {
      const { thread, created } = await api.outreach.create({ company_id: target, source: 'drawer' })
      if (created) track('outreach_added', { source: 'drawer' })
      if (currentId.current === target) setState({ status: 'ready', thread, added: created })
    } catch (err) {
      if (currentId.current === target) setError(messageOf(err, 'Could not add this company to your outreach.'))
    } finally { setAdding(false) }
  }

  const thread = state.status === 'ready' ? state.thread : null
  const added = state.status === 'ready' && state.added

  // One live root, so a change of state is announced.
  return <>
    <div className="or-block" data-tone={thread ? threadTone(thread) : 'idle'} aria-live="polite" aria-busy={state.status === 'loading'}>
      <span className="or-lamp" aria-hidden />
      {state.status === 'loading' && <span>Checking your outreach…</span>}
      {state.status === 'error' && <>
        <span>Could not check your outreach.</span>
        <button type="button" className="text-button" onClick={() => setAttempt(n => n + 1)}>Try again</button>
      </>}
      {state.status === 'ready' && !thread && <>
        <span>Not in your outreach yet.</span>
        <button type="button" className="primary-button" onClick={add} disabled={adding}>{adding ? 'Adding…' : 'Add to outreach'}</button>
      </>}
      {thread && added && <>
        <span>Added.</span>
        <Link className="text-button" href={openHref(thread)}>Open the thread →</Link>
      </>}
      {thread && !added && <>
        <span><b>{STAGE_LABEL[thread.stage]}</b>{threadLead(thread)}</span>
        <Link className="text-button" href={openHref(thread)}>Open thread →</Link>
      </>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
  </>
}

type Phase = 'idle' | 'adding' | 'added' | 'existing' | 'failed'

const LIST_LABEL: Record<Phase, string> = {
  idle: '+ Outreach',
  adding: 'Adding…',
  added: 'In outreach',
  existing: 'Already in outreach',
  failed: 'Could not add. Retry',
}

/**
 * The List row's "+ Outreach". It sits beside the row's own button, never inside it,
 * and the create call is idempotent: a company already in outreach says so.
 */
export function ListOutreachButton({ companyId, companyName }: { companyId: string; companyName: string }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const open = phase === 'idle' || phase === 'failed'

  async function add(event: MouseEvent<HTMLButtonElement>) {
    // The row beside it opens the drawer; this must not.
    event.stopPropagation()
    if (!open) return
    setPhase('adding')
    try {
      const { created } = await api.outreach.create({ company_id: companyId, source: 'list' })
      if (created) track('outreach_added', { source: 'list' })
      setPhase(created ? 'added' : 'existing')
    } catch {
      setPhase('failed')
    }
  }

  // aria-disabled, not disabled, so a keyboard user's focus stays on the button while it works.
  return <button
    type="button"
    className="text-button cm-row-outreach"
    onClick={add}
    aria-disabled={!open}
    aria-live="polite"
    aria-label={phase === 'idle' ? `Add ${companyName} to outreach` : `${LIST_LABEL[phase]}: ${companyName}`}
  >{LIST_LABEL[phase]}</button>
}
