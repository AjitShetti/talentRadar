'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import CopyButton from '@/components/CopyButton'
import { RELATIONSHIPS } from '@/components/ReferralAsk'
import { track } from '@/lib/analytics'
import { api, DraftFormat, IssueSnapshot, OutreachDraft, OutreachPerson, ThreadDetail } from '@/lib/api'
import {
  BODY_MAX, channelActionLabel, channelHref, DAILY_DRAFTS, defaultFormat, formatSpec, formatsForChannels, FormatSpec, mailtoHref,
} from '@/lib/outreach'

/** One good-first-issue from a company's open-source snapshot. */
export type SnapshotIssue = IssueSnapshot['issues'][number]

type Props = {
  thread: ThreadDetail
  /** Who the message is for; null for a comment on an issue. */
  person: OutreachPerson | null
  /** The issue a GitHub comment is about; null for a message to a person. */
  issue: SnapshotIssue | null
  /** The thread as the server holds it after the touch was recorded. */
  onSent: (detail: ThreadDetail) => void
  onCancel?: () => void
  /** True while an edited draft that has not been sent exists, so the drawer can ask before it closes. */
  onDirtyChange?: (dirty: boolean) => void
}

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)

// What made the draft, said plainly: a template is never presented as a tailored message.
function noteFor(draft: OutreachDraft): string {
  if (draft.generated) return "Written from your resume and this company's profile. Fill any [bracketed] gap."
  if (draft.reason === 'daily_cap') return `You've used today's ${DAILY_DRAFTS} drafts; here's a template.`
  return 'The writing model is busy, so this is a template. Fill the [bracketed] parts yourself.'
}

// Where the channel action goes, or null when we hold nowhere to send the user.
function targetFor(spec: FormatSpec, person: OutreachPerson | null, issue: SnapshotIssue | null): string | null {
  if (spec.key === 'github_comment') return issue ? channelHref(issue.url) : null
  const channel = person?.channels.find(c => c.channel === spec.channel)
  return channel ? channelHref(channel.url) : null
}

/**
 * Writes one message for the user to send themselves. Nothing here sends: the
 * channel action copies the text and opens the page (or the user's mail client),
 * and "I sent it" only records that the user did, after they click it.
 */
