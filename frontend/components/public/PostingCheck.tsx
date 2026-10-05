'use client'

import { FormEvent, useState } from 'react'
import Link from 'next/link'
import { ArrowRight } from 'lucide-react'
import LivenessStamp from '@/components/LivenessStamp'
import { API_URL, Liveness } from '@/lib/api'
import { track } from '@/lib/analytics'

type CheckResult = {
  status: 'indexed' | 'open_now' | 'closed_now' | 'unreachable' | 'unsupported'
  message: string
  liveness: Liveness | null
  role: { slug: string; title: string; company: string | null } | null
}

const HEADLINE: Record<CheckResult['status'], string> = {
  indexed: '',
  open_now: 'Open right now',
  closed_now: 'No longer listed',
  unreachable: 'Could not check just now',
  unsupported: 'We can’t verify this source',
}

/**
 * Paste a posting URL, find out whether the employer still lists it.
 *
 * The answer is only ever as strong as the evidence: a role we already track
 * gets its full verdict, one we have never seen gets "open right now" and an
 * explicit statement that we know nothing about its history, and a site we
 * cannot ask gets told so rather than given a made-up reading.
 */
export default function PostingCheck({ compact = false }: { compact?: boolean }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CheckResult | null>(null)
  const [error, setError] = useState('')

  async function check(event: FormEvent) {
    event.preventDefault()
    const value = url.trim()
    if (!value || busy) return
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await fetch(`${API_URL}/api/v1/public/check`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: value }),
      })
      if (response.status === 429) throw new Error('That is a lot of checks in a short time. Try again in a few minutes.')
      if (response.status === 422) throw new Error('That does not look like a link to a job posting.')
      if (!response.ok) throw new Error('The check could not be completed. Try again shortly.')
      const data = await response.json() as CheckResult
      setResult(data)
      track('check_submitted', { status: data.status })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The check could not be completed.')
    } finally { setBusy(false) }
  }

  return <div className={compact ? 'posting-check compact' : 'posting-check'}>
    <form onSubmit={check}>
      <label htmlFor="posting-url">Link to a job posting</label>
      <div className="posting-check-row">
        <input id="posting-url" type="url" inputMode="url" required maxLength={2048} value={url}
          onChange={event => setUrl(event.target.value)} placeholder="https://boards.greenhouse.io/company/jobs/…" />
        <button type="submit" disabled={busy}>{busy ? 'Checking…' : 'Is it still open?'}</button>
      </div>
      <p className="posting-check-hint">Works for roles hosted on Greenhouse, Lever and Ashby. The first check can take up to a minute while the server wakes.</p>
    </form>

    <div aria-live="polite">
      {error && <p className="posting-check-error">{error}</p>}
      {result && <div className={`posting-check-result status-${result.status}`}>
        {result.role && <p className="posting-check-role">{result.role.title}{result.role.company ? ` · ${result.role.company}` : ''}</p>}
        {result.liveness
          ? <LivenessStamp liveness={result.liveness} open />
          : <><strong>{HEADLINE[result.status]}</strong><p>{result.message}</p></>}
        {result.role
          ? <Link className="posting-check-next" href={`/r/${result.role.slug}`}>See the full evidence <ArrowRight size={13} /></Link>
          : result.status === 'open_now' && <Link className="posting-check-next" href="/signup">Track it and prepare for the interview <ArrowRight size={13} /></Link>}
      </div>}
    </div>
  </div>
}
