'use client'

import { useState } from 'react'
import { PenLine } from 'lucide-react'
import CopyButton from '@/components/CopyButton'
import { api } from '@/lib/api'
import { track } from '@/lib/analytics'

const RELATIONSHIPS: Array<{ key: string; label: string }> = [
  { key: 'former_colleague', label: 'We have worked together' },
  { key: 'alumni', label: 'Same college or past employer' },
  { key: 'acquaintance', label: 'We have met or spoken' },
  { key: 'stranger', label: 'We have not met' },
]

/**
 * Drafts the message that asks one person for a referral.
 *
 * Nothing here sends anything — the draft lands in an editable box and the
 * user copies it into whichever channel they actually use. When the model was
 * unavailable the box holds a template, and the note says so: a template
 * presented as a tailored draft would be the product pretending.
 */
export default function ReferralAsk({ jobId, recipientName, recipientTitle }: { jobId: string; recipientName?: string | null; recipientTitle?: string | null }) {
  const [open, setOpen] = useState(false)
  const [relationship, setRelationship] = useState('stranger')
  const [message, setMessage] = useState('')
  const [generated, setGenerated] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function draft() {
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await api.roles.referralAsk(jobId, { recipient_name: recipientName || undefined, recipient_title: recipientTitle || undefined, relationship })
      setMessage(result.message); setGenerated(result.generated)
      track('referral_drafted', { generated: result.generated, relationship })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The draft could not be written.')
    } finally { setBusy(false) }
  }

  if (!open) return <button type="button" className="text-button" onClick={() => setOpen(true)}><PenLine size={12} /> Draft a referral ask</button>

  return <div className="referral-ask">
    <label>How do you know {recipientName || 'them'}?
      <select value={relationship} onChange={event => setRelationship(event.target.value)}>
        {RELATIONSHIPS.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
      </select>
    </label>
    <button type="button" className="outline-button" onClick={draft} disabled={busy}>{busy ? 'Writing…' : message ? 'Write another' : 'Write the draft'}</button>
    {error && <p className="form-error">{error}</p>}
    {message && <>
      <label className="referral-ask-draft">Your draft — edit before you send it
        <textarea value={message} onChange={event => setMessage(event.target.value)} rows={9} />
      </label>
      <div className="dossier-actions">
        <CopyButton text={message} />
        <span className="referral-ask-note">{generated
          ? 'Written from your saved resume and this posting. Fill any [bracketed] gap — we do not guess how you know someone.'
          : 'The writing model is busy, so this is a template. Fill the [bracketed] parts yourself.'}</span>
      </div>
    </>}
  </div>
}
