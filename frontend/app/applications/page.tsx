'use client'

import Link from 'next/link'
import { FormEvent, useEffect, useRef, useState } from 'react'
import AppShell from '@/components/AppShell'
import { useDay } from '@/components/DayLine'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, Application } from '@/lib/api'
import { forgetNote } from '@/lib/day-line'
import { asksInterviewTime, inputFromInterviewAt, interviewAtFromInput } from '@/lib/interview-time'
import { usePersistentState } from '@/lib/persistent-state'
import { dayTime } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

// Must stay in sync with ApplicationStatus in storage/models.py — the PATCH
// endpoint rejects anything outside the enum, and a filter tab that names a
// status the API never returns can only ever show an empty tracker.
const statuses = ['saved', 'applied', 'online_assessment', 'screening', 'interview', 'offer', 'rejected', 'withdrawn']
const label = (status: string) => { const words = status.replace(/_/g, ' '); return words.charAt(0).toUpperCase() + words.slice(1) }
const added = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** A row's role and company: the posting's own, else what the user typed for a role found elsewhere. */
const roleOf = (app: Application) => app.job?.title || app.role_title || 'Job no longer available'
const companyOf = (app: Application) => app.job?.company_name || app.company_name || 'Company not listed'

/** Track a role found somewhere TalentRadar never indexed. Nothing is fetched from its link. */
function AddRole({ onAdded, onCancel }: { onAdded: (app: Application) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(''); const [company, setCompany] = useState(''); const [link, setLink] = useState(''); const [status, setStatus] = useState('applied')
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      onAdded(await api.applications.createTyped({ role_title: title, company_name: company, posting_url: link.trim() || undefined, status }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add that role.')
    } finally {
      setBusy(false)
    }
  }
  return <form className="application-add" onSubmit={submit} aria-label="Add a role you found elsewhere">
    <label>Role<input autoFocus required maxLength={300} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Backend engineer" /></label>
    <label>Company<input required maxLength={200} value={company} onChange={e => setCompany(e.target.value)} placeholder="e.g. Lumen Works" /></label>
    <label><span>Posting link <small>Optional</small></span><input type="url" maxLength={512} value={link} onChange={e => setLink(e.target.value)} placeholder="https://" /></label>
    <label>Status<select value={status} onChange={e => setStatus(e.target.value)}>{statuses.map(option => <option value={option} key={option}>{label(option)}</option>)}</select></label>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="application-add-acts">
      <button type="button" className="text-button" onClick={onCancel}>Cancel</button>
      <button type="submit" className="primary-button" disabled={busy}>{busy ? 'Adding…' : 'Add to tracker'}</button>
    </div>
  </form>
}

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

/**
 * The interview's time, as the user enters it. Nothing else knows it: moving a role to
 * interview says nothing about when the interview is, so the Overview only asks until this is set.
 */
function InterviewTime({ app, onSave }: { app: Application; onSave: (interviewAt: string | null) => Promise<boolean> }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const shown = useRef<HTMLDivElement>(null)
  const role = app.job?.title || app.role_title || 'this application'

  // The control that had focus leaves the page with its form; keep the keyboard on the Edit button.
  const stopEditing = () => {
    setEditing(false)
    setTimeout(() => shown.current?.querySelector('button')?.focus(), 0)
  }
  const save = async (next: string | null) => {
    setBusy(true)
    const saved = await onSave(next)
    setBusy(false)
    if (saved) stopEditing()
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (value && !busy) void save(interviewAtFromInput(value))
  }
  const edit = () => { setValue(inputFromInterviewAt(app.interview_at)); setEditing(true) }

  if (editing) return <form className="application-when" onSubmit={submit} aria-label={`Interview time for ${role}`}>
    <label>Interview (IST)<input type="datetime-local" autoFocus value={value} onChange={e => setValue(e.target.value)} required /></label>
    <button type="submit" className="outline-button" disabled={busy || !value}>{busy ? 'Saving…' : 'Save time'}</button>
    {app.interview_at && <button type="button" className="text-button" disabled={busy} onClick={() => void save(null)}>Remove</button>}
    <button type="button" className="text-button" disabled={busy} onClick={stopEditing}>Cancel</button>
  </form>

  return <div className="application-when" ref={shown}>
    <span>{app.interview_at ? `Interview ${dayTime(app.interview_at)}` : 'No interview time yet'}</span>
    <button type="button" className="text-button" onClick={edit} aria-label={`${app.interview_at ? 'Edit the interview time' : 'Add the interview time'} for ${role}`}>{app.interview_at ? 'Edit' : 'Add the time'}</button>
  </div>
}

export default function ApplicationsPage() {
  const [apps, setApps] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = usePersistentState('applications.filter', 'all')
  const { notes } = useDay()
  const [adding, setAdding] = useState(false)

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

  async function saveInterviewTime(id: string, interviewAt: string | null) {
    try {
      const updated = await api.applications.update(id, { interview_at: interviewAt })
      setApps(current => current.map(app => app.id === id ? updated : app))
      forgetNote(id)
      setError('')
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the interview time.')
      return false
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
      <p className="ov2-lead">Roles saved from Find roles appear here automatically. Found one somewhere else? <button type="button" className="text-button" onClick={() => setAdding(true)} aria-expanded={adding}>Add a role</button></p>
    </header>

    {adding && <AddRole onCancel={() => setAdding(false)} onAdded={app => { setApps(current => [app, ...current]); setAdding(false); setFilter('all') }} />}

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
            <p>Save roles from Find roles, or add one you found elsewhere, and move them through your search here.</p>
            <Link className="outline-button" href="/search">Find roles</Link>
          </div>
        : <div className="application-table">{shown.map((app, i) => {
            const posting = externalHref(app.job?.source_url || app.posting_url)
            const note = STEPS.includes(app.status) ? notes.get(app.id) : undefined
            return <article className="application-row" key={app.id} style={{ '--i': i } as React.CSSProperties}>
              <div className="application-role">
                <strong>{app.job_id && app.job ? <Link href={`/roles/${encodeURIComponent(app.job_id)}`}>{app.job.title}</Link> : roleOf(app)}</strong>
                <span>{companyOf(app)} · {app.job ? app.job.location_raw || (app.job.is_remote ? 'Remote' : '—') : app.role_title ? 'Added by you' : '—'}</span>
                {note && <em className="hot-note">{note}</em>}
              </div>
              <StageTicks status={app.status} />
              <select value={app.status} onChange={e => changeStatus(app.id, e.target.value)} aria-label={`Status of ${roleOf(app)}`}>
                {statuses.map(status => <option value={status} key={status}>{label(status)}</option>)}
              </select>
              <span className="application-date">Added {added(app.created_at)}</span>
              <div className="application-acts">
                {posting && <a href={posting} {...EXTERNAL_LINK_PROPS} aria-label="Open the original posting">Open <span aria-hidden="true">↗</span></a>}
                <button type="button" onClick={() => remove(app.id)} aria-label={`Remove ${roleOf(app)} from your tracker`}>Remove</button>
              </div>
              {asksInterviewTime(app.status, app.interview_at) && <InterviewTime app={app} onSave={next => saveInterviewTime(app.id, next)} />}
            </article>
          })}</div>}
  </AppShell></RequireAuth>
}
