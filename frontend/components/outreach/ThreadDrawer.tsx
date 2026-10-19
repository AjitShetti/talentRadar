'use client'

import Link from 'next/link'
import { FormEvent, ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react'
import { Loader2, MoreHorizontal, X } from 'lucide-react'
import DraftPanel, { SnapshotIssue } from '@/components/outreach/DraftPanel'
import { track } from '@/lib/analytics'
import {
  api, OutreachChannel, OutreachChannelName, OutreachOutcome, OutreachPerson, OutreachTouch, SuggestedPerson, ThreadDetail,
} from '@/lib/api'
import {
  canMarkSent, CHANNEL_LABEL, channelHref, companySectorLabel, companyStageLabel, dayMonth, followUpWait, initials, isStaleError,
  KIND_LABEL, MAX_FOLLOW_UPS, NEXT_STEP_DONE, NEXT_STEP_MAX, nextStepPatch, NOTE_MAX, openSourceNote, OUTCOMES, personLine, personTone,
  stamp, STAGE_LABEL, touchChannel,
} from '@/lib/outreach'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'
import { useDialog } from '@/lib/use-dialog'

const SNOOZE_DAYS = 3

// The detail api/routers/applications.py sends with its 409 for a job the user already tracks.
const APPLICATION_EXISTS = 'Application already exists'

type Props = {
  threadId: string
  /** The person the link named (?person=), scrolled to when the thread opens. */
  focusPersonId: string | null
  /** Open that person's draft panel when the thread loads (?draft=1). */
  openDraft: boolean
  /** Open that person's reply form when the thread loads (?reply=1). */
  openReply?: boolean
  onClose: () => void
  /** Called with the thread as the server holds it after every change, so the page can reload its lists. */
  onChanged: (detail: ThreadDetail) => void
  /** Called once the thread has been deleted, just before the drawer closes. */
  onDeleted?: () => void
}

// One inline panel at a time: a draft or a reply for a person, or a comment on an issue.
type Panel = { kind: 'draft' | 'reply'; personId: string } | { kind: 'issue'; url: string }

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)
const personDomId = (id: string) => `or-person-${id}`

function touchTitle(t: OutreachTouch): string {
  const who = t.person_name
  if (t.kind === 'reply') {
    if (t.channel === 'call' || t.channel === 'event') return who ? `Spoke with ${who}` : 'A conversation'
    return who ? `${who} replied` : 'A reply'
  }
  const base = t.kind === 'first' ? 'First message' : t.kind === 'follow_up' ? 'Follow-up' : 'Message'
  return who ? `${base} to ${who}` : base
}

