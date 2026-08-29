'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, BriefcaseBusiness, ExternalLink, FileText, Lightbulb, MapPin, MessageSquare, Search, Sparkles, Target, UploadCloud } from 'lucide-react'
import AppShell from '@/components/AppShell'
import BriefingFeed from '@/components/BriefingFeed'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import RequireAuth from '@/components/RequireAuth'
import { api, Briefing, JobMatch, SkillsFocus } from '@/lib/api'

type Dimension = { key: string; label: string; score: number; weakest: boolean }
type TrackStat = { track: string; label: string; score: number; sessions: number }
type WeakMoment = { question: string; score: number; track_label: string; difficulty: string; dimension: string; dimension_score: number; was_followup: boolean }
type TrendPoint = { label: string; score: number; date: string | null; completed: boolean }
type InterviewInsights = { sessions_analyzed: number; questions_analyzed: number; average_score: number; delta: number; abandoned: number; trend: TrendPoint[]; dimensions: Dimension[]; weakest_dimension: { key: string; label: string; score: number; hint: string } | null; tracks: TrackStat[]; weakest_track: TrackStat | null; weak_moments: WeakMoment[]; focus: string[] }

const pct = (value: number) => `${Math.min(100, Math.max(3, value))}%`

/**
 * The momentum line the briefing used to carry as its own card. It lives here
 * now, on top of the panel that already holds the full score breakdown.
 */
function momentumLine(interviews: InterviewInsights) {
  const latest = interviews.trend.length > 0 ? interviews.trend[interviews.trend.length - 1].score : interviews.average_score
  const average = Math.round(interviews.average_score)
  const trend = interviews.delta < -2
    ? `${Math.abs(interviews.delta)} points below your average of ${average}`
    : interviews.delta > 2
      ? `${interviews.delta} points above your average of ${average}`
      : `holding steady around ${average}`
  const weakest = interviews.weakest_track?.label
  return `Last session scored ${Math.round(latest)}/100 — ${trend}.${weakest ? ` Your weakest track is ${weakest}.` : ''}`
}

