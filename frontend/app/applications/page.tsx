'use client'

import { useEffect, useState } from 'react'
import { ExternalLink, Loader2, Trash2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, Application } from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'

// Must stay in sync with ApplicationStatus in storage/models.py — the PATCH
// endpoint rejects anything outside the enum, and a filter tab that names a
// status the API never returns can only ever show an empty tracker.
const statuses = ['saved', 'applied', 'online_assessment', 'screening', 'interview', 'offer', 'rejected', 'withdrawn']
const label = (status: string) => status.replace(/_/g, ' ')

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

  return <RequireAuth><AppShell>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Application tracker</span>
        <h1>Keep every opportunity moving<span>.</span></h1>
        <p>Roles saved from Find roles appear here automatically.</p>
      </div>
      <div className="tracker-total"><strong><FlapText value={apps.length} /></strong><span>active records</span></div>
    </section>

    <div className="status-tabs">
      {['all', ...statuses].map(status =>
        <button key={status} className={filter === status ? 'selected' : ''} onClick={() => setFilter(status)}>{label(status)}</button>)}
    </div>

    {error && <p className="form-error">{error}</p>}

    {loading
      ? <div className="loading-state"><Loader2 className="spin"/> Loading your tracker…</div>
      : !shown.length
        ? <div className="empty-state">
            <h2>No {filter === 'all' ? '' : label(filter)} applications yet.</h2>
            <p>Save roles from the Find roles page and move them through your search here.</p>
          </div>
        : <div className="application-table">{shown.map(app =>
            <article className="application-row" key={app.id} data-tone={toneFor(app.status)}>
              <span className="app-lamp" aria-hidden />
              <div className="company-logo ink">{(app.job?.company_name || '?').slice(0, 1)}</div>
              <div className="application-role">
                <strong>{app.job?.title || 'Job no longer available'}</strong>
                <span>{app.job?.company_name || 'Company not listed'} · {app.job?.location_raw || (app.job?.is_remote ? 'Remote' : '—')}</span>
              </div>
              <select value={app.status} onChange={e => changeStatus(app.id, e.target.value)}>
                {statuses.map(status => <option value={status} key={status}>{label(status)}</option>)}
              </select>
              <span className="application-date">Saved {new Date(app.created_at).toLocaleDateString()}</span>
              {app.job?.source_url
                ? <a href={app.job.source_url} target="_blank" className="row-arrow"><ExternalLink size={16}/></a>
                : <span />}
              <button className="row-arrow danger" onClick={() => remove(app.id)}><Trash2 size={16}/></button>
            </article>)}</div>}
  </AppShell></RequireAuth>
}