export default function ThreadDrawer({ threadId, focusPersonId, openDraft, openReply = false, onClose, onChanged, onDeleted }: Props) {
  const [detail, setDetail] = useState<ThreadDetail | null>(null)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [panel, setPanel] = useState<Panel | null>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const rootRef = useRef<HTMLElement>(null)
  const dirtyRef = useRef(false)
  const openedRef = useRef(false)
  const onChangedRef = useRef(onChanged)
  const onDeletedRef = useRef(onDeleted)
  useEffect(() => { onChangedRef.current = onChanged; onDeletedRef.current = onDeleted })
  const titleId = useId()

  // An edited draft cost one of the day's twenty, so closing the drawer over one asks first.
  const discardOk = useCallback(() => !dirtyRef.current || window.confirm('Discard the draft you are writing?'), [])
  const requestClose = useCallback(() => { if (discardOk()) onClose() }, [discardOk, onClose])
  const onKeyDown = useDialog(rootRef, requestClose, { modal: true, closeOnEscape: true })

  // The page behind must not scroll while the drawer owns the screen.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])

  useEffect(() => {
    let cancelled = false
    api.outreach.thread(threadId)
      .then(result => { if (!cancelled) setDetail(result) })
      .catch(err => { if (!cancelled) setLoadError(messageOf(err, 'Could not load this thread.')) })
    return () => { cancelled = true }
  }, [threadId])

  // A link can name a person and ask for their draft or reply; that is honoured once, when the thread first loads.
  useEffect(() => {
    if (!detail || openedRef.current) return
    openedRef.current = true
    const person = focusPersonId ? detail.people.find(p => p.id === focusPersonId) : undefined
    if (!person) return
    if (openDraft && person.draft) setPanel({ kind: 'draft', personId: person.id })
    else if (openReply && person.state !== 'replied' && person.state !== 'dropped') setPanel({ kind: 'reply', personId: person.id })
    else document.getElementById(personDomId(person.id))?.scrollIntoView({ block: 'nearest' })
  }, [detail, focusPersonId, openDraft, openReply])

  const apply = useCallback((next: ThreadDetail) => {
    setDetail(next)
    onChangedRef.current(next)
  }, [])

  // Reads the thread again after a refusal that says this drawer is out of date (the person replied in another tab,
  // the person was added already, the thread is gone), so the buttons match the server. A panel for someone who
  // can no longer be written to or answered closes with it; a failed read leaves the refusal on screen as it is.
  const refresh = useCallback(async () => {
    try {
      const next = await api.outreach.thread(threadId)
      apply(next)
      setPanel(current => {
        if (!current || current.kind === 'issue') return current
        const person = next.people.find(p => p.id === current.personId)
        const stillOpen = current.kind === 'draft' ? Boolean(person?.draft) : Boolean(person && person.state !== 'replied' && person.state !== 'dropped')
        return stillOpen ? current : null
      })
    } catch { /* gone or unreachable: the message already shown says so */ }
  }, [threadId, apply])

  // A refusal that means the thread has moved on without this tab keeps its message and reads the thread again.
  const staleFailure = useCallback((message: string) => { setActionError(message); void refresh() }, [refresh])

  // One change at a time, so a double click is one touch and not two. Resolves true when the change was made.
  // A failure is shown at the top of the drawer unless the caller shows it somewhere nearer.
  async function run(key: string, work: () => Promise<ThreadDetail | null>, failure: string, onFail: (message: string) => void = setActionError): Promise<boolean> {
    if (pending) return false
    setPending(key); setActionError(''); setNotice('')
    try {
      const next = await work()
      if (next) apply(next)
      return true
    } catch (err) {
      const message = messageOf(err, failure)
      onFail(message)
      if (isStaleError(err)) staleFailure(message)
      return false
    } finally { setPending(null) }
  }

  // Closes a panel and puts focus back on the person it was open for.
  function closePanel(personId?: string) {
    setPanel(null)
    if (personId) requestAnimationFrame(() => document.getElementById(personDomId(personId))?.focus())
  }

  function openPanel(next: Panel) {
    if (!discardOk()) return
    setMenuFor(null)
    setPanel(next)
  }

  // The draft panel's "I sent it": the same guard as the row button, so the two can never overlap.
  const sentFromDraft = (personId: string | null, closeAfter: () => void) =>
    (touch: { channel: OutreachChannelName; body?: string }, onFail: (message: string) => void) =>
      run(`sent:${personId ?? 'issue'}`, async () => {
        const next = await api.outreach.touch(threadId, { person_id: personId, channel: touch.channel, direction: 'out', body: touch.body })
        closeAfter()
        return next
      }, 'Could not record that.', onFail)

  const iSentIt = (p: OutreachPerson) => run(`sent:${p.id}`, async () => {
    const channel = touchChannel(p)
    const next = await api.outreach.touch(threadId, { person_id: p.id, channel, direction: 'out' })
    track('outreach_sent', { channel })
    return next
  }, 'Could not record that.')

  const snooze = (p: OutreachPerson) => run(`snooze:${p.id}`, async () => {
    const next = await api.outreach.updatePerson(threadId, p.id, { snooze_days: SNOOZE_DAYS })
    setMenuFor(null)
    return next
  }, 'Could not snooze that.')

  async function removePerson(p: OutreachPerson) {
    if (!window.confirm(`Take ${p.name} out of this thread? What you recorded stays on the timeline.`)) return
    const done = await run(`remove:${p.id}`, () => api.outreach.removePerson(threadId, p.id), 'Could not remove them.')
    if (done) { setMenuFor(null); if (panel && 'personId' in panel && panel.personId === p.id) setPanel(null) }
  }

  const recordReply = (p: OutreachPerson, fields: { note: string; nextStep: string; nextStepAt: string }) =>
    run(`reply:${p.id}`, async () => {
      const next = await api.outreach.touch(threadId, {
        person_id: p.id,
        channel: touchChannel(p),
        direction: 'in',
        note: fields.note.trim() || undefined,
        next_step: fields.nextStep.trim() || undefined,
        next_step_at: fields.nextStepAt || undefined,
      })
      closePanel(p.id)
      return next
    }, 'Could not record the reply.')

  const addSuggested = (s: SuggestedPerson) => run(`add:${s.contact_id}`, () => api.outreach.addPerson(threadId, { contact_id: s.contact_id }), 'Could not add them.')

  // The server links someone typed in to a contact it already holds under that name; when it did, say so.
  function addKnown(fields: { name: string; title: string; email: string; linkedin: string }) {
    const before = detail
    return run('add-known', async () => {
      const next = await api.outreach.addPerson(threadId, {
        name: fields.name.trim(),
        title: fields.title.trim() || undefined,
        email: fields.email.trim() || undefined,
        linkedin_url: fields.linkedin.trim() || undefined,
      })
      const known = new Set(before?.people.map(p => p.id))
      const added = next.people.find(p => !known.has(p.id))
      if (added?.contact_id && before?.suggested_people.some(s => s.contact_id === added.contact_id)) {
        setNotice(`Linked to the saved contact ${added.name}.`)
      }
      return next
    }, 'Could not add them.')
  }

  // A role the user already tracks is linked, not refused: the server answers a second create with a 409 and no id.
  async function applicationFor(jobId: string): Promise<{ id: string }> {
    try {
      return await api.applications.create(jobId, 'saved')
    } catch (err) {
      if (!(err instanceof Error) || err.message !== APPLICATION_EXISTS) throw err
      const existing = (await api.roles.dossier(jobId)).readiness?.application
      if (!existing) throw err
      return existing
    }
  }

  const trackRole = () => run('track', async () => {
    const jobId = detail?.job?.id
    if (!jobId) return null
    const application = await applicationFor(jobId)
    return api.outreach.update(threadId, { application_id: application.id })
  }, 'Could not track this role.')

  const saveNote = (note: string) => run('note', () => api.outreach.update(threadId, { note: note.trim() || null }), 'Could not save the note.')
  const saveNextStep = (text: string, date: string) => run('next-step', () => api.outreach.update(threadId, nextStepPatch(text, date)), 'Could not save the next step.')
  const finishNextStep = () => run('next-step', () => api.outreach.update(threadId, NEXT_STEP_DONE), 'Could not mark that done.')

  const closeThread = (outcome: OutreachOutcome) => run('close', () => api.outreach.update(threadId, { close: { outcome } }), 'Could not close this thread.')
  const reopenThread = () => run('reopen', () => api.outreach.update(threadId, { reopen: true }), 'Could not reopen this thread.')

  async function deleteThread() {
    if (!window.confirm('Delete this thread and everything recorded in it? This cannot be undone.')) return
    const done = await run('delete', async () => { await api.outreach.remove(threadId); return null }, 'Could not delete this thread.')
    if (done) {
      dirtyRef.current = false
      onDeletedRef.current?.()
      onClose()
    }
  }

  const name = detail?.company_name ?? ''
  const busy = pending !== null
  const issues = detail?.issues?.issues ?? []
  const ossNote = detail ? openSourceNote(detail) : null
  const noCompany = detail !== null && detail.company_id === null

  return <>
    <button type="button" className="ci-backdrop cm-over" onClick={requestClose} aria-label="Close thread" />
    <aside
      ref={rootRef}
      className="ci-drawer cm-over"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      data-testid="thread-drawer"
      onKeyDown={onKeyDown}
    >
      <header className="ci-drawer-head">
        <span className="ci-logo" aria-hidden>{name ? initials(name) : ''}</span>
        <div className="ci-drawer-title">
          <h2 id={titleId}>{name || 'Thread'}</h2>
          {detail && <>
            {detail.company_summary && <p className="cm-summary">{detail.company_summary}</p>}
            <div className="or-chips">
              <span className="or-chip">{STAGE_LABEL[detail.stage]}{detail.stage === 'closed' && detail.outcome ? ` · ${OUTCOMES.find(o => o.value === detail.outcome)?.label ?? detail.outcome}` : ''}</span>
              {detail.company?.stage && <span className="or-chip">{companyStageLabel(detail.company.stage)}</span>}
              {detail.company?.sector && <span className="or-chip">{companySectorLabel(detail.company.sector)}</span>}
              {detail.job && (detail.job.id
                ? <Link className="or-chip" href={`/roles/${encodeURIComponent(detail.job.id)}`}>Role · {detail.job.title || 'Open role'}</Link>
                : <span className="or-chip">Role · {detail.job.title || 'Open role'}</span>)}
              {detail.application && <Link className="or-chip" href="/applications">Application · {detail.application.status || 'saved'}</Link>}
            </div>
            {detail.job?.id && !detail.application && <div className="or-actions" style={{ marginTop: 10 }}>
              <button type="button" className="outline-button" onClick={trackRole} disabled={busy}>Track this role</button>
            </div>}
            <ThreadNote value={detail.note} busy={busy} onSave={saveNote} />
          </>}
        </div>
        <button type="button" className="ci-close" onClick={requestClose} aria-label="Close" data-autofocus><X size={18} /></button>
      </header>

      <div className="ci-drawer-body">
        {loadError && <p className="form-error" style={{ marginTop: 18 }}>{loadError}</p>}
        {!detail && !loadError && <div className="cm-drawer-loading" aria-busy="true"><Loader2 size={16} className="spin" />Loading…</div>}
        {actionError && <p className="form-error" role="alert" style={{ marginTop: 18 }}>{actionError}</p>}
        {notice && <p className="or-hint" role="status" style={{ marginTop: 18 }}>{notice}</p>}

        {detail && <>
          <section className="ci-section" aria-label="People in this thread">
            <h3>People in this thread</h3>
            {detail.pacing_note && <p className="or-pacing">{detail.pacing_note}</p>}
            {detail.people.length === 0 && <p className="ci-empty-small">
              {noCompany
                ? `No one yet. Add someone you know at ${detail.company_name}.`
                : detail.suggested_people.length > 0
                  ? 'No one added yet. Pick someone below or add someone you know.'
                  : `No one on record at ${detail.company_name}. Search for them below or add someone you know.`}
            </p>}
            {detail.people.map(p => <PersonRow
              key={p.id}
              person={p}
              busy={busy}
              menuOpen={menuFor === p.id}
              panel={panel && 'personId' in panel && panel.personId === p.id ? panel.kind : null}
              onDraft={() => openPanel({ kind: 'draft', personId: p.id })}
              onReplyForm={() => openPanel({ kind: 'reply', personId: p.id })}
              onSent={() => iSentIt(p)}
              onMenu={() => setMenuFor(menuFor === p.id ? null : p.id)}
              onSnooze={() => snooze(p)}
              onRemove={() => removePerson(p)}
              onCancelPanel={() => closePanel(p.id)}
              onReply={fields => recordReply(p, fields)}
              draftPanel={<DraftPanel
                thread={detail}
                person={p}
                issue={null}
                busy={busy}
                onSend={sentFromDraft(p.id, () => closePanel(p.id))}
                onCancel={() => closePanel(p.id)}
                onDirtyChange={dirty => { dirtyRef.current = dirty }}
                onStale={staleFailure}
              />}
            />)}
          </section>

          <section className="ci-section" aria-label="Who else to add">
            <h3>Who else to add</h3>
            {noCompany && <p className="or-hint">We don&apos;t have {detail.company_name} on the map, so there are no people or issues to suggest. Add someone you know.</p>}
            {detail.suggested_people.map(s => <div className="or-person" key={s.contact_id} data-tone="idle">
              <span className="or-lamp" aria-hidden />
              <span className="or-avatar" aria-hidden>{initials(s.name)}</span>
              <div>
                <strong>{s.name}{s.title && <span> · {s.title}</span>}</strong>
                <small>
                  {KIND_LABEL[s.kind] ?? s.kind}
                  <span className={`ci-badge ${s.verified ? 'verified' : 'unverified'}`}>{s.verified ? 'verified' : 'unverified'}</span>
                  {s.user_saved ? ' · saved by you' : s.source_host ? ` · Source: ${s.source_host}` : ''}
                </small>
                <small>{s.reason}</small>
                <div className="or-actions">
                  <button type="button" className="outline-button" onClick={() => addSuggested(s)} disabled={busy} aria-label={`Add ${s.name} to this thread`}>Add</button>
                </div>
              </div>
            </div>)}
            {!noCompany && detail.suggested_people.length === 0 && detail.people.length > 0 && <p className="ci-empty-small">No one else on record.</p>}
            {(detail.linkedin_links.length > 0 || detail.careers_url) && <>
              <p className="or-hint" style={{ marginTop: 12 }}>Look for people yourself. These open in a new tab; we never load them.</p>
              <div className="or-channels">
                {detail.linkedin_links.map(link => <ExtLinkButton key={link.url} href={link.url}>{link.label}</ExtLinkButton>)}
                {detail.careers_url && <ExtLinkButton href={detail.careers_url}>Careers page</ExtLinkButton>}
              </div>
            </>}
            <AddKnownForm busy={busy} onAdd={addKnown} />
          </section>

          {ossNote && <section className="ci-section" aria-label="Open source">
            <h3>Open source</h3>
            <p className="or-hint">{ossNote}</p>
            {issues.map(issue => <IssueRow
              key={issue.url}
              issue={issue}
              open={panel?.kind === 'issue' && panel.url === issue.url}
              onDraft={() => openPanel({ kind: 'issue', url: issue.url })}
              panelNode={<DraftPanel
                thread={detail}
                person={null}
                issue={issue}
                busy={busy}
                onSend={sentFromDraft(null, () => setPanel(null))}
                onCancel={() => setPanel(null)}
                onDirtyChange={dirty => { dirtyRef.current = dirty }}
              />}
            />)}
          </section>}

          <section className="ci-section" aria-label="Timeline">
            <h3>Timeline</h3>
            <NextStep detail={detail} busy={busy} onSave={saveNextStep} onDone={finishNextStep} />
            {detail.touches.length === 0
              ? <p className="ci-empty-small">Nothing recorded yet. Each message you send and each reply lands here.</p>
              : <ol className="or-timeline">
                  {detail.touches.map(t => <li key={t.id}>
                    <time dateTime={t.happened_at}>{stamp(t.happened_at)}</time>
                    <div>
                      <b>{touchTitle(t)}</b> · {CHANNEL_LABEL[t.channel]}
                      {t.note && <><br />{t.note}</>}
                      {t.body && <details><summary>Kept copy</summary><p style={{ whiteSpace: 'pre-wrap' }}>{t.body}</p></details>}
                    </div>
                  </li>)}
                </ol>}
          </section>

          <footer className="or-foot">
            <CloseControl detail={detail} busy={busy} onClose={closeThread} onReopen={reopenThread} />
            <button type="button" className="outline-button danger-outline" style={{ marginLeft: 'auto' }} onClick={deleteThread} disabled={busy}>Delete thread</button>
          </footer>
        </>}
      </div>
    </aside>
  </>
}

