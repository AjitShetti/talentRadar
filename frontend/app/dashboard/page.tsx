'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, Mic, Search, Sparkles, SquareCheck, X } from 'lucide-react'
import AppShell from '@/components/AppShell'
import BriefingFeed from '@/components/BriefingFeed'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { ActivityItem, AgendaItem, api, Briefing, JobMatch, SkillsFocus } from '@/lib/api'

type Dimension = { key: string; label: string; score: number; weakest: boolean }
type TrackStat = { track: string; label: string; score: number; sessions: number }
type TrendPoint = { label: string; score: number; date: string | null; completed: boolean }
type InterviewInsights = { sessions_analyzed: number; questions_analyzed: number; average_score: number; delta: number; abandoned: number; trend: TrendPoint[]; dimensions: Dimension[]; weakest_dimension: { key: string; label: string; score: number; hint: string } | null; tracks: TrackStat[]; weakest_track: TrackStat | null; focus: string[] }

/** What the last overnight sweep did — services/sweep.py::get_last_sweep(). */
type Sweep = { finished_at: string | null; sources: string[]; boards: number; postings_read: number; postings_added: number }

/** One ranked action. `why` is evidence, never encouragement. */
type Action = { key: string; title: string; why: string; tags: { label: string; hot?: boolean }[]; cta: string; href: string }

const clock = (iso: string | null) => {
  if (!iso) return null
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? null : at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
}

const sweepTime = (iso: string | null) => clock(iso) ?? '—'

/**
 * Rank today's actions, hardest constraint first.
 *
 * A dated interview is the only thing the user cannot move, so it always leads.
 * After that the order is: finish the setup everything else is computed from,
 * then the briefing's own next-best action, then the cheapest durable win
 * (a first mock), then study, then apply. Three at most — a fourth would make
 * this a list to read rather than a decision to act on.
 */
function buildActions(
  agenda: AgendaItem[],
  briefing: Briefing | null,
  matches: JobMatch[] | null | undefined,
  interviews: InterviewInsights | null | undefined,
  focus: SkillsFocus | null | undefined,
  applied: number,
  onboarded: boolean,
): Action[] {
  const out: Action[] = []

  const today = agenda.filter(item => item.days_away === 0)
  if (today.length > 0) {
    const next = today[0]
    const at = clock(next.scheduled_at)
    out.push({
      key: 'interview-today',
      title: `Prep for ${next.company}`,
      why: `Your ${next.role} interview is today${at ? ` at ${at}` : ''}. Everything below can wait until it is done.`,
      tags: [{ label: at ? `Today ${at}` : 'Today', hot: true }, ...(today.length > 1 ? [{ label: `${today.length} interviews today` }] : [])],
      cta: 'Open prep',
      href: '/interview',
    })
  }

  if (!onboarded) {
    out.push({
      key: 'onboard',
      title: 'Finish your profile',
      why: 'Target roles and a resume are what every match and every gap on this page is computed from. Without them the overnight sweep has nothing to search for.',
      tags: [{ label: 'Blocks everything', hot: true }, { label: '2 minutes' }],
      cta: 'Complete setup',
      href: '/onboarding',
    })
  }

  // The briefing is the deterministic "what needs you" engine (services/copilot.py).
  // Its top card outranks anything derived here, so it goes in before the
  // generic fallbacks rather than being shown in a second competing list.
  const lead = briefing?.cards?.find(card => card.tone === 'primary' || card.tone === 'warning')
  if (lead && out.length < 3) {
    const action = lead.actions?.[0]
    out.push({
      key: `briefing-${lead.id}`,
      title: lead.title,
      why: lead.detail,
      tags: [{ label: lead.kind.replace(/_/g, ' '), hot: lead.tone === 'warning' }],
      cta: action?.label || 'Open',
      href: action?.href || '/applications',
    })
  }

  const sessions = interviews?.sessions_analyzed ?? 0
  if (sessions === 0 && out.length < 3) {
    out.push({
      key: 'first-mock',
      title: 'Run your first mock interview',
      why: 'You have no interview data yet. One session sets your baseline and every prep sheet after it gets sharper.',
      tags: [{ label: 'High impact', hot: true }, { label: '15 minutes' }],
      cta: 'Start now',
      href: '/interview',
    })
  } else if (out.length < 3 && interviews?.weakest_dimension) {
    const weak = interviews.weakest_dimension
    out.push({
      key: 'practise',
      title: `Practise ${weak.label.toLowerCase()}`,
      why: `${weak.label} is your weakest dimension at ${Math.round(weak.score)}/100 across ${interviews.questions_analyzed} answers.`,
      tags: [{ label: `${Math.round(weak.score)}/100` }, ...(interviews.weakest_track ? [{ label: interviews.weakest_track.label }] : [])],
      cta: 'Practise it',
      href: '/interview',
    })
  }

  const gap = focus?.items?.[0]
  if (gap && out.length < 3) {
    out.push({
      key: 'study',
      title: focus?.kind === 'resume_improvements' ? gap.title : `Study ${gap.title}`,
      why: gap.detail,
      tags: [{ label: focus?.kind === 'resume_improvements' ? 'Resume' : 'Biggest gap' }, ...(focus?.target_roles?.length ? [{ label: focus.target_roles[0] }] : [])],
      cta: 'View gap',
      href: '#gaps',
    })
  }

  if (matches && matches.length > 0 && out.length < 3) {
    out.push({
      key: 'apply',
      title: "Open one of last night's roles",
      why: applied === 0
        ? `No applications yet. ${matches.length} posting${matches.length === 1 ? '' : 's'} came back from the overnight sweep — the tracker starts working the moment you send one.`
        : `${matches.length} posting${matches.length === 1 ? '' : 's'} came back from the overnight sweep, matched against your target roles.`,
      tags: [{ label: `${matches.length} waiting` }, { label: 'India · from the sweep' }],
      cta: 'Read them',
      href: '#sweep',
    })
  }

  return out.slice(0, 3)
}