export default function Dashboard() {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [briefingFailed, setBriefingFailed] = useState(false)
  const [error, setError] = useState('')

  const loadBriefing = useCallback(async () => {
    try {
      setBriefing(await api.agent.briefing())
      setBriefingFailed(false)
    } catch {
      // The briefing is one half of the page — never blank the other half for it.
      setBriefingFailed(true)
    }
  }, [])

  useEffect(() => {
    // Two calls on purpose: the briefing is pure SQL and paints immediately,
    // while /dashboard/overview waits on an LLM-backed skill focus.
    api.dashboard().then(setData).catch(err => setError(err instanceof Error ? err.message : 'Dashboard data is unavailable.'))
    loadBriefing()
  }, [loadBriefing])

  async function dismiss(cardId: string, snoozeDays?: number) {
    // Optimistic: the card goes now, the server catches up.
    setBriefing(current => current
      ? { ...current, cards: current.cards.filter(c => c.id !== cardId), hidden_count: current.hidden_count + 1 }
      : current)
    try {
      await api.agent.dismissCard(cardId, snoozeDays)
    } catch {
      loadBriefing()
    }
  }

  const profile = data?.profile as { full_name?: string; onboarding_completed?: boolean; target_roles?: string[] | null } | undefined
  const analytics = data?.analytics as Record<string, number> | undefined
  const skillsFocus = data?.skills_focus as SkillsFocus | null | undefined
  const focusIsResume = skillsFocus?.kind === 'resume_improvements'
  const interviews = data?.interviews as InterviewInsights | null | undefined
  const hasInterviews = Boolean(interviews && interviews.sessions_analyzed > 0)
  const stats = briefing?.stats
  const jobMatches = data?.job_matches as JobMatch[] | null | undefined
  const targetRoles = profile?.target_roles || []

  return <RequireAuth><AppShell>
    <section className="welcome-row">
      <div>
        <p className="eyebrow">YOUR CAREER COMMAND CENTER</p>
        <h1>Good to see you{profile?.full_name ? `, ${profile.full_name.split(' ')[0]}` : ''} <span>✦</span></h1>
        <p className="subhead">Live data from your profile, application tracker, and career agent.</p>
      </div>
      <a className="primary-button" href="#copilot"><Sparkles size={16}/> Ask your copilot</a>
    </section>
    {error && <p className="form-error">{error}</p>}
    {!data && !error
      ? <div className="loading-state">Loading your career workspace…</div>
      : <section className="metric-grid">
        <Link href="/settings" className="metric-card accent">
          <div className="metric-top"><span>PROFILE STATUS</span><Target size={17}/></div>
          <div className="metric-body">
            <div className="progress-ring" style={{ '--progress': profile?.onboarding_completed ? '360deg' : '180deg' } as React.CSSProperties}><span>{profile?.onboarding_completed ? 100 : 50}<small>%</small></span></div>
            <div><strong>{profile?.onboarding_completed ? 'Profile ready' : 'Complete your profile'}</strong><p>Sharper recommendations start here</p></div>
          </div>
          <div className="metric-link">Edit profile <ArrowRight size={14}/></div>
        </Link>
        <Link href="/applications" className="metric-card">
          <div className="metric-top"><span>APPLICATIONS</span><BriefcaseBusiness size={17}/></div>
          <div className="big-number">{analytics?.total_applications ?? 0}</div>
          <p>roles currently in your tracker</p>
          {stats && <div className="metric-breakdown">
            <span><b>{stats.saved ?? 0}</b> saved</span>
            <span><b>{stats.applied ?? 0}</b> applied</span>
            <span><b>{stats.interviews ?? 0}</b> interviewing</span>
          </div>}
          <div className="metric-link purple-link">Open tracker <ArrowRight size={14}/></div>
        </Link>
        <Link href="/interview" className="metric-card">
          <div className="metric-top"><span>INTERVIEW LAB</span><MessageSquare size={17}/></div>
          {hasInterviews && interviews ? <>
            <div className="big-number">
              {Math.round(interviews.average_score)}<span className="out-of">/100</span>
              {interviews.delta !== 0 && <em className={`metric-delta ${interviews.delta > 0 ? 'up' : 'down'}`}>{interviews.delta > 0 ? '+' : ''}{interviews.delta}</em>}
            </div>
            <p>avg across {interviews.questions_analyzed} answers · weakest: {interviews.weakest_dimension?.label ?? '—'}</p>
            {interviews.trend.length > 1 && <div className="trend-bars">{interviews.trend.slice(-8).map((point, i) => <i key={i} style={{ height: pct(point.score) }} title={`${point.label} · ${point.score}%`}/>)}</div>}
          </> : <>
            <div className="big-number">Practice</div>
            <p>Run a focused mock interview with your LangGraph agent.</p>
          </>}
          <div className="metric-link purple-link">{hasInterviews ? 'Practice again' : 'Start a session'} <ArrowRight size={14}/></div>
        </Link>
      </section>}

    {/* The briefing and the copilot carry their own loading and failure states,
        and the copilot has no other home now — so neither waits on the overview
        payload, and neither disappears if it fails. */}
    <BriefingFeed briefing={briefing} failed={briefingFailed} onDismiss={dismiss}/>

    <div className="copilot-section" id="copilot">
      <CopilotWorkspace/>
    </div>

    {data && <>
      {targetRoles.length > 0 && <section className="panel">
        <div className="panel-heading">
          <div><p className="eyebrow">TODAY'S MATCHES</p><h2>Open roles for {targetRoles.slice(0, 2).join(' / ')}</h2></div>
          <Link className="text-button" href="/search">Search more <ArrowRight size={14}/></Link>
        </div>
        {jobMatches && jobMatches.length > 0
          ? <div className="job-results">{jobMatches.map(job => <article className="job-card" key={job.id}>
              <div className="job-card-top">
                <div className="company-logo violet">{(job.company || '?').slice(0, 1)}</div>
                <div><h2>{job.title}</h2><p>{job.company || 'Company not listed'}</p></div>
              </div>
              <div className="job-meta">
                <span><MapPin size={14}/>{job.location || (job.is_remote ? 'Remote' : 'Location not listed')}</span>
                {job.is_remote && <span>Remote-friendly</span>}
                {job.salary_raw && <span>{job.salary_raw}</span>}
              </div>
              {job.skills.length > 0 && <div className="chips">{job.skills.slice(0, 5).map(skill => <span key={skill}>{skill}</span>)}</div>}
              {job.source_url && <div className="job-actions"><a className="text-button" href={job.source_url} target="_blank">View original <ExternalLink size={14}/></a></div>}
            </article>)}</div>
          : <p className="muted-copy">No new openings for {targetRoles.slice(0, 2).join(' / ')} today — check back tomorrow.</p>}
      </section>}

      <section className="panel insight-panel">
        <div className="panel-heading">
          <div><p className="eyebrow">INTERVIEW FEEDBACK</p><h2>{hasInterviews ? 'What to work on next' : 'Feedback from your mock interviews'}</h2></div>
          <Link className="text-button" href="/interview">Interview lab <ArrowRight size={14}/></Link>
        </div>
        {hasInterviews && interviews ? <>
          <p className="momentum-line">{momentumLine(interviews)}</p>
          {interviews.focus.length > 0 && <div className="insight-focus"><Lightbulb size={16}/><div>{interviews.focus.map((line, i) => <p key={i}>{line}</p>)}</div></div>}
          <div className="insight-split">
            <div className="insight-col">
              <h3>Score breakdown · last {interviews.sessions_analyzed} session{interviews.sessions_analyzed === 1 ? '' : 's'}</h3>
              {interviews.dimensions.map(dimension => <div key={dimension.key} className={`meter${dimension.weakest ? ' weak' : ''}`}>
                <div className="meter-top"><strong>{dimension.label}{dimension.weakest && <span className="meter-tag">WEAKEST</span>}</strong><b>{Math.round(dimension.score)}</b></div>
                <div className="meter-track"><i style={{ width: pct(dimension.score) }}/></div>
              </div>)}
              {interviews.tracks.length > 1 && <div className="round-chips">{interviews.tracks.map(track => <span key={track.track} className={track.track === interviews.weakest_track?.track ? 'weak' : ''}>{track.label} <b>{Math.round(track.score)}</b></span>)}</div>}
            </div>
            <div className="insight-col">
              <h3>Where it went wrong</h3>
              {interviews.weak_moments.length > 0 ? interviews.weak_moments.map((moment, i) => <div className="weak-moment" key={i}>
                <b>{Math.round(moment.score)}</b>
                <div>
                  <p>{moment.question}</p>
                  <small>{moment.track_label} · {moment.difficulty} · {moment.dimension} {moment.dimension_score}/10{moment.was_followup ? ' · follow-up' : ''}</small>
                </div>
              </div>) : <p className="muted-copy">No single answer dropped below 70% recently. Step the difficulty up to keep finding gaps.</p>}
            </div>
          </div>
        </> : <p className="muted-copy">Run a mock interview and this panel becomes your feedback report — score breakdown by correctness, clarity and depth, your weakest round type, and the exact questions that cost you the most points.</p>}
      </section>

      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">JUMP BACK IN</p><h2>Move something forward</h2></div>
            <Sparkles size={17}/>
          </div>
          <div className="quick-actions">
            <Link href="/search"><Search size={18}/><span>Find roles</span></Link>
            <Link href="/resume-studio"><FileText size={18}/><span>Improve resume</span></Link>
            <Link href="/interview"><MessageSquare size={18}/><span>Practice interview</span></Link>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">CAREER SIGNALS</p><h2>{focusIsResume ? 'Sharpen your resume' : 'Skills to strengthen'}</h2></div>
            <Link className="text-button" href="/settings">Profile <ArrowRight size={14}/></Link>
          </div>
          {skillsFocus ? <>
            <p className="focus-headline">{skillsFocus.headline}</p>
            {skillsFocus.items.length > 0
              ? <div className="signal-list">{skillsFocus.items.map((item, i) => <div key={i}><span>{item.title}</span><small>{item.detail}</small></div>)}</div>
              : <Link className="outline-button focus-cta" href="/settings"><UploadCloud size={14}/> {skillsFocus.status === 'no_resume' ? 'Upload your resume' : 'Set your target role'}</Link>}
            {skillsFocus.items.length > 0 && skillsFocus.resume_filename && <p className="focus-source">Compared <strong>{skillsFocus.resume_filename}</strong> against {skillsFocus.target_roles.join(', ')} postings.</p>}
          </> : <p className="muted-copy">Add your target role and upload your resume in Profile &amp; Goals to see the skills these roles expect.</p>}
        </section>
      </div>
    </>}
  </AppShell></RequireAuth>
}
