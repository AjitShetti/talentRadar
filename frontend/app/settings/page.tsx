'use client'

import { ChangeEvent, FormEvent, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import RoutineEditor from '@/components/RoutineEditor'
import Segmented from '@/components/Segmented'
import { api, currentEmail } from '@/lib/api'
import { publishRoutine } from '@/lib/day-line'
import { INDIAN_CITIES } from '@/lib/filters'
import { Goal, goalFromSentence, GOALS } from '@/lib/goals'
import { readPersisted, usePersistentState } from '@/lib/persistent-state'
import type { Routine } from '@/lib/routine'

function formatDate(iso: string | null) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return ''
  }
}

export default function SettingsPage() {
  // Fields are persisted, and marked dirty on the first edit, so edits made
  // before a visit to another page are still here — not overwritten by the
  // saved profile — until the user saves them.
  const [name, setName] = usePersistentState('settings.name', '')
  const [headline, setHeadline] = usePersistentState('settings.headline', '')
  const [roles, setRoles] = usePersistentState('settings.roles', '')
  const [locations, setLocations] = usePersistentState('settings.locations', '')
  const [years, setYears] = usePersistentState('settings.years', '')
  const [skills, setSkills] = usePersistentState('settings.skills', '')
  const [remote, setRemote] = usePersistentState('settings.remote', false)
  const [digest, setDigest] = usePersistentState('settings.digest', false)
  // Offered only when this server can send mail; without it the box would promise nothing.
  const [mailEnabled, setMailEnabled] = useState(false)
  // A goal sentence written some other way is left as it is until the user picks one here.
  const [goal, setGoal] = usePersistentState<Goal | ''>('settings.goal', '')
  const [dirty, setDirty] = usePersistentState('settings.dirty', false)
  const edit = <T,>(setter: (value: T) => void) => (value: T) => { setter(value); setDirty(true) }
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(''); const [error, setError] = useState('')

  const [resumeName, setResumeName] = useState<string | null>(null)
  const [resumeSavedAt, setResumeSavedAt] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [resumeError, setResumeError] = useState('')

  // The routine has its own form and its own save, apart from the profile's.
  const [routine, setRoutine] = useState<Routine | null>(null)
  const [routineError, setRoutineError] = useState('')
  const [routineSaved, setRoutineSaved] = useState(false)

  useEffect(() => {
    api.routine.get().then(setRoutine).catch(() => setRoutineError('Could not load your routine.'))

    // The saved resume is what the dashboard compares against the target roles
    // below, so it is loaded and replaced right here alongside them.
    api.resumes.me().then(saved => {
      if (saved) { setResumeName(saved.filename); setResumeSavedAt(saved.updated_at) }
    }).catch(() => {})

    api.profile.get().then(result => {
      setMailEnabled(Boolean(result.mail_enabled))
      if (readPersisted<boolean>('settings.dirty')) return
      const profile = result.profile || {}
      setName(String(profile.full_name || ''))
      setHeadline(String(profile.headline || ''))
      setRoles(Array.isArray(profile.target_roles) ? profile.target_roles.join(', ') : '')
      setLocations(Array.isArray(profile.target_locations) ? profile.target_locations.join(', ') : '')
      setYears(profile.years_experience == null ? '' : String(profile.years_experience))
      setSkills(Array.isArray(profile.skills) ? profile.skills.map((item: unknown) => typeof item === 'string' ? item : (item as { name?: string }).name || '').join(', ') : '')
      setRemote(Boolean(profile.is_remote_preferred))
      setDigest(Boolean(profile.digest_email))
      setGoal(goalFromSentence(profile.career_goals) ?? '')
    }).catch(err => setError(err instanceof Error ? err.message : 'Could not load profile.'))
  }, [])

  function routineSavedAs(saved: Routine) {
    publishRoutine(saved, currentEmail())
    setRoutineSaved(true)
  }

  function list(value: string) { return value.split(',').map(v => v.trim()).filter(Boolean) }

  async function handleResume(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setResumeError(''); setExtracting(true)
    try {
      const saved = await api.resumes.extractText(file)
      setResumeName(saved.filename); setResumeSavedAt(saved.updated_at)
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Could not read that file.')
    } finally {
      setExtracting(false)
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    setMessage(''); setError(''); setSaving(true)
    const parsedYears = years.trim() === '' ? null : Number(years)
    if (parsedYears != null && (Number.isNaN(parsedYears) || parsedYears < 0 || parsedYears > 60)) {
      setError('Years of experience must be a number between 0 and 60.'); setSaving(false); return
    }
    try {
      await api.profile.save({
        full_name: name,
        headline,
        target_roles: list(roles),
        target_locations: list(locations),
        years_experience: parsedYears,
        skills: list(skills),
        is_remote_preferred: remote,
        ...(mailEnabled ? { digest_email: digest } : {}),
        ...(goal ? { career_goals: GOALS.find(g => g.value === goal)?.sentence } : {}),
      })
      setDirty(false)
      setMessage('Profile saved. Your job search filters now start from these defaults.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save profile.')
    } finally {
      setSaving(false)
    }
  }

  return <RequireAuth><AppShell narrow>
    <header className="day-title">
      <div className="day-head">
        <h1 className="day-h1">Set the direction of your search.</h1>
      </div>
      <p className="ov2-lead">These pre-fill your search. Add a resume and the Overview shows what&apos;s missing.</p>
    </header>
    <form className="card-form settings-form" onSubmit={save}>
      <div className="field-group">
        <span>What brings you here</span>
        <Segmented label="What brings you here" options={GOALS.map(g => ({ value: g.value, label: g.title }))} value={goal || 'both'} onChange={edit(setGoal)} />
      </div>
      <label>Full name<input value={name} onChange={e => edit(setName)(e.target.value)} /></label>
      <label>Professional headline<input value={headline} onChange={e => edit(setHeadline)(e.target.value)} placeholder="e.g. Product designer focused on fintech" /></label>
      <label>Target roles <small>Separate with commas</small><input value={roles} onChange={e => edit(setRoles)(e.target.value)} placeholder="Product Designer, UX Researcher" /></label>
      <label>Preferred locations <small>Separate with commas — the first one pre-fills your search. Jobs cover all of India; Companies and Events are Bengaluru only for now.</small>
        <input list="settings-cities" value={locations} onChange={e => edit(setLocations)(e.target.value)} placeholder="Bengaluru, Remote" />
        <datalist id="settings-cities">{INDIAN_CITIES.map(city => <option key={city} value={city} />)}</datalist>
      </label>
      <label>Years of experience <small>Sets the default experience filter in job search</small>
        <input type="number" min={0} max={60} step={0.5} value={years} onChange={e => edit(setYears)(e.target.value)} placeholder="e.g. 4" />
      </label>
      <label>Skills <small>Separate with commas</small><input value={skills} onChange={e => edit(setSkills)(e.target.value)} placeholder="Figma, User research, Systems thinking" /></label>
      <label className="toggle-row"><input type="checkbox" checked={remote} onChange={e => edit(setRemote)(e.target.checked)} /> Prefer remote opportunities</label>
      {mailEnabled && <label className="toggle-row"><input type="checkbox" checked={digest} onChange={e => edit(setDigest)(e.target.checked)} /> Email me my day plan each morning <small>Around 07:00 IST, only on days there is something to do</small></label>}
      {dirty && !saving && <p className="muted-copy">You have unsaved changes.</p>}
      {error && <p className="form-error">{error}</p>}
      {message && <p className="success-message">{message}</p>}
      <button type="submit" className="primary-button" disabled={saving}>{saving ? 'Saving…' : 'Save profile'}</button>
    </form>

    <section className="card-form routine-card">
      <h2>Daily routine</h2>
      <p className="muted-copy">What each part of your day is for. It shows on the line under the menu.</p>
      {routineError && <p className="form-error" role="alert">{routineError}</p>}
      {routine && <div onInput={() => setRoutineSaved(false)} onClick={() => setRoutineSaved(false)}>
        <RoutineEditor initial={routine.blocks} onSaved={routineSavedAs} />
      </div>}
      {routineSaved && <p className="success-message" role="status">Saved.</p>}
    </section>

    <section className="card-form resume-goals-card">
      <p className="eyebrow">Your resume</p>
      <h2>Upload the resume you actually send out.</h2>
      <p className="muted-copy">We compare it against the target roles above and surface the two or three skills those roles ask for that your resume doesn&apos;t show yet — or, if it already covers them, how to make it read stronger.</p>
      {resumeName && <p className="resume-status">Using <strong>{resumeName}</strong>{resumeSavedAt ? ` · saved ${formatDate(resumeSavedAt)}` : ''}</p>}
      <div className="resume-upload">
        <label htmlFor="profile-resume" className="outline-button upload-trigger">
          {extracting ? 'Reading file…' : resumeName ? 'Replace resume' : 'Upload PDF or DOCX'}
        </label>
        <input id="profile-resume" type="file" accept=".pdf,.docx,.txt" className="file-input" onChange={handleResume} />
        {!resumeName && !extracting && <small className="upload-hint">PDF, DOCX or TXT · saved to your account</small>}
      </div>
      {resumeError && <p className="form-error">{resumeError}</p>}
    </section>

    <AccountSection />
  </AppShell></RequireAuth>
}

/** The account itself: a new password, and deleting the account with everything in it. */
function AccountSection() {
  const router = useRouter()
  const [current, setCurrent] = useState(''); const [next, setNext] = useState(''); const [repeat, setRepeat] = useState('')
  const [pwBusy, setPwBusy] = useState(false); const [pwMessage, setPwMessage] = useState(''); const [pwError, setPwError] = useState('')
  const [deleting, setDeleting] = useState(false); const [confirmPassword, setConfirmPassword] = useState('')
  const [delBusy, setDelBusy] = useState(false); const [delError, setDelError] = useState('')

  async function changePassword(e: FormEvent) {
    e.preventDefault()
    setPwMessage(''); setPwError('')
    if (next !== repeat) { setPwError('The new passwords do not match.'); return }
    setPwBusy(true)
    try {
      await api.auth.changePassword(current, next)
      setCurrent(''); setNext(''); setRepeat('')
      setPwMessage('Password changed.')
    } catch (err) {
      setPwError(err instanceof Error ? err.message : 'Could not change your password.')
    } finally {
      setPwBusy(false)
    }
  }

  async function deleteAccount(e: FormEvent) {
    e.preventDefault()
    if (!window.confirm('Delete your account and everything in it? This cannot be undone.')) return
    setDelError(''); setDelBusy(true)
    try {
      await api.auth.deleteAccount(confirmPassword)
      router.push('/')
    } catch (err) {
      setDelError(err instanceof Error ? err.message : 'Could not delete your account.')
      setDelBusy(false)
    }
  }

  return <section className="card-form resume-goals-card">
    <p className="eyebrow">Your account</p>
    <h2>Change your password.</h2>
    <form onSubmit={changePassword}>
      <label>Current password<input type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} /></label>
      <label>New password<input type="password" autoComplete="new-password" minLength={8} required value={next} onChange={e => setNext(e.target.value)} placeholder="At least 8 characters" /></label>
      <label>Repeat the new password<input type="password" autoComplete="new-password" required value={repeat} onChange={e => setRepeat(e.target.value)} /></label>
      {pwError && <p className="form-error" role="alert">{pwError}</p>}
      {pwMessage && <p className="success-message">{pwMessage}</p>}
      <button type="submit" className="outline-button" disabled={pwBusy}>{pwBusy ? 'Changing…' : 'Change password'}</button>
    </form>

    <h2 className="account-delete-title">Delete your account.</h2>
    <p className="muted-copy">Removes your profile, resume, tracker, interviews, outreach, saved events and practice. This cannot be undone.</p>
    {!deleting
      ? <button type="button" className="outline-button danger-outline" onClick={() => setDeleting(true)}>Delete account</button>
      : <form onSubmit={deleteAccount}>
          <label>Your password, to confirm<input type="password" autoComplete="current-password" required autoFocus value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} /></label>
          {delError && <p className="form-error" role="alert">{delError}</p>}
          <div className="account-delete-actions">
            <button type="button" className="text-button" onClick={() => { setDeleting(false); setConfirmPassword(''); setDelError('') }}>Cancel</button>
            <button type="submit" className="outline-button danger-outline" disabled={delBusy}>{delBusy ? 'Deleting…' : 'Delete everything'}</button>
          </div>
        </form>}
  </section>
}
