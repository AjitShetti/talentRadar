'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ExternalLink, Loader2, Trash2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, Application } from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

// Must stay in sync with ApplicationStatus in storage/models.py — the PATCH
// endpoint rejects anything outside the enum, and a filter tab that names a
// status the API never returns can only ever show an empty tracker.
const statuses = ['saved', 'applied', 'online_assessment', 'screening', 'interview', 'offer', 'rejected', 'withdrawn']
const label = (status: string) => { const words = status.replace(/_/g, ' '); return words.charAt(0).toUpperCase() + words.slice(1) }
const added = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

// The lamp reads the same way as the briefing's: amber is live and needs you,
// green is won, red is closed against you, faint is parked.
const TONES: Record<string, string> = {
  applied: 'live', online_assessment: 'live', screening: 'live', interview: 'live',
  offer: 'won', rejected: 'lost', withdrawn: 'lost',
}
const toneFor = (status: string) => TONES[status] || 'idle'

export default function ApplicationsPage() {
  const [apps, setApps] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = usePersistentState('applications.filter', 'all')

  async function load() {
    setLoading(true)
    try {
      const response = await api.applications.list()
      setApps(response.applications)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load applications.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function changeStatus(id: string, status: string) {
    try {
      const updated = await api.applications.update(id, { status })
      setApps(current => current.map(app => app.id === id ? updated : app))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update application.')
    }
  }

  async function remove(id: string) {
    if (!confirm('Remove this application from your tracker?')) return
    try {
      await api.applications.remove(id)
      setApps(current => current.filter(app => app.id !== id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove application.')
    }
  }

  const shown = filter === 'all' ? apps : apps.filter(app => app.status === filter)
  const count = (status: string) => status === 'all' ? apps.length : apps.filter(app => app.status === status).length

  return <RequireAuth><AppShell>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Application tracker</span>
        <h1>Keep every opportunity moving<span>.</span></h1>
        <p>Roles saved from Find roles appear here automatically.</p>
      </div>
      <div className="tracker-total"><strong><FlapText value={apps.length} /></strong><span>in your tracker</span></div>
    </section>

    <div className="status-tabs" role="group" aria-label="Filter by status">
      {['all', ...statuses].map(status =>
        <button type="button" key={status} className={filter === status ? 'selected' : ''} aria-pressed={filter === status} onClick={() => setFilter(status)}>
          {label(status)}{!loading && count(status) > 0 && <span className="status-count">{count(status)}</span>}
        </button>)}
    </div>

    {error && <p className="form-error" role="alert">{error}</p>}

    {loading
      ? <div className="loading-state"><Loader2 className="spin"/> Loading your tracker…</div>
      : !shown.length
        ? <div className="empty-state">
            <h2>{filter === 'all' ? 'No applications yet.' : `Nothing at ${label(filter).toLowerCase()} right now.`}</h2>
            <p>Save roles from Find roles and move them through your search here.</p>
            <Link className="outline-button" href="/search">Find roles</Link>
          </div>
        : <div className="application-table">{shown.map(app =>
            <article className="application-row" key={app.id} data-tone={toneFor(app.status)}>
              <span className="app-lamp" aria-hidden />
              <div className="company-logo ink">{(app.job?.company_name || '?').slice(0, 1)}</div>
              <div className="application-role">
                <strong>{app.job_id && app.job ? <Link href={`/roles/${encodeURIComponent(app.job_id)}`}>{app.job.title}</Link> : app.job?.title || 'Job no longer available'}</strong>
                <span>{app.job?.company_name || 'Company not listed'} · {app.job?.location_raw || (app.job?.is_remote ? 'Remote' : '—')}</span>
              </div>
              <select value={app.status} onChange={e => changeStatus(app.id, e.target.value)} aria-label={`Status of ${app.job?.title || 'this application'}`}>
                {statuses.map(status => <option value={status} key={status}>{label(status)}</option>)}
              </select>
              <span className="application-date">Added {added(app.created_at)}</span>
              {externalHref(app.job?.source_url)
                ? <a href={externalHref(app.job?.source_url)!} {...EXTERNAL_LINK_PROPS} className="row-arrow" aria-label="Open the original posting" title="Open the original posting"><ExternalLink size={16}/></a>
                : <span />}
              <button type="button" className="row-arrow danger" onClick={() => remove(app.id)} aria-label={`Remove ${app.job?.title || 'this application'} from your tracker`} title="Remove from tracker"><Trash2 size={16}/></button>
            </article>)}</div>}
  </AppShell></RequireAuth>
}
