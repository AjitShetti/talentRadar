'use client'

import { FormEvent, useEffect, useState } from 'react'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import { api } from '@/lib/api'
import { INDIAN_CITIES } from '@/lib/filters'

export default function SettingsPage() {
  const [name, setName] = useState('')
  const [headline, setHeadline] = useState('')
  const [roles, setRoles] = useState('')
  const [locations, setLocations] = useState('')
  const [years, setYears] = useState('')
  const [skills, setSkills] = useState('')
  const [remote, setRemote] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(''); const [error, setError] = useState('')

  useEffect(() => {
    api.profile.get().then(result => {
      const profile = result.profile || {}
      setName(String(profile.full_name || ''))
      setHeadline(String(profile.headline || ''))
      setRoles(Array.isArray(profile.target_roles) ? profile.target_roles.join(', ') : '')
      setLocations(Array.isArray(profile.target_locations) ? profile.target_locations.join(', ') : '')
      setYears(profile.years_experience == null ? '' : String(profile.years_experience))
      setSkills(Array.isArray(profile.skills) ? profile.skills.map((item: unknown) => typeof item === 'string' ? item : (item as { name?: string }).name || '').join(', ') : '')
      setRemote(Boolean(profile.is_remote_preferred))
    }).catch(err => setError(err instanceof Error ? err.message : 'Could not load profile.'))
  }, [])

  function list(value: string) { return value.split(',').map(v => v.trim()).filter(Boolean) }

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
      })
      setMessage('Profile saved. Your job search filters now start from these defaults.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save profile.')
    } finally {
      setSaving(false)
    }
  }

  return <RequireAuth><AppShell>
    <section className="page-heading">
      <p className="eyebrow">PROFILE &amp; SETTINGS</p>
      <h1>Set the direction of your search.</h1>
      <p>Your city, experience, and target roles pre-fill the job search filters and steer skill signals and AI coaching.</p>
    </section>
    <form className="card-form settings-form" onSubmit={save}>
      <label>Full name<input value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Professional headline<input value={headline} onChange={e => setHeadline(e.target.value)} placeholder="e.g. Product designer focused on fintech" /></label>
      <label>Target roles <small>Separate with commas</small><input value={roles} onChange={e => setRoles(e.target.value)} placeholder="Product Designer, UX Researcher" /></label>
      <label>Preferred locations <small>Separate with commas — the first one pre-fills your search</small>
        <input list="settings-cities" value={locations} onChange={e => setLocations(e.target.value)} placeholder="Bengaluru, Remote" />
        <datalist id="settings-cities">{INDIAN_CITIES.map(city => <option key={city} value={city} />)}</datalist>
      </label>
      <label>Years of experience <small>Sets the default experience filter in job search</small>
        <input type="number" min={0} max={60} step={0.5} value={years} onChange={e => setYears(e.target.value)} placeholder="e.g. 4" />
      </label>
      <label>Skills <small>Separate with commas</small><input value={skills} onChange={e => setSkills(e.target.value)} placeholder="Figma, User research, Systems thinking" /></label>
      <label className="toggle-row"><input type="checkbox" checked={remote} onChange={e => setRemote(e.target.checked)} /> Prefer remote opportunities</label>
      {error && <p className="form-error">{error}</p>}
      {message && <p className="success-message">{message}</p>}
      <button className="primary-button" disabled={saving}>{saving ? 'Saving…' : 'Save profile'}</button>
    </form>
  </AppShell></RequireAuth>
}