function ExtLinkButton({ href, children }: { href: string; children: ReactNode }) {
  const safe = externalHref(href)
  if (!safe) return null
  return <a className="ci-link" href={safe} {...EXTERNAL_LINK_PROPS}>{children}</a>
}

function ChannelLink({ channel }: { channel: OutreachChannel }) {
  const href = channelHref(channel.url)
  if (!href) return null
  const external = !href.startsWith('mailto:')
  return <a className="ci-link" href={href} {...(external ? EXTERNAL_LINK_PROPS : {})}>{channel.label}</a>
}

type PersonRowProps = {
  person: OutreachPerson
  busy: boolean
  menuOpen: boolean
  panel: 'draft' | 'reply' | null
  onDraft: () => void
  onReplyForm: () => void
  onSent: () => void
  onMenu: () => void
  onSnooze: () => void
  onRemove: () => void
  onCancelPanel: () => void
  onReply: (fields: { note: string; nextStep: string; nextStepAt: string }) => Promise<boolean>
  draftPanel: ReactNode
}

// A person, where they stand, and the actions their state allows. Nothing here sends a message.
function PersonRow({ person: p, busy, menuOpen, panel, onDraft, onReplyForm, onSent, onMenu, onSnooze, onRemove, onCancelPanel, onReply, draftPanel }: PersonRowProps) {
  const menuId = useId()
  const canReply = p.state !== 'replied' && p.state !== 'dropped'
  const canSnooze = p.state === 'waiting' && p.follow_ups_sent < MAX_FOLLOW_UPS && p.next_follow_up_at !== null
  const wait = followUpWait(p)
  return <div className="or-person" id={personDomId(p.id)} tabIndex={-1} data-tone={personTone(p)}>
    <span className="or-lamp" aria-hidden />
    <span className="or-avatar" aria-hidden>{initials(p.name)}</span>
    <div>
      <strong>{p.name}{p.title && <span> · {p.title}</span>}</strong>
      <small>
        {KIND_LABEL[p.kind] ?? p.kind}
        <span className={`ci-badge ${p.verified ? 'verified' : 'unverified'}`}>{p.verified ? 'verified' : 'unverified'}</span>
        {p.user_saved ? ' · saved by you' : p.source_host ? ` · Source: ${p.source_host}` : ''}
      </small>
      {p.channels.length > 0 && <div className="or-channels">{p.channels.map(c => <ChannelLink key={`${c.channel}:${c.url}`} channel={c} />)}</div>}
      <span className="or-state">{personLine(p)}</span>
    </div>
    <div className="or-actions">
      {p.draft && <button type="button" className="outline-button" onClick={onDraft} disabled={busy} aria-expanded={panel === 'draft'} aria-label={`${p.draft === 'first' ? 'Draft first message' : 'Draft follow-up'} to ${p.name}`}>
        {p.draft === 'first' ? 'Draft first message' : 'Draft follow-up'}
      </button>}
      {wait && <span className="or-wait">{wait}</span>}
      {canMarkSent(p) && <button type="button" className="outline-button" onClick={onSent} disabled={busy} aria-label={`I sent it to ${p.name}`}>I sent it</button>}
      {canReply && <button type="button" className="outline-button" onClick={onReplyForm} disabled={busy} aria-expanded={panel === 'reply'} aria-label={`They replied (${p.name})`}>They replied</button>}
      <button type="button" className="outline-button" onClick={onMenu} aria-expanded={menuOpen} aria-controls={menuId} aria-label={`More actions for ${p.name}`}><MoreHorizontal size={14} /></button>
    </div>
    {menuOpen && <div className="or-actions" id={menuId}>
      {canSnooze && <button type="button" className="text-button" onClick={onSnooze} disabled={busy}>Snooze {SNOOZE_DAYS} days</button>}
      <button type="button" className="text-button danger" onClick={onRemove} disabled={busy}>Remove from thread</button>
    </div>}
    {panel === 'draft' && draftPanel}
    {panel === 'reply' && <ReplyForm busy={busy} onSubmit={onReply} onCancel={onCancelPanel} name={p.name} />}
  </div>
}

