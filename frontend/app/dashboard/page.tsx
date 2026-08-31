'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, BriefcaseBusiness, CheckCircle2, ExternalLink, MapPin, MessageSquare, Sparkles, Target, UploadCloud } from 'lucide-react'
import AppShell from '@/components/AppShell'
import BriefingFeed from '@/components/BriefingFeed'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { ActivityItem, AgendaItem, api, Briefing, JobMatch, SkillsFocus } from '@/lib/api'

type Dimension = { key: string; label: string; score: number; weakest: boolean }
type TrackStat = { track: string; label: string; score: number; sessions: number }
type WeakMoment = { question: string; score: number; track_label: string; difficulty: string; dimension: string; dimension_score: number; was_followup: boolean }
type TrendPoint = { label: string; score: number; date: string | null; completed: boolean }
type InterviewInsights = { sessions_analyzed: number; questions_analyzed: number; average_score: number; delta: number; abandoned: number; trend: TrendPoint[]; dimensions: Dimension[]; weakest_dimension: { key: string; label: string; score: number; hint: string } | null; tracks: TrackStat[]; weakest_track: TrackStat | null; weak_moments: WeakMoment[]; focus: string[] }

/** One line on the day board: a departure, dated or not. */
type BoardRow = { key: string; time: string; tone: 'now' | 'next' | 'idle'; title: string; detail: string; code: string; href: string }

const pct = (value: number) => `${Math.min(100, Math.max(3, value))}%`

const STATUS_LABELS: Record<string, string> = {
  saved: 'Saved',
  applied: 'Applied',
  online_assessment: 'Online assessment',
  screening: 'Screening',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
}

const statusLabel = (value: string | null) => (value ? STATUS_LABELS[value] || value.replace(/_/g, ' ') : 'Tracker')

/** The board's own dateline — read like a departure board's live date, not a category label. */
function boardDate() {
  return new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
}

/**
 * The time column. Today and tomorrow get a clock because the hour is the
 * thing that matters; anything further out gets a date instead.
 */
function boardTime(item: AgendaItem) {
  const when = new Date(item.scheduled_at)
  if (Number.isNaN(when.getTime())) return '--:--'
  return item.days_away <= 1
    ? when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
    : when.toLocaleDateString(undefined, { day: '2-digit', month: 'short' }).toUpperCase()
}

function activityAge(days: number | null) {
  if (days === null) return '—'
  return days <= 0 ? 'Today' : days === 1 ? 'Yesterday' : `${days}d ago`
}

/**
 * Build the day board.
 *
 * Dated commitments come first and in time order, because they are the only
 * rows the user cannot move. Everything after them is the day's undated work,
 * ranked apply → practise → study, and each row points at the panel further
 * down the page that carries its detail.
 */
