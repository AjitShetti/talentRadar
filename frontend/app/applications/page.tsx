'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import AppShell from '@/components/AppShell'
import { useDay } from '@/components/DayLine'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, Application } from '@/lib/api'
import { forgetNote } from '@/lib/day-line'
import { usePersistentState } from '@/lib/persistent-state'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

// Must stay in sync with ApplicationStatus in storage/models.py — the PATCH
// endpoint rejects anything outside the enum, and a filter tab that names a
// status the API never returns can only ever show an empty tracker.
const statuses = ['saved', 'applied', 'online_assessment', 'screening', 'interview', 'offer', 'rejected', 'withdrawn']
const label = (status: string) => { const words = status.replace(/_/g, ' '); return words.charAt(0).toUpperCase() + words.slice(1) }
const added = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** The six steps a role moves through. Rejected and withdrawn are where it stopped, not steps. */
const STEPS = statuses.slice(0, 6)
const STEP_NAMES = ['Saved', 'Applied', 'Online assessment', 'Screening', 'Interview', 'Offer']

/** Where a role stands. A closed role keeps no record of how far it got, so it claims no step. */
function StageTicks({ status }: { status: string }) {
  const at = STEPS.indexOf(status)
  const said = at < 0 ? label(status) : `At ${STEP_NAMES[at]}, step ${at + 1} of ${STEPS.length}`
  return <div className="stage" role="img" aria-label={said}>
    {STEPS.map((step, i) => <i
      key={step}
      style={{ '--s': i } as React.CSSProperties}
      data-on={i < at ? '' : undefined}
      data-now={i === at ? '' : undefined}
    />)}
  </div>
}

export default function ApplicationsPage() {
  const [apps, setApps] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = usePersistentState('applications.filter', 'all')
  const { notes } = useDay()

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
      forgetNote(id)
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
    <header className="day-title">
      <div className="day-head">
        <h1 className="day-h1">Keep every opportunity moving.</h1>
        <span className="ov2-date"><FlapText value={apps.length} /> in your tracker</span>
      </div>
      <p className="ov2-lead">Roles saved from Find roles appear here automatically.</p>
    </header>

    <div className="status-tabs" role="group" aria-label="Filter by status">
      {['all', ...statuses].map(status =>
        <button type="button" key={status} className={filter === status ? 'selected' : ''} aria-pressed={filter === status} onClick={() => setFilter(status)}>
          {label(status)}{!loading && count(status) > 0 && <span className="status-count">{count(status)}</span>}
        </button>)}
    </div>

    {error && <p className="form-error" role="alert">{error}</p>}

    {loading
      ? <div className="loading-state">Loading your tracker…</div>
      : !shown.length
        ? <div className="empty-state">
            <h2>{filter === 'all' ? 'No applications yet.' : `Nothing at ${label(filter).toLowerCase()} right now.`}</h2>
            <p>Save roles from Find roles and move them through your search here.</p>
            <Link className="outline-button" href="/search">Find roles</Link>
          </div>
        : <div className="application-table">{shown.map((app, i) => {
            const posting = externalHref(app.job?.source_url)
            const note = STEPS.includes(app.status) ? notes.get(app.id) : undefined
            return <article className="application-row" key={app.id} style={{ '--i': i } as React.CSSProperties}>
              <div className="application-role">
                <strong>{app.job_id && app.job ? <Link href={`/roles/${encodeURIComponent(app.job_id)}`}>{app.job.title}</Link> : app.job?.title || 'Job no longer available'}</strong>
                <span>{app.job?.company_name || 'Company not listed'} · {app.job?.location_raw || (app.job?.is_remote ? 'Remote' : '—')}</span>
                {note && <em className="hot-note">{note}</em>}
              </div>
              <StageTicks status={app.status} />
              <select value={app.status} onChange={e => changeStatus(app.id, e.target.value)} aria-label={`Status of ${app.job?.title || 'this application'}`}>
                {statuses.map(status => <option value={status} key={status}>{label(status)}</option>)}
              </select>
              <span className="application-date">Added {added(app.created_at)}</span>
              <div className="application-acts">
                {posting && <a href={posting} {...EXTERNAL_LINK_PROPS} aria-label="Open the original posting">Open</a>}
                <button type="button" onClick={() => remove(app.id)} aria-label={`Remove ${app.job?.title || 'this application'} from your tracker`}>Remove</button>
              </div>
            </article>
          })}</div>}
  </AppShell></RequireAuth>
}