// "They replied": what they said in a line or two, and what happens next if anything.
function ReplyForm({ name, busy, onSubmit, onCancel }: {
  name: string
  busy: boolean
  onSubmit: (fields: { note: string; nextStep: string; nextStepAt: string }) => Promise<boolean>
  onCancel: () => void
}) {
  const [note, setNote] = useState('')
  const [nextStep, setNextStep] = useState('')
  const [nextStepAt, setNextStepAt] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const id = useId()
  useEffect(() => { ref.current?.querySelector<HTMLElement>('textarea')?.focus() }, [])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!busy) void onSubmit({ note, nextStep, nextStepAt })
  }

  return <div ref={ref}>
    <form className="ci-contact-form" onSubmit={submit} aria-label={`Record ${name}'s reply`}>
      <label className="ci-span2" htmlFor={`${id}-note`}>What did {name} say? <span className="ci-empty-small">{note.length} / {NOTE_MAX}</span></label>
      <textarea id={`${id}-note`} className="ci-span2" rows={3} maxLength={NOTE_MAX} value={note} onChange={e => setNote(e.target.value)} placeholder="Optional. Kept on the timeline." />
      <input aria-label="Next step" maxLength={NEXT_STEP_MAX} value={nextStep} onChange={e => setNextStep(e.target.value)} placeholder="Next step, e.g. Call on Thursday" />
      <input aria-label="Next step date" type="date" value={nextStepAt} onChange={e => setNextStepAt(e.target.value)} />
      <div className="ci-form-actions">
        <button type="submit" className="outline-button" disabled={busy}>{busy ? 'Saving…' : 'Save reply'}</button>
        <button type="button" className="text-button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  </div>
}