export default function Dashboard() {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [briefingFailed, setBriefingFailed] = useState(false)
  const [error, setError] = useState('')
  const [copilotOpen, setCopilotOpen] = useState(false)

  const loadBriefing = useCallback(async () => {
    try {
      setBriefing(await api.agent.briefing())
      setBriefingFailed(false)
    } catch {
      setBriefingFailed(true)
    }
  }, [])

  useEffect(() => {
    api.dashboard().then(setData).catch(err => setError(err instanceof Error ? err.message : 'Dashboard data is unavailable.'))
    loadBriefing()
  }, [loadBriefing])

  async function dismiss(cardId: string, snoozeDays?: number) {
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
  const interviews = data?.interviews as InterviewInsights | null | undefined
  const jobMatches = data?.job_matches as JobMatch[] | null | undefined
  const sweep = data?.sweep as Sweep | null | undefined
  const agenda = (data?.agenda as AgendaItem[] | undefined) || []
  const activity = (data?.recent_activity as ActivityItem[] | undefined) || []
  const stats = briefing?.stats

  const onboarded = Boolean(profile?.onboarding_completed)
  const applied = stats?.applied ?? 0
  const interviewsToday = agenda.filter(item => item.days_away === 0).length
  const sessions = interviews?.sessions_analyzed ?? 0
  const actions = data ? buildActions(agenda, briefing, jobMatches, interviews, skillsFocus, applied, onboarded) : []

  // Cards already promoted into Today's focus must not appear again below it.
  const usedCardIds = new Set(actions.map(a => a.key.replace(/^briefing-/, '')).filter(k => k !== ''))
  const restBriefing = briefing ? { ...briefing, cards: briefing.cards.filter(c => !usedCardIds.has(c.id)) } : null

  return <RequireAuth><AppShell bleed>
    <div className="ov-page">

      {/* ── masthead: who, when, and the day's shape as data ───────────── */}
      <header className="ov-band ov-masthead">
        <div className="ov-inner">
          <div className="ov-rise">
            <h1>Good to see you{profile?.full_name ? `, ${profile.full_name.split(' ')[0]}` : ''}<span>.</span></h1>
            <p className="ov-sub">
              {interviewsToday > 0
                ? `${interviewsToday} interview${interviewsToday === 1 ? '' : 's'} today — that is the whole plan.`
                : briefing?.headline || 'You are on top of things — nothing is going stale.'}
            </p>
            {data && <div className="ov-readout">
              <div data-live={interviewsToday > 0 ? 'true' : undefined}>
                <b><FlapText value={interviewsToday} /></b><span>Interviews today</span>
              </div>
              <div><b><FlapText value={agenda.length} /></b><span>On the calendar</span></div>
              <div><b><FlapText value={applied} /></b><span>In flight</span></div>
              <div data-live={(jobMatches?.length ?? 0) > 0 ? 'true' : undefined}>
                <b><FlapText value={jobMatches?.length ?? 0} /></b><span>Swept overnight</span>
              </div>
            </div>}
          </div>
          <div className="ov-stamp ov-rise" style={{ animationDelay: '90ms' }}>
            <b>{new Date().toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()}</b>
            {clock(new Date().toISOString())}
            {sweep && <><br />Last sweep {sweepTime(sweep.finished_at)}</>}
            {profile?.target_roles?.[0] && <><br />{profile.target_roles[0]}</>}
          </div>
        </div>
      </header>

      {error && <div className="ov-band"><div className="ov-inner"><p className="form-error">{error}</p></div></div>}

      {/* ── today's focus: the page's reason to exist ──────────────────── */}
      <section className="ov-band ov-focus">
        <div className="ov-inner">
          <div className="ov-head">
            <h2 className="ov-rise" style={{ animationDelay: '110ms' }}>Today&apos;s focus</h2>
            <span className="ov-rule" style={{ animationDelay: '160ms' }} />
            {actions.length > 0 && <span className="ov-note ov-rise" style={{ animationDelay: '220ms' }}>
              {actions.length} item{actions.length === 1 ? '' : 's'} · in order
            </span>}
          </div>
          {!data && !error
            ? <div className="loading-state">Loading your day…</div>
            : actions.length > 0
              ? <FocusList actions={actions} />
              : <div className="dayboard-clear"><CheckCircle2 size={15} /> Board clear — nothing scheduled and nothing going stale.</div>}
        </div>
      </section>

      {/* ── what the sweep brought back ────────────────────────────────── */}
      {data && jobMatches && jobMatches.length > 0 && <section className="ov-band" id="sweep">
        <div className="ov-inner">
          <div className="ov-head">
            <h2>Fetched while you were away</h2>
            <span className="ov-rule" />
            <span className="ov-note">Read against your resume{sweep?.finished_at ? ` · ${sweepTime(sweep.finished_at)}` : ''}</span>
          </div>
          <div className="ov-sweep-list">
            {jobMatches.map(job => <Link className="ov-role" key={job.id} href="/search">
              <span className="ov-rtime">{job.posted_at ? new Date(job.posted_at).toLocaleDateString(undefined, { day: '2-digit', month: 'short' }).toUpperCase() : '—'}</span>
              <div>
                <div className="ov-rtitle">{job.title} · {job.company || 'Company not listed'}</div>
                <p className="ov-rmeta">
                  {job.location || (job.is_remote ? 'Remote' : 'Location not listed')}
                  {job.is_remote && ' · Remote-friendly'}
                  {job.salary_raw ? ` · ${job.salary_raw}` : ''}
                  {job.skills.length > 0 && ` · ${job.skills.slice(0, 4).join(', ')}`}
                </p>
              </div>
              <div className="ov-fit">
                <span className="ov-fk">Kept for</span>
                <span className="ov-fv">{job.matched_role}</span>
              </div>
              <span className="ov-link">Open <ArrowRight size={12} /></span>
            </Link>)}
          </div>
          <div className="ov-sweep-foot">
            <span>
              {sweep
                ? `Swept ${sweep.boards} board${sweep.boards === 1 ? '' : 's'} · ${sweep.postings_read} postings read · ${jobMatches.length} kept`
                : `${jobMatches.length} kept for your target roles`}
            </span>
            <Link className="ov-link" href="/search">Run a search of your own <ArrowRight size={12} /></Link>
          </div>
        </div>
      </section>}

      {/* Anything the briefing flagged that did not make the top three. */}
      {(restBriefing?.cards.length || briefingFailed) ? <section className="ov-band">
        <div className="ov-inner">
          <div className="ov-head"><h2>Also on your radar</h2><span className="ov-rule" /></div>
          <BriefingFeed briefing={restBriefing} failed={briefingFailed} onDismiss={dismiss} />
        </div>
      </section> : null}

      {/* ── quick actions ──────────────────────────────────────────────── */}
      <section className="ov-band ov-quick" aria-label="Quick actions">
        <div className="ov-inner">
          <div className="ov-qrow">
            <Link className="ov-qcol" href="/interview">
              <div className="ov-qtop"><Mic size={15} /><h3>Practice</h3></div>
              <p>Timed answers, scored on structure and signal.</p>
              <span className="ov-link">Start session <ArrowRight size={12} /></span>
            </Link>
            <Link className="ov-qcol" href="/search">
              <div className="ov-qtop"><Search size={15} /><h3>Apply</h3></div>
              <p>Describe a role and we read the boards live.</p>
              <span className="ov-link">Search more <ArrowRight size={12} /></span>
            </Link>
            <Link className="ov-qcol" href="/interview">
              <div className="ov-qtop"><SquareCheck size={15} /><h3>Interview Lab</h3></div>
              <p>Question bank and transcripts, per company.</p>
              <span className="ov-link">Open lab <ArrowRight size={12} /></span>
            </Link>
          </div>
        </div>
      </section>

      {/* ── where you stand ───────────────────────────────────────────── */}
      {data && <section className="ov-band" aria-label="Where you stand">
        <div className="ov-inner">
          <div className="ov-head"><h2>Where you stand</h2><span className="ov-rule" /></div>
          <div className="ov-gauges">
            <div className="ov-gauge">
              <span className="ov-gk">Profile</span>
              <span className="ov-gv"><FlapText value={onboarded ? 100 : 50} />% <small>{onboarded ? 'ready' : 'incomplete'}</small></span>
              <Measure fill={onboarded ? 100 : 50} />
            </div>
            <div className="ov-gauge">
              <span className="ov-gk">Applications</span>
              <span className="ov-gv">
                <FlapText value={analytics?.total_applications ?? 0} />
                <small>{applied > 0 ? `${applied} in flight` : 'none in flight'}</small>
              </span>
              <Measure fill={Math.min(100, (analytics?.total_applications ?? 0) * 10)} />
            </div>
            <div className="ov-gauge">
              <span className="ov-gk">Interview Lab</span>
              <span className="ov-gv">
                {sessions > 0 ? <><FlapText value={Math.round(interviews?.average_score ?? 0)} /><small>avg over {interviews?.questions_analyzed ?? 0} answers</small></> : <>— <small>no sessions yet</small></>}
              </span>
              <Measure fill={sessions > 0 ? Math.round(interviews?.average_score ?? 0) : 0} />
            </div>
          </div>
          <div className="ov-stand-foot">
            <Link className="ov-link" href="/applications">Open tracker <ArrowRight size={12} /></Link>
            <Link className="ov-link" href="/settings">Edit profile <ArrowRight size={12} /></Link>
            {activity.length > 0 && <Link className="ov-link" href="/applications">{activity.length} recent change{activity.length === 1 ? '' : 's'} <ArrowRight size={12} /></Link>}
          </div>
        </div>
      </section>}

      {/* ── close your gaps ───────────────────────────────────────────── */}
      {data && <section className="ov-band" id="gaps">
        <div className="ov-inner">
          <div className="ov-head">
            <h2>Close your gaps</h2>
            <span className="ov-rule" />
            {skillsFocus?.target_roles?.length ? <span className="ov-note">Against {skillsFocus.target_roles.join(' · ')}</span> : null}
          </div>
          {skillsFocus && skillsFocus.items.length > 0
            ? <>
              <div>
                {skillsFocus.items.map((item, i) => <div className="ov-gap" key={`${item.title}-${i}`}>
                  <span className="ov-n">{String(i + 1).padStart(2, '0')}</span>
                  <div>
                    <strong className="ov-name">{item.title}</strong>
                    <p className="ov-gd">{item.detail}</p>
                  </div>
                </div>)}
              </div>
              <div className="ov-gaps-foot">
                <Link className="ov-btn ov-btn-line" href="/resume-studio">Generate learning plan</Link>
                {skillsFocus.resume_filename
                  ? <p>Ranked by how often each one blocked a match. Compared <strong>{skillsFocus.resume_filename}</strong> against your target roles.</p>
                  : <p>Ranked by how often each one blocked a match.</p>}
              </div>
            </>
            : <p className="muted-copy">
              {skillsFocus?.status === 'no_resume'
                ? 'Upload your resume in Profile & Goals and this names the skills these roles expect that you are missing.'
                : 'Add a target role and upload your resume to see the gaps between you and the roles you want.'}
            </p>}
        </div>
      </section>}

    </div>

    {/* ── the copilot: an edge rail, not a panel on the day sheet ─────── */}
    <nav className="ov-railbar" aria-label="Copilot">
      <button
        className="ov-railbtn"
        type="button"
        aria-expanded={copilotOpen}
        aria-controls="ov-copilot"
        aria-label={copilotOpen ? 'Close your copilot' : 'Open your copilot'}
        onClick={() => setCopilotOpen(open => !open)}
      ><Sparkles size={17} /></button>
      <span className="ov-raillabel">Copilot</span>
      <span className="ov-raildot" title="Briefed on today" />
    </nav>

    <CopilotDrawer open={copilotOpen} onClose={() => setCopilotOpen(false)} />
  </AppShell></RequireAuth>
}

/** The measure under a gauge: etched ticks, filled only as far as is true. */
function Measure({ fill }: { fill: number }) {
  return <span className="ov-measure">
    {fill > 0 && <i style={{ width: `${Math.min(100, Math.max(2, fill))}%` }} />}
    {[0, 25, 50, 75].map(at => <b key={at} style={{ left: `${at}%` }} />)}
    <b style={{ right: 0 }} />
  </span>
}

/**
 * The focus list and its one travelling marker.
 *
 * A single amber bar rides the spine between actions rather than three
 * highlights blinking independently. It rests on the first action — the thing
 * to do first — and returns there when the pointer leaves.
 */
function FocusList({ actions }: { actions: Action[] }) {
  const listRef = useRef<HTMLOListElement>(null)
  const gliderRef = useRef<HTMLSpanElement>(null)
  const settle = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [hot, setHot] = useState(0)
  const [awake, setAwake] = useState(false)

  // Position the marker from layout rather than from a guessed row height, and
  // re-measure on resize: the rows reflow at 920px and the spine moves with them.
  useEffect(() => {
    const place = () => {
      const list = listRef.current, glider = gliderRef.current
      if (!list || !glider) return
      const row = list.children[hot] as HTMLElement | undefined
      if (!row) return
      // One transform does both jobs: translateY places the marker, scaleY
      // sizes it against its 1px base. Animating height instead would relayout
      // the spine on every frame of the travel.
      const top = Math.round(row.offsetTop + row.offsetHeight * 0.19)
      const span = Math.round(row.offsetHeight * 0.62)
      glider.style.transform = `translateY(${top}px) scaleY(${span})`
      glider.style.opacity = '1'
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [hot, actions.length])

  useEffect(() => () => { if (settle.current) clearTimeout(settle.current) }, [])

  const enter = (i: number) => {
    if (settle.current) clearTimeout(settle.current)
    setHot(i); setAwake(true)
  }
  const leave = () => {
    settle.current = setTimeout(() => { setHot(0); setAwake(false) }, 90)
  }

  return <div className="ov-focus-wrap">
    <span className="ov-glider" ref={gliderRef} aria-hidden="true" />
    <ol className="ov-focus-list" ref={listRef} data-awake={awake ? 'true' : undefined} onMouseLeave={leave}>
      {actions.map((action, i) => <li
        className="ov-act ov-rise"
        key={action.key}
        data-lead={i === 0 ? 'true' : undefined}
        data-hot={hot === i ? 'true' : undefined}
        style={{ animationDelay: `${200 + i * 70}ms` }}
        onMouseEnter={() => enter(i)}
        onFocus={() => enter(i)}
      >
        <span className="ov-plate">{String(i + 1).padStart(2, '0')}</span>
        <div className="ov-act-body">
          <h3>{action.title}</h3>
          <p className="ov-why">{action.why}</p>
          {action.tags.length > 0 && <div className="ov-tags">
            {action.tags.map(tag => <span key={tag.label} data-hot={tag.hot ? 'true' : undefined}>{tag.label}</span>)}
          </div>}
        </div>
        <Link className={`ov-btn ${i === 0 ? 'ov-btn-accent' : 'ov-btn-line'}`} href={action.href}>
          {action.cta} <ArrowRight size={14} />
        </Link>
      </li>)}
    </ol>
  </div>
}

/** The copilot drawer: the workspace, moved off the sheet and behind a scrim. */
function CopilotDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    // Move focus into the panel so the drawer is reachable from the keyboard.
    const first = panel.current?.querySelector<HTMLElement>('input, textarea, button')
    const timer = setTimeout(() => first?.focus(), 320)
    return () => { document.removeEventListener('keydown', onKey); clearTimeout(timer) }
  }, [open, onClose])

  return <>
    <div className="ov-scrim" data-open={open ? 'true' : undefined} onClick={onClose} />
    <aside className="ov-drawer" id="ov-copilot" data-open={open ? 'true' : undefined} aria-label="Your copilot" aria-hidden={!open}>
      <div className="ov-drawer-top">
        <div>
          <h2>Ask your copilot</h2>
          <p>Briefed on your profile, your tracker and last night&apos;s sweep.</p>
        </div>
        <button className="ov-x" type="button" onClick={onClose} aria-label="Close copilot"><X size={15} /></button>
      </div>
      <div className="ov-drawer-body" ref={panel}>
        {/* Mounted only while open: CopilotWorkspace fetches on mount, and an
            always-mounted drawer would fire that request on every page load. */}
        {open && <CopilotWorkspace />}
      </div>
    </aside>
  </>
}
