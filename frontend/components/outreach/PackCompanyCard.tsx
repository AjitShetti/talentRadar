'use client'

import { useState } from 'react'
import Link from 'next/link'
import type { PackCompany, PackItem } from '@/lib/api'
import { BODY_MAX, FORMATS, KIND_LABEL, SUBJECT_MAX, threadHref } from '@/lib/outreach'
import { addressBadge, mailtoHref } from '@/lib/outreach-pack'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

export type ItemHandlers = {
  /** Records that the user sent this text themselves. Nothing is sent from here. */
  onSent: (item: PackItem, message: string, subject: string | null) => Promise<void>
  onSkip: (item: PackItem) => void
  /** Asks again for a message whose writing failed. */
  onWrite: (item: PackItem) => void
}

// What made the text, said plainly: a template is never presented as a tailored message.
const templateNote = (item: PackItem) => item.reason === 'daily_cap'
  ? "A template: today's AI drafts are used up. Fill the [bracketed] parts yourself."
  : 'A template: the writing model was not available. Fill the [bracketed] parts yourself.'

const who = (item: PackItem) => {
  const role = item.person.title || (item.person.kind ? KIND_LABEL[item.person.kind] : '') || ''
  return { name: item.person.name || KIND_LABEL[item.person.kind ?? ''] || 'Hiring team', role }
}

type ItemProps = ItemHandlers & { item: PackItem; writing: boolean }

/** One recipient: where to reach them, the editable message, and what the user did. */
export function PackItemRow({ item, writing, onSent, onSkip, onWrite }: ItemProps) {
  const [text, setText] = useState(item.message ?? '')
  const [subject, setSubject] = useState(item.subject ?? '')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null)
  const { name, role } = who(item)
  const isEmail = item.channel === 'email' && item.address !== null
  const badge = item.address ? addressBadge(item.address) : null
  const linkedin = externalHref(item.linkedin_url)
  const max = FORMATS.find(f => f.key === item.format)?.maxChars ?? null
  const sent = item.status === 'sent'

  async function copy() {
    try {
      await navigator.clipboard.writeText(isEmail && subject ? `${subject}\n\n${text}` : text)
      setCopied('yes')
    } catch {
      setCopied('failed')
    }
  }

  async function markSent() {
    if (busy || !text.trim()) return
    setBusy(true)
    try { await onSent(item, text, isEmail ? subject.trim() || null : null) } finally { setBusy(false) }
  }

  return <article className="pack-item" data-status={item.status}>
    <div className="pack-who">
      <strong>{name}{role && <span> &middot; {role}</span>}</strong>
      {item.kind === 'follow_up' && <span className="or-chip">Follow-up</span>}
      {sent && <span className="pack-status">Sent</span>}
    </div>
    <div className="pack-reach">
      {isEmail && item.address
        ? <>
            <span className="pack-address">{item.address.value}</span>
            {badge && <span className="pack-badge" data-basis={item.address.basis}>{badge.label}</span>}
            {badge?.hint && <span className="pack-hint">{badge.hint}</span>}
          </>
        : <span className="pack-hint">No email found &middot; LinkedIn {item.format === 'linkedin_note' ? 'connection note' : 'message'}</span>}
    </div>

    {sent
      ? <p className="pack-sent-text">{item.message}{item.thread_id && <> <Link href={threadHref({ threadId: item.thread_id, personId: null, mode: null })}>Open thread &rarr;</Link></>}</p>
      : item.message === null
        ? writing
          ? <div className="pack-skeleton" aria-busy="true" aria-label="Writing the message"><i /><i /><i /></div>
          : <p className="pack-hint">Could not write this one. <button type="button" className="text-button" onClick={() => onWrite(item)}>Try again</button></p>
        : <>
            {isEmail && <label className="pack-field">
              <span>Subject</span>
              <input value={subject} onChange={e => setSubject(e.target.value)} maxLength={SUBJECT_MAX} />
            </label>}
            <label className="pack-field">
              <span>Message{max !== null && <em data-over={text.length > max ? '' : undefined}>{text.length}/{max}</em>}</span>
              <textarea value={text} onChange={e => setText(e.target.value)} rows={isEmail ? 7 : 4} maxLength={BODY_MAX} />
            </label>
            {!item.generated && <p className="pack-hint">{templateNote(item)}</p>}
            <div className="pack-actions">
              <button type="button" className="primary-button" disabled={busy || !text.trim()} onClick={markSent}>I sent it</button>
              {isEmail && item.address
                ? <a className="outline-button" href={mailtoHref(item.address.value, subject.trim() || null, text)}>Open in email</a>
                : linkedin && <a className="outline-button" href={linkedin} {...EXTERNAL_LINK_PROPS}>Open LinkedIn</a>}
              <button type="button" className="outline-button" onClick={copy}>{copied === 'yes' ? 'Copied' : 'Copy'}</button>
              <button type="button" className="text-button" disabled={busy} onClick={() => onSkip(item)}>Skip</button>
            </div>
            {copied === 'failed' && <p className="form-error">Could not copy. Select the text and copy it yourself.</p>}
          </>}
  </article>
}

type Props = ItemHandlers & {
  company: PackCompany
  writing: boolean
  onApplied: (company: PackCompany, jobId: string) => Promise<void>
}

/** One company in today's pack: why it was picked, its matching roles, and who to write to. */
export default function PackCompanyCard({ company, writing, onApplied, ...handlers }: Props) {
  const [applying, setApplying] = useState<string | null>(null)
  const items = company.items.filter(item => item.status !== 'skipped')

  async function applied(jobId: string) {
    if (applying) return
    setApplying(jobId)
    try { await onApplied(company, jobId) } finally { setApplying(null) }
  }

  return <section className="pack-company" aria-labelledby={`pack-${company.company_id ?? company.name}`}>
    <div className="pack-company-top">
      <h3 id={`pack-${company.company_id ?? company.name}`}>{company.name}</h3>
      {company.applied && <span className="pack-status">Applied</span>}
    </div>
    {company.summary && <p className="pack-summary">{company.summary}</p>}
    {company.reasons.length > 0 && <div className="or-reasons">{company.reasons.map(r => <span key={r}>{r}</span>)}</div>}
    {company.roles.length > 0 && <ul className="pack-roles">
      {company.roles.map(role => <li key={role.job_id}>
        <span>{role.title}</span>
        <Link className="outline-button" href={`/roles/${encodeURIComponent(role.job_id)}`}>Apply</Link>
        {!company.applied && company.company_id &&
          <button type="button" className="text-button" disabled={applying !== null} onClick={() => applied(role.job_id)}>I applied</button>}
      </li>)}
    </ul>}
    {items.map(item => <PackItemRow
      key={`${item.id}:${item.message === null ? 'pending' : 'ready'}`}
      item={item}
      writing={writing}
      {...handlers}
    />)}
  </section>
}