// "Add someone you know": a name is enough. The server saves them privately, or links the contact it already holds.
function AddKnownForm({ busy, onAdd }: { busy: boolean; onAdd: (fields: { name: string; title: string; email: string; linkedin: string }) => Promise<boolean> }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [linkedin, setLinkedin] = useState('')
  const [email, setEmail] = useState('')
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) nameRef.current?.focus() }, [open])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || !name.trim()) return
    if (await onAdd({ name, title, email, linkedin })) {
      setName(''); setTitle(''); setLinkedin(''); setEmail(''); setOpen(false)
    }
  }

  if (!open) return <div className="ci-contact-actions"><button type="button" className="outline-button" onClick={() => setOpen(true)}>Add someone you know</button></div>

  return <form className="ci-contact-form" onSubmit={submit} aria-label="Add someone you know">
    <input aria-label="Name" required maxLength={128} placeholder="Name" value={name} onChange={e => setName(e.target.value)} ref={nameRef} />
    <input aria-label="Title" maxLength={128} placeholder="Title, e.g. Staff engineer" value={title} onChange={e => setTitle(e.target.value)} />
    <input className="ci-span2" aria-label="LinkedIn profile link" maxLength={512} placeholder="LinkedIn profile link (optional)" value={linkedin} onChange={e => setLinkedin(e.target.value)} />
    <input className="ci-span2" aria-label="Email" type="email" maxLength={256} placeholder="Email (optional, only if they gave it to you)" value={email} onChange={e => setEmail(e.target.value)} />
    <div className="ci-form-actions">
      <button type="submit" className="outline-button" disabled={busy || !name.trim()}>{busy ? 'Saving…' : 'Add to this thread'}</button>
      <button type="button" className="text-button" onClick={() => setOpen(false)}>Cancel</button>
      <span className="ci-empty-small">Saved only to your account.</span>
    </div>
  </form>
}

