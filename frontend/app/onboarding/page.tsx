'use client'

import { ChangeEvent, FormEvent, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Check, UploadCloud } from 'lucide-react'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import { api } from '@/lib/api'

export default function OnboardingPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [location, setLocation] = useState('')
  const [years, setYears] = useState('')
  const [skills, setSkills] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const [resumeName, setResumeName] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [resumeError, setResumeError] = useState('')

  // The upload saves straight to the account, so it stands on its own rather
  // than riding on the form submit — and skipping it never blocks onboarding.
  async function handleResume(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setResumeError(''); setExtracting(true)
    try {
      const saved = await api.resumes.extractText(file)
      setResumeName(saved.filename)
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : 'Could not read that file.')
    } finally {
      setExtracting(false)
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api.profile.save({
        full_name: name,
        target_roles: role ? [role] : [],
        target_locations: location ? [location] : [],
        years_experience: years.trim() === '' ? null : Number(years),
        skills: skills.split(',').map(s => s.trim()).filter(Boolean),
      })
      router.push('/dashboard')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save your profile.')
    } finally {
      setSaving(false)
    }
  }

  return <RequireAuth><AppShell>
    <section className="form-page">
      <p className="eyebrow">WELCOME TO TALENTRADAR</p>
      <h1>Tell us where you&apos;re headed.</h1>
      <p className="lead">We&apos;ll use this to tailor searches, match you with roles, and guide your preparation.</p>
      <form className="card-form" onSubmit={submit}>
        <label>Full name<input value={name} onChange={e => setName(e.target.value)} placeholder="Your name" /></label>
        <label>Target role<input value={role} onChange={e => setRole(e.target.value)} placeholder="e.g. Product designer" /></label>
        <label>Preferred location<input value={location} onChange={e => setLocation(e.target.value)} placeholder="e.g. Bengaluru or Remote" /></label>
        <label>Years of experience<input type="number" min={0} max={60} step={0.5} value={years} onChange={e => setYears(e.target.value)} placeholder="e.g. 4" /></label>
        <label>Core skills <small>Separate with commas</small><input value={skills} onChange={e => setSkills(e.target.value)} placeholder="Figma, Product strategy, Research" /></label>

        <div className="onboarding-resume">
          <span className="field-label">Your resume <small>We read it against the target role above to find your skill gaps</small></span>
          {resumeName && <p className="resume-status"><Check size={13} /> Using <strong>{resumeName}</strong></p>}
          <div className="resume-upload">
            <label htmlFor="onboarding-resume" className="outline-button upload-trigger">
              <UploadCloud size={14} />
              {extracting ? 'Reading file…' : resumeName ? 'Replace resume' : 'Upload PDF or DOCX'}
            </label>
            <input id="onboarding-resume" type="file" accept=".pdf,.docx,.txt" hidden onChange={handleResume} />
            {!resumeName && !extracting && <small className="upload-hint">Optional — you can add it later in Profile &amp; Goals</small>}
          </div>
          {resumeError && <p className="form-error">{resumeError}</p>}
        </div>

        {error && <p className="form-error">{error}</p>}
        <button className="primary-button" disabled={saving}>{saving ? 'Saving…' : 'Create my workspace'}</button>
      </form>
    </section>
  </AppShell></RequireAuth>
}
