'use client'

import { ChangeEvent, FormEvent, useState } from 'react'
import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import { useRouter } from 'next/navigation'
import { ArrowLeft, ArrowRight, BriefcaseBusiness, Check, Compass, Dumbbell, UploadCloud } from 'lucide-react'
import RequireAuth from '@/components/RequireAuth'
import Segmented from '@/components/Segmented'
import { api } from '@/lib/api'
import { INDIAN_CITIES } from '@/lib/filters'
import { usePersistentState } from '@/lib/persistent-state'

type Stage = 'student' | 'working' | 'between'
type Goal = 'apply' | 'prepare' | 'both'

const STAGES: { value: Stage; label: string }[] = [
  { value: 'student', label: 'Student' },
  { value: 'working', label: 'Working' },
  { value: 'between', label: 'Between jobs' },
]

/** Where each goal lands the user, and the sentence saved as their career goal. */
const GOALS: { value: Goal; title: string; detail: string; icon: typeof Compass; href: string; sentence: string }[] = [
  { value: 'apply', title: 'Land a job', detail: 'Openings, who to contact at each company, and a tracker for every application.', icon: BriefcaseBusiness, href: '/search', sentence: 'Land a job: find openings, reach the right people and track applications.' },
  { value: 'prepare', title: 'Get interview-ready', detail: 'Mock interviews, a daily coding question, weekly take-homes and a leaderboard.', icon: Dumbbell, href: '/practice', sentence: 'Get interview-ready: mock interviews, daily practice and take-homes.' },
  { value: 'both', title: 'Both', detail: 'Start from a day plan that mixes applying and practice.', icon: Compass, href: '/dashboard', sentence: 'Land a job and get interview-ready at the same time.' },
]

const STEPS = ['You', 'Your goal', 'Your target'] as const