function IssueRow({ issue, open, onDraft, panelNode }: { issue: SnapshotIssue; open: boolean; onDraft: () => void; panelNode: ReactNode }) {
  const href = externalHref(issue.url)
  return <div className="or-issue">
    {href ? <a href={href} {...EXTERNAL_LINK_PROPS}>{issue.title}</a> : <span>{issue.title}</span>}
    <small>
      {issue.repo}#{issue.number}
      {issue.labels.length > 0 && ` · ${issue.labels.join(', ')}`}
      {issue.updated_at && ` · updated ${dayMonth(issue.updated_at)}`}
      {` · ${issue.comments} ${issue.comments === 1 ? 'comment' : 'comments'}`}
    </small>
    <div className="or-actions" style={{ marginTop: 9 }}>
      <button type="button" className="outline-button" onClick={onDraft} aria-expanded={open}>Draft a comment</button>
    </div>
    {open && panelNode}
  </div>
}

// Why this company: the user's own line, kept on the thread and offered to the draft as context. Saved on request.
function ThreadNote({ value, busy, onSave }: { value: string | null; busy: boolean; onSave: (note: string) => Promise<boolean> }) {
  const saved = value ?? ''
  const [text, setText] = useState(saved)
  const baseline = useRef(saved)
  const id = useId()
  // A thread read again replaces what is shown only if the user has not started typing over it.
  useEffect(() => {
    setText(current => (current === baseline.current ? saved : current))
    baseline.current = saved
  }, [saved])
  const dirty = text.trim() !== saved.trim()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!busy && dirty) void onSave(text)
  }

  return <form className="or-note-form" onSubmit={submit} aria-label="Why this company">
    <label htmlFor={`${id}-note`}>Why this company <span className="ci-empty-small">{text.length} / {NOTE_MAX}</span></label>
    <textarea id={`${id}-note`} rows={2} maxLength={NOTE_MAX} value={text} onChange={e => setText(e.target.value)} placeholder="Optional. A line in your own words, offered to the draft." />
    {dirty && <div className="or-actions">
      <button type="submit" className="outline-button" disabled={busy}>Save note</button>
      <button type="button" className="text-button" onClick={() => setText(saved)}>Cancel</button>
    </div>}
  </form>
}