function buildBoard(
  agenda: AgendaItem[],
  matches: JobMatch[] | null | undefined,
  interviews: InterviewInsights | null | undefined,
  focus: SkillsFocus | null | undefined,
  onboarded: boolean,
): BoardRow[] {
  const rows: BoardRow[] = agenda.map(item => ({
    key: `agenda-${item.application_id}`,
    time: boardTime(item),
    tone: item.days_away === 0 ? 'now' : item.days_away === 1 ? 'next' : 'idle',
    title: `${item.role} interview`,
    detail: `${item.company} · ${statusLabel(item.status)} · ${item.when_label.toLowerCase()}`,
    code: item.days_away === 0 ? 'Today' : item.when_label,
    href: '/interview',
  }))

  if (!onboarded) {
    rows.push({
      key: 'setup',
      time: '—',
      tone: 'next',
      title: 'Finish your profile',
      detail: 'Target roles and a resume are what every match on this page is computed from.',
      code: 'Setup',
      href: '/settings',
    })
  }

  if (matches && matches.length > 0) {
    rows.push({
      key: 'apply',
      time: '—',
      tone: rows.length === 0 ? 'next' : 'idle',
      title: `Apply to ${matches[0].title}`,
      detail: `${matches[0].company || 'Company not listed'} — and ${matches.length - 1} other match${matches.length === 2 ? '' : 'es'} for your target roles.`,
      code: `${matches.length} match${matches.length === 1 ? '' : 'es'}`,
      href: '#apply',
    })
  }

  if (interviews && interviews.sessions_analyzed > 0) {
    const track = interviews.weakest_track
    const dimension = interviews.weakest_dimension
    rows.push({
      key: 'practise',
      time: '—',
      tone: 'idle',
      title: track ? `Practise a ${track.label.toLowerCase()} round` : 'Practise a mock round',
      detail: dimension
        ? `${dimension.label} is your weakest dimension at ${Math.round(dimension.score)}/100.`
        : 'Raise the difficulty — your scores are even across the board.',
      code: 'Weakest',
      href: '/interview',
    })
  } else {
    rows.push({
      key: 'practise',
      time: '—',
      tone: 'idle',
      title: 'Run your first mock interview',
      detail: 'Fifteen minutes gives this page a real read on where you lose points.',
      code: 'No data',
      href: '/interview',
    })
  }

  if (focus && focus.items.length > 0) {
    rows.push({
      key: 'study',
      time: '—',
      tone: 'idle',
      title: `Study ${focus.items[0].title}`,
      detail: focus.items[0].detail,
      code: focus.kind === 'resume_improvements' ? 'Resume' : 'Skill gap',
      href: '#study',
    })
  }

  return rows
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
  const agenda = (data?.agenda as AgendaItem[] | undefined) || []
  const activity = (data?.recent_activity as ActivityItem[] | undefined) || []
  const targetRoles = profile?.target_roles || []

  const onboarded = Boolean(profile?.onboarding_completed)
  const profileTicks = onboarded ? 5 : 2
  const weakest = interviews?.weakest_dimension
  const board = data ? buildBoard(agenda, jobMatches, interviews, skillsFocus, onboarded) : []
  const interviewsToday = agenda.filter(item => item.days_away === 0).length

  return <RequireAuth><AppShell>
    <section className="board-header">
      <div>
        <span className="board-date">{boardDate()}</span>
        <h1>Good to see you{profile?.full_name ? `, ${profile.full_name.split(' ')[0]}` : ''}<span>.</span></h1>
        <p className="subhead">
          {interviewsToday > 0
            ? `${interviewsToday} interview${interviewsToday === 1 ? '' : 's'} today. Prep for ${agenda[0].company} first — everything below can wait.`
            : briefing?.headline || 'Here is what is worth your time today.'}
        </p>
        {/* The day's shape as data, not prose — the header carries numbers the
            way the rest of the board does. */}
        {data && <div className="dayshape">
          <span data-live={interviewsToday > 0 ? 'true' : undefined}><b>{interviewsToday}</b> interviews today</span>
          <span><b>{agenda.length}</b> on the calendar</span>
          <span><b>{jobMatches?.length ?? 0}</b> new matches</span>
          <span><b>{stats?.applied ?? 0}</b> in flight</span>
        </div>}
      </div>
      <a className="primary-button" href="#copilot"><Sparkles size={16}/> Ask your copilot</a>
    </section>
    {error && <p className="form-error">{error}</p>}

    {/* The day board: every commitment and every job of the day on one strip,
        dated rows first. This is the index — the panels below are the detail. */}
    {!data && !error
      ? <div className="loading-state">Loading your day…</div>
      : <section className="dayboard">
        <div className="dayboard-cols"><b>Time</b><b/><b>Today</b><b>Status</b></div>
        {board.length > 0 ? board.map(row => <Link className="db-row" key={row.key} href={row.href} data-tone={row.tone}>
          <span className="db-time">{row.time}</span>
          <span className="db-lamp"/>
          <span className="db-what"><strong>{row.title}</strong><small>{row.detail}</small></span>
          <span className="db-code">{row.code} <ArrowRight size={13}/></span>
        </Link>) : <div className="dayboard-clear"><CheckCircle2 size={15}/> Board clear — nothing scheduled and nothing going stale.</div>}
      </section>}

    {/* The briefing and the copilot carry their own loading and failure states,
        and the copilot has no other home now — so neither waits on the overview
        payload, and neither disappears if it fails. */}
    <BriefingFeed briefing={briefing} failed={briefingFailed} onDismiss={dismiss}/>

    {data && <>
      {/* Apply. Fresh postings matched against the roles the user actually said
          they want, so this is a shortlist and not a search. */}
      {targetRoles.length > 0 && <section className="panel" id="apply">
        <div className="panel-heading">
          <div><h2>Apply to one of these today</h2></div>
          <Link className="text-button" href="/search">Search more <ArrowRight size={14}/></Link>
        </div>
        {/* Three, not the whole list. This is a shortlist for today — the day
            board already names the total, and Search carries the rest. */}
        {jobMatches && jobMatches.length > 0
          ? <div className="job-results">{jobMatches.slice(0, 3).map(job => <article className="job-card" key={job.id}>
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

      {/* Get better today. One decision with two halves, split by a hairline
          rather than broken into two competing panels. */}
      <section className="panel" id="study">
        <div className="panel-heading">
          <div><h2>Get better today</h2></div>
          <Link className="text-button" href="/interview">Interview lab <ArrowRight size={14}/></Link>
        </div>
        <div className="split-2">
          <div>
            <p className="split-head">Practise</p>
            {hasInterviews && interviews ? <>
              {weakest && <div className="meter weak">
                <div className="meter-top"><strong>{weakest.label}<span className="meter-tag">WEAKEST</span></strong><b>{Math.round(weakest.score)}</b></div>
                <div className="meter-track"><i style={{ width: pct(weakest.score) }}/></div>
              </div>}
              {weakest?.hint && <p className="muted-copy">{weakest.hint}</p>}
              {interviews.tracks.length > 1 && <div className="round-chips">{interviews.tracks.map(track => <span key={track.track} className={track.track === interviews.weakest_track?.track ? 'weak' : ''}>{track.label} <b>{Math.round(track.score)}</b></span>)}</div>}
              <Link className="outline-button focus-cta" href="/interview">
                <MessageSquare size={14}/> Run a {interviews.weakest_track?.label.toLowerCase() || 'mock'} round
              </Link>
            </> : <>
              <p className="muted-copy">No mock interviews yet. One session is enough for this panel to name the dimension costing you the most points.</p>
              <Link className="outline-button focus-cta" href="/interview"><MessageSquare size={14}/> Start your first session</Link>
            </>}
          </div>
          <div>
            <p className="split-head">{focusIsResume ? 'Sharpen your resume' : 'Study'}</p>
            {skillsFocus ? <>
              <p className="focus-headline">{skillsFocus.headline}</p>
              {skillsFocus.items.length > 0
                ? <div className="rank-list">{skillsFocus.items.map((item, i) => <div className="rank-row" key={i}>
                    <span className="rank-n">{String(i + 1).padStart(2, '0')}</span>
                    <div><strong>{item.title}</strong><small>{item.detail}</small></div>
                  </div>)}</div>
                : <Link className="outline-button focus-cta" href="/settings"><UploadCloud size={14}/> {skillsFocus.status === 'no_resume' ? 'Upload your resume' : 'Set your target role'}</Link>}
              {skillsFocus.items.length > 0 && skillsFocus.resume_filename && <p className="focus-source">Compared <strong>{skillsFocus.resume_filename}</strong> against {skillsFocus.target_roles.join(', ')} postings.</p>}
            </> : <p className="muted-copy">Add your target role and upload your resume in Profile &amp; Goals to see the skills these roles expect.</p>}
          </div>
        </div>
      </section>

      {/* What already happened, as a ledger — quieter than the board above it. */}
      {activity.length > 0 && <section className="panel">
        <div className="panel-heading">
          <div><h2>What moved lately</h2></div>
          <Link className="text-button" href="/applications">Full tracker <ArrowRight size={14}/></Link>
        </div>
        <div className="ledger-list">
          {activity.map((item, i) => <div className="ledger-row" key={`${item.application_id}-${i}`}>
            <span className="ledger-when">{activityAge(item.days_ago)}</span>
            <div className="ledger-what">
              <strong>{item.role}</strong> <em>· {item.company}</em>
              <div className="ledger-move">
                {item.from_status && <>{statusLabel(item.from_status)} <i>→</i></>}
                <b data-outcome={item.to_status}>{statusLabel(item.to_status)}</b>
              </div>
              {item.note && <small className="ledger-note">{item.note}</small>}
            </div>
          </div>)}
        </div>
      </section>}

      {/* The counts. Still here, still the system's hairline-divided counter
          row — just no longer the first thing the page says, and now the last
          thing it says before the copilot. */}
      <section className="panel">
        <div className="panel-heading">
          <div><h2>Where you stand</h2></div>
          <Link className="text-button" href="/applications">Open tracker <ArrowRight size={14}/></Link>
        </div>
        <div className="metric-grid">
          <Link href="/settings" className="metric-card">
            <div className="metric-top"><span>Profile status</span><Target size={16}/></div>
            <div className="big-number"><FlapText value={onboarded ? 100 : 50} />%</div>
            <p>{onboarded ? 'Profile ready' : 'Complete your profile'}</p>
            <div className="metric-ticks">{Array.from({ length: 5 }).map((_, i) => <i key={i} data-lit={i < profileTicks ? 'true' : undefined} />)}</div>
          </Link>
          <Link href="/applications" className="metric-card">
            <div className="metric-top"><span>Applications</span><BriefcaseBusiness size={16}/></div>
            <div className="big-number"><FlapText value={analytics?.total_applications ?? 0} /></div>
            <p>roles currently in your tracker</p>
            {stats && <div className="metric-breakdown">
              <span><b>{stats.saved ?? 0}</b> saved</span>
              <span><b>{stats.applied ?? 0}</b> applied</span>
              <span><b>{stats.interviews ?? 0}</b> interviewing</span>
            </div>}
          </Link>
          <Link href="/interview" className="metric-card">
            <div className="metric-top"><span>Interview lab</span><MessageSquare size={16}/></div>
            {hasInterviews && interviews ? <>
              <div className="big-number">
                <FlapText value={Math.round(interviews.average_score)} /><span className="out-of">/100</span>
                {interviews.delta !== 0 && <em className={`metric-delta ${interviews.delta > 0 ? 'up' : 'down'}`}>{interviews.delta > 0 ? '+' : ''}{interviews.delta}</em>}
              </div>
              <p>avg across {interviews.questions_analyzed} answers · weakest: {interviews.weakest_dimension?.label ?? '—'}</p>
              {interviews.trend.length > 1 && <div className="metric-trend">{interviews.trend.slice(-6).map((point, i, arr) => <span key={i} data-latest={i === arr.length - 1 ? 'true' : undefined} title={`${point.label} · ${point.score}%`}>{Math.round(point.score)}</span>)}</div>}
            </> : <>
              <div className="big-number">—</div>
              <p>Run a focused mock interview with your LangGraph agent.</p>
            </>}
          </Link>
        </div>
      </section>

    </>}

    {/* The copilot closes the page. The board above it is the day; this is
        where you go when you want to ask about it. */}
    <div className="copilot-section" id="copilot">
      <CopilotWorkspace/>
    </div>
  </AppShell></RequireAuth>
}