export default function DraftPanel({ thread, person, issue, onSent, onCancel, onDirtyChange }: Props) {
  const kind = person?.draft ?? 'first'
  // A comment on an issue needs the issue, so it is offered only when one is given.
  const specs = useMemo(
    () => person ? formatsForChannels(person.channels.map(c => c.channel)).filter(f => f.key !== 'github_comment') : [formatSpec('github_comment')],
    [person],
  )
  const [format, setFormat] = useState<DraftFormat | null>(() => defaultFormat(specs, kind))
  const [relationship, setRelationship] = useState('stranger')
  const [result, setResult] = useState<OutreachDraft | null>(null)
  const [text, setText] = useState('')
  const [subject, setSubject] = useState('')
  const [used, setUsed] = useState(false)
  const [keep, setKeep] = useState(false)
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const rootRef = useRef<HTMLElement>(null)

  // Focus moves in when the panel opens, to the first control that can take it.
  useEffect(() => { rootRef.current?.querySelector<HTMLElement>('select:not([disabled]),button')?.focus() }, [])

  const dirty = result !== null && text.trim() !== ''
  const dirtyRef = useRef(onDirtyChange)
  useEffect(() => { dirtyRef.current = onDirtyChange })
  useEffect(() => { dirtyRef.current?.(dirty) }, [dirty])
  useEffect(() => () => { dirtyRef.current?.(false) }, [])

  const who = person ? person.name : issue ? `${issue.repo}#${issue.number}` : 'this issue'

  if (!specs.length) {
    return <section className="or-draft" ref={rootRef} aria-label={`Draft for ${who}`}>
      <p className="or-draft-note">We hold no way to reach {who} that a message can be written for.</p>
      {onCancel && <div className="or-draft-row"><button type="button" className="text-button" onClick={onCancel}>Close</button></div>}
    </section>
  }

  // The result's own format decides the channel action: the select may have moved since the draft was written.
  const drafted = result ? formatSpec(result.format) : null
  const target = drafted ? targetFor(drafted, person, issue) : null
  const isEmail = drafted?.channel === 'email'
  const over = result ? Math.max(0, text.length - result.max_chars) : 0

  async function write() {
    if (busy || !format) return
    setBusy(true); setError('')
    try {
      const next = await api.outreach.draft(thread.id, {
        person_id: person?.id,
        format,
        relationship: person ? relationship : undefined,
        issue_url: format === 'github_comment' ? issue?.url : undefined,
      })
      setResult(next); setText(next.message); setSubject(next.subject ?? ''); setUsed(false)
      track('outreach_drafted', { format: next.format, generated: next.generated })
    } catch (err) {
      setError(messageOf(err, 'The draft could not be written.'))
    } finally { setBusy(false) }
  }

  // The copy and the new tab both start inside the click, so the browser lets the tab open.
  async function openChannel() {
    if (!target) return
    setError('')
    if (isEmail) {
      window.location.href = mailtoHref(target.replace(/^mailto:/i, ''), subject.trim() || null, text)
      setUsed(true)
      return
    }
    try {
      const copied = navigator.clipboard.writeText(text)
      window.open(target, '_blank', 'noopener,noreferrer')
      setUsed(true)
      await copied
    } catch {
      setError('Could not copy to the clipboard. Select the text and copy it yourself.')
    }
  }

  async function sent() {
    if (!drafted || sending) return
    setSending(true); setError('')
    try {
      const body = keep ? text.trim() || undefined : undefined
      const next = await api.outreach.touch(thread.id, { person_id: person?.id ?? null, channel: drafted.channel, direction: 'out', body })
      track('outreach_sent', { channel: drafted.channel })
      onSent(next)
    } catch (err) {
      setError(messageOf(err, 'Could not record that.'))
    } finally { setSending(false) }
  }

  return <section className="or-draft" ref={rootRef} aria-label={`Draft for ${who}`}>
    <div className="or-draft-pair">
      <label>Format
        <select value={format ?? ''} onChange={e => setFormat(e.target.value as DraftFormat)} disabled={specs.length < 2}>
          {specs.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </label>
      {person && <label>How do you know {person.name}?
        <select value={relationship} onChange={e => setRelationship(e.target.value)}>
          {RELATIONSHIPS.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
        </select>
      </label>}
    </div>

    <div className="or-draft-row">
      <button type="button" className="outline-button" onClick={write} disabled={busy || !format}>{busy ? 'Writing…' : result ? 'Write another' : 'Write the draft'}</button>
      {onCancel && <button type="button" className="text-button" onClick={onCancel}>Cancel</button>}
    </div>

    {error && <p className="form-error" role="alert">{error}</p>}

    {result && drafted && <>
      {isEmail && <label>Subject
        <input type="text" value={subject} maxLength={120} onChange={e => setSubject(e.target.value)} />
      </label>}
      <label>Your draft — edit before you use it
        <textarea value={text} maxLength={BODY_MAX} rows={9} onChange={e => setText(e.target.value)} />
      </label>
      <div className="or-draft-row">
        <span className="or-count" data-over={over > 0 ? '' : undefined}>{text.length} / {result.max_chars}{over > 0 ? ` · ${over} over` : ''}</span>
      </div>
      <p className="or-draft-note" role="status">
        {noteFor(result)}{' '}
        <span className="or-count">{result.remaining_today} {result.remaining_today === 1 ? 'draft' : 'drafts'} left today</span>
      </p>

      <div className="or-draft-row">
        {target && <button type="button" className="outline-button" onClick={openChannel}>{channelActionLabel(drafted.key)}</button>}
        {(!target || isEmail) && <CopyButton text={text} />}
        <button type="button" className={used ? 'primary-button' : 'outline-button'} onClick={sent} disabled={sending}>{sending ? 'Saving…' : 'I sent it'}</button>
      </div>
      <label className="or-keep">
        <input type="checkbox" checked={keep} onChange={e => setKeep(e.target.checked)} />
        Keep a copy in this thread
      </label>
    </>}
  </section>
}