export default function OnboardingPage() {
  const router = useRouter()
  const [step, setStep] = usePersistentState('onboarding.step', 0)
  const [name, setName] = usePersistentState('onboarding.name', '')
  const [stage, setStage] = usePersistentState<Stage>('onboarding.stage', 'working')
  const [title, setTitle] = usePersistentState('onboarding.title', '')
  const [years, setYears] = usePersistentState('onboarding.years', '')
  const [goal, setGoal] = usePersistentState<Goal | ''>('onboarding.goal', '')
  const [role, setRole] = usePersistentState('onboarding.role', '')
  const [location, setLocation] = usePersistentState('onboarding.location', '')
  const [skills, setSkills] = usePersistentState('onboarding.skills', '')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const [resumeName, setResumeName] = useState<string | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [resumeError, setResumeError] = useState('')

  // A student's experience is zero unless they say otherwise; the server needs a number to call the profile complete.
  const yearsValue = years.trim() === '' ? (stage === 'student' ? 0 : null) : Number(years)
  const canNext = [
    name.trim() !== '' && yearsValue !== null && !Number.isNaN(yearsValue) && yearsValue >= 0 && yearsValue <= 60,
    goal !== '',
    role.trim() !== '',
  ]
  // A greyed-out Continue does not say what is missing; this does, once they try.
  function missing(): string {
    if (step === 0) {
      if (name.trim() === '') return 'Tell us what to call you.'
      return 'Add your years of experience (0 to 60).'
    }
    if (step === 1) return 'Pick what brings you here.'
    return 'Add the role you are aiming for.'
  }

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
    if (!canNext[step]) { setError(missing()); return }
    if (step < STEPS.length - 1) { setError(''); setStep(step + 1); return }
    if (!canNext.every(Boolean)) return
    const chosen = GOALS.find(g => g.value === goal) ?? GOALS[2]
    setSaving(true)
    setError('')
    try {
      await api.profile.save({
        full_name: name.trim(),
        current_role: title.trim() || STAGES.find(s => s.value === stage)?.label,
        career_goals: chosen.sentence,
        target_roles: [role.trim()],
        target_locations: location.trim() ? [location.trim()] : [],
        years_experience: yearsValue,
        skills: skills.split(',').map(s => s.trim()).filter(Boolean),
      })
      setStep(0)
      router.push(chosen.href)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save your profile.')
    } finally {
      setSaving(false)
    }
  }

  const last = step === STEPS.length - 1

  return <RequireAuth>
    <main className="onb-page">
      <header className="onb-top">
        <span className="brand"><BrandLockup /></span>
        <ol className="onb-steps" aria-label="Progress">
          {STEPS.map((label, i) => <li key={label} data-state={i < step ? 'done' : i === step ? 'now' : undefined} aria-current={i === step ? 'step' : undefined}>
            <span>{i < step ? <Check size={11} strokeWidth={3} /> : i + 1}</span>{label}
          </li>)}
        </ol>
      </header>

      <form className="onb-card" onSubmit={submit}>
        <div className="onb-pane" key={step}>
          {step === 0 && <>
            <p className="onb-kicker">Welcome to TalentRadar</p>
            <h1>Let&apos;s get to know you.</h1>
            <p className="onb-lead">A few answers and we&apos;ll set up the right workspace for you.</p>
            <label className="onb-field">What should we call you?<input autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="Your name" autoComplete="name" /></label>
            <div className="onb-field">
              <span>Where are you right now?</span>
              <Segmented options={STAGES} value={stage} onChange={setStage} label="Where are you right now?" />
            </div>
            <div className="onb-row">
              <label className="onb-field">{stage === 'student' ? 'What are you studying?' : 'What do you do?'}<input value={title} onChange={e => setTitle(e.target.value)} placeholder={stage === 'student' ? 'e.g. B.Tech, Computer Science' : 'e.g. Backend engineer'} /></label>
              <label className="onb-field onb-years">Years of experience<input type="number" min={0} max={60} step={0.5} value={years} onChange={e => setYears(e.target.value)} placeholder={stage === 'student' ? '0' : 'e.g. 3'} /></label>
            </div>
          </>}

          {step === 1 && <>
            <p className="onb-kicker">Your goal</p>
            <h1>What brings you here?</h1>
            <p className="onb-lead">We&apos;ll take you straight to it. Everything else stays one tap away.</p>
            <div className="onb-goals" role="radiogroup" aria-label="What brings you here?">
              {GOALS.map(option => {
                const Icon = option.icon
                const on = goal === option.value
                return <button type="button" role="radio" aria-checked={on} key={option.value} className="onb-goal" data-on={on ? 'true' : undefined} onClick={() => setGoal(option.value)}>
                  <span className="onb-goal-icon"><Icon size={20} strokeWidth={1.8} /></span>
                  <span className="onb-goal-text"><strong>{option.title}</strong><small>{option.detail}</small></span>
                  <span className="onb-goal-check" aria-hidden="true"><Check size={13} strokeWidth={3} /></span>
                </button>
              })}
            </div>
          </>}

          {step === 2 && <>
            <p className="onb-kicker">Your target</p>
            <h1>{goal === 'prepare' ? 'What are you preparing for?' : 'What are you looking for?'}</h1>
            <p className="onb-lead">This sets your searches, your daily question and the gaps we point out.</p>
            <label className="onb-field">Target role<input autoFocus value={role} onChange={e => setRole(e.target.value)} placeholder="e.g. Frontend engineer" /></label>
            <label className="onb-field"><span>Preferred city <small>Optional</small></span>
              <input list="onb-cities" value={location} onChange={e => setLocation(e.target.value)} placeholder="Bengaluru, or Remote" />
              <datalist id="onb-cities">{INDIAN_CITIES.map(city => <option key={city} value={city} />)}</datalist>
            </label>
            <label className="onb-field"><span>Core skills <small>Separate with commas</small></span><input value={skills} onChange={e => setSkills(e.target.value)} placeholder="React, TypeScript, System design" /></label>
            <div className="onb-field">
              <span>Your resume <small>Optional · we read it against your target role</small></span>
              <div className="onb-upload">
                <label htmlFor="onboarding-resume" className="outline-button">
                  {resumeName ? <Check size={15} /> : <UploadCloud size={15} />}
                  {extracting ? 'Reading file…' : resumeName ? resumeName : 'Upload PDF or DOCX'}
                </label>
                <input id="onboarding-resume" type="file" accept=".pdf,.docx,.txt" className="file-input" onChange={handleResume} />
              </div>
              {resumeError && <p className="form-error" role="alert">{resumeError}</p>}
            </div>
          </>}
        </div>

        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="onb-actions">
          {step > 0
            ? <button type="button" className="outline-button" onClick={() => { setError(''); setStep(step - 1) }}><ArrowLeft size={15} /> Back</button>
            : <Link className="text-button onb-skip" href="/dashboard">Skip for now</Link>}
          <button type="submit" className="primary-button" disabled={saving} aria-disabled={!canNext[step] || undefined}>
            {last ? (saving ? 'Setting up…' : 'Take me there') : 'Continue'} <ArrowRight size={15} />
          </button>
        </div>
      </form>
    </main>
  </RequireAuth>
}