// The thread's next step: what the user said happens next, and when. Marking it done or changing it is
// always possible on an open thread; a reply recorded without one can be given one here.
function NextStep({ detail, busy, onSave, onDone }: {
  detail: ThreadDetail
  busy: boolean
  onSave: (text: string, date: string) => Promise<boolean>
  onDone: () => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [date, setDate] = useState('')
  const id = useId()
  const open = detail.stage !== 'closed'
  const has = Boolean(detail.next_step || detail.next_step_at)
  const when = detail.next_step_at ? dayMonth(detail.next_step_at) : ''
  if (!has && !(open && detail.stage === 'replied')) return null

  const edit = () => { setText(detail.next_step ?? ''); setDate(detail.next_step_at ?? ''); setEditing(true) }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || !text.trim()) return
    if (await onSave(text, date)) setEditing(false)
  }

  if (editing) {
    return <form className="ci-contact-form" onSubmit={submit} aria-label="Next step">
      <input id={`${id}-text`} aria-label="Next step" autoFocus maxLength={NEXT_STEP_MAX} value={text} onChange={e => setText(e.target.value)} placeholder="Next step, e.g. Call on Thursday" />
      <input aria-label="Next step date" type="date" value={date} onChange={e => setDate(e.target.value)} />
      <div className="ci-form-actions">
        <button type="submit" className="outline-button" disabled={busy || !text.trim()}>{busy ? 'Saving…' : 'Save next step'}</button>
        <button type="button" className="text-button" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </form>
  }

  return <div className="or-next">
    {has
      ? <p className="or-hint">Next step: <b>{detail.next_step || 'Not named'}</b>{when ? ` · ${when}` : ''}</p>
      : <p className="or-hint">No next step set.</p>}
    {open && <div className="or-actions">
      {has && <button type="button" className="outline-button" onClick={() => void onDone()} disabled={busy} aria-label="Mark the next step done">Done</button>}
      <button type="button" className="text-button" onClick={edit} disabled={busy} aria-label={has ? 'Edit the next step' : 'Add a next step'}>{has ? 'Edit' : 'Add a next step'}</button>
    </div>}
  </div>
}

// Close with an outcome, or reopen a closed thread.
function CloseControl({ detail, busy, onClose, onReopen }: {
  detail: ThreadDetail
  busy: boolean
  onClose: (outcome: OutreachOutcome) => Promise<boolean>
  onReopen: () => Promise<boolean>
}) {
  const [outcome, setOutcome] = useState<OutreachOutcome | ''>('')
  if (detail.stage === 'closed') {
    const label = OUTCOMES.find(o => o.value === detail.outcome)?.label
    return <>
      <span className="or-hint" style={{ margin: 0 }}>Closed{label ? ` · ${label}` : ''}</span>
      <button type="button" className="outline-button" onClick={() => void onReopen()} disabled={busy}>Reopen</button>
    </>
  }
  return <>
    <select aria-label="Outcome" value={outcome} onChange={e => setOutcome(e.target.value as OutreachOutcome | '')}>
      <option value="">How did it end?</option>
      {OUTCOMES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
    <button type="button" className="outline-button" onClick={() => { if (outcome) void onClose(outcome) }} disabled={busy || !outcome}>Close thread</button>
  </>
}
