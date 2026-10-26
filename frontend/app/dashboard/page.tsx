'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, ArrowUpRight, CheckCircle2, ChevronRight, Lightbulb, Sparkles, Target, TrendingDown, TrendingUp, X } from 'lucide-react'
import AppShell from '@/components/AppShell'
import BriefingFeed from '@/components/BriefingFeed'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { Ring, ScoreBars, Segment, Sparkline, StackBar } from '@/components/overview/Charts'
import { AgendaItem, api, Briefing, BriefingCard, JobMatch, OutreachHome, SkillsFocus } from '@/lib/api'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

type Dimension = { key: string; label: string; score: number; weakest: boolean }
type TrackStat = { track: string; label: string; score: number; sessions: number }
type TrendPoint = { label: string; score: number; date: string | null; completed: boolean }
type InterviewInsights = { sessions_analyzed: number; questions_analyzed: number; average_score: number; delta: number; abandoned: number; trend: TrendPoint[]; dimensions: Dimension[]; weakest_dimension: { key: string; label: string; score: number; hint: string } | null; tracks: TrackStat[]; weakest_track: TrackStat | null; focus: string[] }

/** One stage of GET /dashboard/overview's `funnel`: how many applications sit at it now. */
type FunnelStage = { stage: string; count: number; conversion_rate: number }

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

/** A number the server put in a briefing card's `meta`, or null when it is missing or not one. */
const metaNumber = (card: BriefingCard, key: string): number | null => {
  const value = card.meta?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * A briefing card as a ranked action. The card's first button is the call to
 * action, and the key carries the card id so the briefing below does not repeat it.
 */
function fromCard(card: BriefingCard, tags: Action['tags'], fallback: { cta: string; href: string }): Action {
  const first = card.actions?.[0]
  return {
    key: `briefing-${card.id}`,
    title: card.title,
    why: card.detail,
    tags,
    cta: first?.label || fallback.cta,
    href: first?.href || fallback.href,
  }
}

/** The tag a briefing lead carries. Card kinds are internal names ("saved_backlog"); this is what a person reads. */
const LEAD_TAGS: Record<string, string> = {
  priority: 'Next best step',
  saved_backlog: 'Saved, not applied',
  stale_application: 'Going quiet',
  scheduled_interview: 'Interview',
  interview_momentum: 'Practice',
  closed_role: 'Role closed',
}
const leadTag = (kind: string) => LEAD_TAGS[kind] ?? kind.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

/** What the "event_today" tag says: the card's own title says "Tonight" for an evening start, "Today" for an earlier one. */
const todayTag = (card: BriefingCard) => card.tone === 'warning'
  ? { label: 'Cancelled', hot: true }
  : { label: card.title.startsWith('Tonight') ? 'Tonight' : 'Today', hot: true }

/**
 * Rank today's actions, hardest constraint first.
 *
 * A dated interview is the only thing the user cannot move, so it always leads.
 * The order is:
 *   1. an interview today;
 *   2. an event on the user's list for today;
 *   3. finish the setup everything else is computed from;
 *   4. an interview on the calendar within a day (the briefing's card);
 *   5. a take-home due within hours (Practice);
 *   6. an Outreach follow-up that is due;
 *   7. the briefing's own next-best action;
 *   8. the week's event, then who the user met;
 *   9. the one company worth a note (the Outreach suggestion);
 *  10. the cheapest durable win (a first mock), then study, then apply;
 *  11. the one Practice suggestion (the weekly contest, else today's question).
 *
 * An event for today is the next dated commitment after an interview, and it
 * expires tonight, so it outranks setup. A follow-up is a date the user set
 * themselves, so it outranks the briefing's next-best action; an interview within a
 * day is a harder one, so the follow-up ranks below it. A take-home deadline is
 * dated too and a missed one cannot be recovered, so it ranks above the follow-up. The week's event and the
 * people just met are time-bound, so they go ahead of the suggestion, which is a
 * nudge, not a commitment. Three at most - a fourth would make this a list to read
 * rather than a decision to act on.
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

  // The server lists the best one first (one the user is going to, then one that starts today), so the first is the one shown.
  const eventToday = briefing?.cards?.find(card => card.kind === 'event_today')
  if (eventToday) out.push(fromCard(eventToday, [todayTag(eventToday)], { cta: 'Open event', href: '/events' }))

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
  // Its cards outrank anything derived here, so they go in before the generic
  // fallbacks rather than being shown in a second competing list.
  const cards = briefing?.cards ?? []
  const taken = (card: BriefingCard) => out.some(action => action.key === `briefing-${card.id}`)

  // An interview within a day. Today's is already the first action, and the briefing
  // has a card for the same interview: showing it again would spend a second slot
  // saying it twice, so that card is skipped here and as the lead.
  const interviewTodayShown = out.some(action => action.key === 'interview-today')
  const repeatsToday = (card: BriefingCard) =>
    interviewTodayShown && card.kind === 'scheduled_interview' && metaNumber(card, 'days_away') === 0
  const soon = cards.find(card => {
    const days = metaNumber(card, 'days_away')
    return card.kind === 'scheduled_interview' && days !== null && days <= 1 && !repeatsToday(card)
  })
  if (soon && out.length < 3) {
    out.push(fromCard(soon, [{ label: metaNumber(soon, 'days_away') === 0 ? 'Today' : 'Tomorrow', hot: true }, { label: 'Interview' }], { cta: 'Open prep', href: '/interview' }))
  }

  // A take-home due within hours. A missed deadline cannot be recovered and a follow-up
  // can slip a day, so this ranks above the Outreach follow-up (controller ruling,
  // 2026-10-20) and has its own slot, which is why the lead below skips 'practice_' cards.
  const deadline = cards.find(card => card.kind === 'practice_deadline')
  if (deadline && out.length < 3 && !taken(deadline)) {
    out.push(fromCard(deadline, [{ label: 'Take-home', hot: true }, { label: 'Due soon' }], { cta: 'Open submission', href: '/practice' }))
  }

  // Everything owed on Outreach arrives as one card; its count is what is due.
  const followUp = cards.find(card => card.kind === 'outreach_followup')
  if (followUp && out.length < 3 && !taken(followUp)) {
    const due = metaNumber(followUp, 'count') ?? 1
    out.push(fromCard(followUp, [{ label: `${due} due`, hot: true }, { label: 'Outreach' }], { cta: 'Open Outreach', href: '/outreach' }))
  }

  // The lead is the briefing's next-best action. Outreach, event and Practice cards have
  // their own slots: as the lead they would count the same card twice.
  // "Finish your profile" above already says what the briefing's complete_onboarding card says.
  const repeatsSetup = (card: BriefingCard) => !onboarded && card.meta.action === 'complete_onboarding'
  const lead = cards.find(card => (card.tone === 'primary' || card.tone === 'warning') && !card.kind.startsWith('outreach_') && !card.kind.startsWith('event_') && !card.kind.startsWith('practice_') && !taken(card) && !repeatsToday(card) && !repeatsSetup(card))
  if (lead && out.length < 3) {
    out.push(fromCard(lead, [{ label: leadTag(lead.kind), hot: lead.tone === 'warning' }], { cta: 'Open', href: '/applications' }))
  }

  // Then the week ahead and the people just met, if there is still room. Both are
  // time-bound, so they go ahead of the Outreach suggestion.
  const eventSoon = cards.find(card => card.kind === 'event_soon')
  if (eventSoon && out.length < 3 && !taken(eventSoon)) out.push(fromCard(eventSoon, [{ label: 'This week' }], { cta: 'Open event', href: '/events' }))
  const eventMet = cards.find(card => card.kind === 'event_met_prompt')
  if (eventMet && out.length < 3 && !taken(eventMet)) out.push(fromCard(eventMet, [{ label: 'Who you met' }], { cta: 'Add who you met', href: '/events' }))

  // One company worth a note, only while the day's target is not met (the server
  // leaves the card out otherwise). It is a nudge, so it ranks below the lead.
  const suggestion = cards.find(card => card.kind === 'outreach_suggestion')
  // The card's detail already reads "<reasons> · Today: n of m reached", so its tag only says where it leads.
  if (suggestion && out.length < 3 && !taken(suggestion)) {
    out.push(fromCard(suggestion, [{ label: 'Outreach' }], { cta: 'See who to contact', href: '/outreach' }))
  }

  // When the briefing already sends the user to the Interview Lab, a second
  // interview row would spend one of three slots repeating it.
  const interviewLed = out.some(action => action.href === '/interview')
  const sessions = interviews?.sessions_analyzed ?? 0
  if (!interviewLed && sessions === 0 && out.length < 3) {
    out.push({
      key: 'first-mock',
      title: 'Run your first mock interview',
      why: 'You have no interview data yet. One session sets your baseline and every prep sheet after it gets sharper.',
      tags: [{ label: 'High impact', hot: true }, { label: '15 minutes' }],
      cta: 'Start now',
      href: '/interview',
    })
  } else if (!interviewLed && out.length < 3 && interviews?.weakest_dimension) {
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
      // The study plan itself is built in the copilot; a resume fix belongs in the studio.
      cta: focus?.kind === 'resume_improvements' ? 'Open Resume Studio' : 'Build a study plan',
      href: focus?.kind === 'resume_improvements' ? '/resume-studio' : '/agent',
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

  // The one Practice suggestion (services/copilot.py): the weekly contest, else today's
  // question. A suggestion, not a commitment, so it only takes a slot nothing else wanted.
  // A take-home due within hours is a `practice_deadline` card and already led, above.
  const practice = briefing?.cards?.find(card => card.kind === 'practice_contest' || card.kind === 'practice_today')
  if (practice && out.length < 3) {
    const action = practice.actions?.[0]
    out.push({
      key: `briefing-${practice.id}`,
      title: practice.title,
      why: practice.detail,
      tags: [{ label: practice.kind === 'practice_contest' ? 'Weekly contest' : 'Daily question' }],
      cta: action?.label || 'Open',
      href: action?.href || '/practice',
    })
  }

  return out.slice(0, 3)
}

/** Morning, afternoon or evening, by the user's own clock. */
const greeting = () => {
  const hour = new Date().getHours()
  return hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
}

/** Application statuses as parts of the whole, in pipeline order. Anything else (screening, withdrawn) is "Other". */
function pipeline(funnel: FunnelStage[], analytics: Record<string, number> | undefined): Segment[] {
  const saved = funnel.find(stage => stage.stage === 'saved')?.count ?? 0
  const parts: Segment[] = [
    { key: 'saved', label: 'Saved', value: saved, color: '#636366' },
    { key: 'applied', label: 'Applied', value: analytics?.applied ?? 0, color: 'var(--accent)' },
    { key: 'oa', label: 'Assessment', value: analytics?.online_assessment ?? 0, color: 'var(--teal)' },
    { key: 'interview', label: 'Interview', value: analytics?.interview ?? 0, color: 'var(--indigo)' },
    { key: 'offer', label: 'Offer', value: analytics?.offers ?? 0, color: 'var(--success)' },
    { key: 'rejected', label: 'Closed', value: analytics?.rejected ?? 0, color: 'var(--danger)' },
  ]
  const other = (analytics?.total_applications ?? 0) - parts.reduce((sum, part) => sum + part.value, 0)
  if (other > 0) parts.push({ key: 'other', label: 'Other', value: other, color: '#48484A' })
  return parts
}

export default function Dashboard() {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [briefingFailed, setBriefingFailed] = useState(false)
  const [outreach, setOutreach] = useState<OutreachHome | null>(null)
  const [error, setError] = useState('')
  const [copilotOpen, setCopilotOpen] = useState(false)
  const fabRef = useRef<HTMLButtonElement>(null)

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
    // Who the user is talking to comes from Outreach; without it that tile says so rather than showing zeros.
    api.outreach.home().then(setOutreach).catch(() => setOutreach(null))
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
  const funnel = (data?.funnel as FunnelStage[] | undefined) || []
  const skillsFocus = data?.skills_focus as SkillsFocus | null | undefined
  const interviews = data?.interviews as InterviewInsights | null | undefined
  const jobMatches = data?.job_matches as JobMatch[] | null | undefined
  const sweep = data?.sweep as Sweep | null | undefined
  const agenda = (data?.agenda as AgendaItem[] | undefined) || []
  const stats = briefing?.stats

  const onboarded = Boolean(profile?.onboarding_completed)
  const applied = stats?.applied ?? 0
  const interviewsToday = agenda.filter(item => item.days_away === 0).length
  const actions = data ? buildActions(agenda, briefing, jobMatches, interviews, skillsFocus, applied, onboarded) : []

  // Cards already promoted into Up next must not appear again below it.
  const usedCardIds = new Set(actions.map(a => a.key.replace(/^briefing-/, '')).filter(k => k !== ''))
  // Before onboarding, "Finish your profile" in Up next is the same ask as the briefing's setup card.
  const restBriefing = briefing ? { ...briefing, cards: briefing.cards.filter(c => !usedCardIds.has(c.id) && !(!onboarded && c.meta.action === 'complete_onboarding')) } : null

  // With one target role every match was found for it, so a pill per row would repeat it.
  const matchedRoles = new Set((jobMatches ?? []).map(job => job.matched_role).filter(Boolean))
  const first = profile?.full_name?.split(' ')[0]
  const headline = interviewsToday > 0
    ? `${interviewsToday} interview${interviewsToday === 1 ? '' : 's'} today. That is the whole plan.`
    : briefing?.headline || 'Here is where things stand, and what to do next.'

  return <RequireAuth><AppShell>
    <div className="ov2">
      <header className="ov2-hero">
        <p className="ov2-date">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</p>
        <h1>{greeting()}{first ? `, ${first}` : ''}.</h1>
        <p className="ov2-lead">{headline}</p>
      </header>

      {error && <p className="form-error">{error}</p>}

      {/* ── what to do next: the reason the page exists ─────────────────── */}
      {(data || !error) && <section className="ov2-section" aria-labelledby="ov2-next">
        <div className="ov2-head"><h2 id="ov2-next">Up next</h2>{actions.length > 0 && <span>{actions.length} thing{actions.length === 1 ? '' : 's'}, in order</span>}</div>
        {!data && !error
          ? <div className="ov2-card ov2-skeleton" aria-busy="true"><i /><i /><i /></div>
          : actions.length > 0
            ? <ol className="ov2-card ov2-next">
              {actions.map((action, i) => <li key={action.key} data-lead={i === 0 ? 'true' : undefined} style={{ animationDelay: `${80 + i * 70}ms` }}>
                <span className="ov2-num">{i + 1}</span>
                <div className="ov2-next-body">
                  <h3>{action.title}</h3>
                  <p>{action.why}</p>
                  {action.tags.length > 0 && <div className="ov2-tags">{action.tags.map(tag => <span key={tag.label} data-hot={tag.hot ? 'true' : undefined}>{tag.label}</span>)}</div>}
                </div>
                <ActionButton action={action} lead={i === 0} />
              </li>)}
            </ol>
            : <div className="ov2-card ov2-clear"><CheckCircle2 size={18} /> All clear. Nothing scheduled and nothing going stale.</div>}
      </section>}

      {/* ── your journey, as pictures ────────────────────────────────────── */}
      {data && <section className="ov2-section" aria-labelledby="ov2-journey">
        <div className="ov2-head"><h2 id="ov2-journey">Your journey so far</h2></div>
        <div className="ov2-bento">
          <Link href="/applications" className="ov2-card ov2-tile ov2-wide">
            <div className="ov2-tile-top"><span>Applications</span><ChevronRight size={16} /></div>
            <div className="ov2-big"><FlapText value={analytics?.total_applications ?? 0} /><small>tracked</small></div>
            <StackBar label="Applications by status" segments={pipeline(funnel, analytics)} />
          </Link>

          <Link href="/outreach" className="ov2-card ov2-tile">
            <div className="ov2-tile-top"><span>People</span><ChevronRight size={16} /></div>
            {outreach
              ? <OutreachTile home={outreach} />
              : <p className="ov2-quiet">Start a conversation with someone at a company you like.</p>}
          </Link>

          <Link href="/applications" className="ov2-card ov2-tile">
            <div className="ov2-tile-top"><span>Interviews</span><ChevronRight size={16} /></div>
            <div className="ov2-ring-row">
              <Ring value={(analytics?.interview ?? 0) + (analytics?.offers ?? 0)} max={Math.max(1, analytics?.total_applications ?? 0)} color="var(--indigo)">
                <b><FlapText value={(analytics?.interview ?? 0) + (analytics?.offers ?? 0)} /></b><small>reached</small>
              </Ring>
              <dl className="ov2-dl">
                <div><dt>In progress</dt><dd>{analytics?.interview ?? 0}</dd></div>
                <div><dt>Cracked</dt><dd className="ov2-good">{analytics?.offers ?? 0}</dd></div>
                <div><dt>Coming up</dt><dd>{agenda.length}</dd></div>
              </dl>
            </div>
          </Link>

          <Link href="/interview" className="ov2-card ov2-tile">
            <div className="ov2-tile-top"><span>Mock interviews</span><ChevronRight size={16} /></div>
            {interviews && interviews.sessions_analyzed > 0
              ? <>
                <div className="ov2-big"><FlapText value={Math.round(interviews.average_score)} /><small>avg score</small>
                  {interviews.delta !== 0 && <em data-up={interviews.delta > 0 ? 'true' : undefined}>{interviews.delta > 0 ? '+' : ''}{Math.round(interviews.delta)}</em>}
                </div>
                <Sparkline points={interviews.trend.map(point => point.score)} />
                <p className="ov2-quiet">{interviews.sessions_analyzed} session{interviews.sessions_analyzed === 1 ? '' : 's'} · {interviews.questions_analyzed} answers</p>
              </>
              : <p className="ov2-quiet">No sessions yet. One mock sets your baseline.</p>}
          </Link>
        </div>
      </section>}

      {/* ── strengths, gaps and what the mocks said ─────────────────────── */}
      {data && <StrengthsAndGaps interviews={interviews} focus={skillsFocus} />}

      {/* ── what the sweep brought back ────────────────────────────────── */}
      {data && jobMatches && jobMatches.length > 0 && <section className="ov2-section" id="sweep" aria-labelledby="ov2-sweep">
        <div className="ov2-head"><h2 id="ov2-sweep">Found while you were away</h2><span>Read against your resume{sweep?.finished_at ? ` · ${sweepTime(sweep.finished_at)}` : ''}</span></div>
        <div className="ov2-card ov2-list">
          {jobMatches.map(job => <Link className="ov2-role" key={job.id} href={`/roles/${encodeURIComponent(job.id)}`}>
            <span className="ov2-logo" aria-hidden="true">{(job.company || '?').slice(0, 1).toUpperCase()}</span>
            <div>
              <strong>{job.title}</strong>
              <p>{job.company || 'Company not listed'} · {job.location || (job.is_remote ? 'Remote' : 'Location not listed')}{job.salary_raw ? ` · ${job.salary_raw}` : ''}</p>
            </div>
            {matchedRoles.size > 1 && <span className="ov2-pill" title="The target role this was found for">{job.matched_role}</span>}
            <ChevronRight size={16} className="ov2-chev" />
          </Link>)}
          <div className="ov2-list-foot">
            <span>{sweep ? `${sweep.boards} board${sweep.boards === 1 ? '' : 's'} · ${sweep.postings_read} postings read · ${jobMatches.length} kept` : `${jobMatches.length} kept for your target roles`}</span>
            <Link className="ov-link" href="/search">Search for more <ArrowRight size={13} /></Link>
          </div>
        </div>
      </section>}

      {/* Anything the briefing flagged that did not make Up next. */}
      {(restBriefing?.cards.length || briefingFailed) ? <section className="ov2-section">
        <div className="ov2-head"><h2>Also on your radar</h2></div>
        <BriefingFeed briefing={restBriefing} failed={briefingFailed} onDismiss={dismiss} />
      </section> : null}
    </div>

    {/* ── the copilot: a floating button and a drawer ───────────────────── */}
    <button
      ref={fabRef}
      className="ov2-copilot"
      type="button"
      aria-expanded={copilotOpen}
      aria-controls="ov-copilot"
      onClick={() => setCopilotOpen(open => !open)}
    ><Sparkles size={16} /> Ask copilot</button>

    <CopilotDrawer open={copilotOpen} onClose={() => { setCopilotOpen(false); fabRef.current?.focus() }} />
  </AppShell></RequireAuth>
}

/** The people tile: who replied, who has not yet, and referrals won. */
function OutreachTile({ home }: { home: OutreachHome }) {
  const by = (stage: string) => home.threads.filter(thread => thread.stage === stage).length
  const talking = by('replied')
  const referrals = home.threads.filter(thread => thread.outcome === 'referred').length
  const segments: Segment[] = [
    { key: 'replied', label: 'Talking', value: talking, color: 'var(--success)' },
    { key: 'waiting', label: 'Waiting', value: by('waiting'), color: 'var(--warning)' },
    { key: 'planned', label: 'Planned', value: by('planned'), color: '#636366' },
  ]
  return <>
    <div className="ov2-big"><FlapText value={talking} /><small>talking to you</small></div>
    <StackBar label="Companies by stage" segments={segments} />
    <p className="ov2-quiet"><b className="ov2-good">{referrals}</b> referral{referrals === 1 ? '' : 's'} · {home.conversations_started} conversation{home.conversations_started === 1 ? '' : 's'} started</p>
  </>
}

/** Where the mocks say the user is strong, where they are weak, and what to do about it. */
function StrengthsAndGaps({ interviews, focus }: { interviews: InterviewInsights | null | undefined; focus: SkillsFocus | null | undefined }) {
  const dims = [...(interviews?.dimensions ?? [])].sort((a, b) => b.score - a.score)
  const strong = dims.slice(0, 2).filter(d => d.score >= 60)
  const weak = dims.length > 2 ? dims.slice(-2).reverse() : dims.filter(d => d.weakest)
  const tips = [
    ...(interviews?.weakest_dimension?.hint ? [interviews.weakest_dimension.hint] : []),
    ...(interviews?.focus ?? []),
  ].slice(0, 3)
  const gaps = focus?.items?.slice(0, 3) ?? []
  if (dims.length === 0 && gaps.length === 0) return null

  return <section className="ov2-section" aria-labelledby="ov2-strengths">
    <div className="ov2-head"><h2 id="ov2-strengths">Strengths and gaps</h2>{interviews && interviews.sessions_analyzed > 0 && <span>From {interviews.questions_analyzed} mock answers</span>}</div>
    <div className="ov2-split">
      {dims.length > 0 && <div className="ov2-card ov2-pad">
        <h3 className="ov2-sub">How your answers score</h3>
        <ScoreBars rows={dims.map(d => ({ key: d.key, label: d.label, score: d.score, tone: strong.some(s => s.key === d.key) ? 'good' : weak.some(w => w.key === d.key) ? 'weak' : 'plain' }))} />
        <div className="ov2-chips">
          {strong.map(d => <span key={d.key} data-tone="good"><TrendingUp size={13} /> Strong: {d.label.toLowerCase()}</span>)}
          {weak.map(d => <span key={d.key} data-tone="weak"><TrendingDown size={13} /> Work on: {d.label.toLowerCase()}</span>)}
        </div>
      </div>}
      <div className="ov2-card ov2-pad">
        <h3 className="ov2-sub">How to improve</h3>
        <ul className="ov2-tips">
          {tips.map(tip => <li key={tip}><Lightbulb size={15} /><span>{tip}</span></li>)}
          {gaps.map(gap => <li key={gap.title}><Target size={15} /><span><b>{focus?.kind === 'resume_improvements' ? gap.title : `Learn ${gap.title}`}.</b> {gap.detail}</span></li>)}
          {tips.length === 0 && gaps.length === 0 && <li><Lightbulb size={15} /><span>Run a mock interview and we&apos;ll tell you exactly what to work on.</span></li>}
        </ul>
        <div className="ov2-tip-actions">
          <Link className="outline-button" href="/interview">Practise an answer</Link>
          {gaps.length > 0 && <Link className="text-button" href={focus?.kind === 'resume_improvements' ? '/resume-studio' : '/agent'}>{focus?.kind === 'resume_improvements' ? 'Fix it in Resume studio' : 'Build a study plan'} <ArrowRight size={13} /></Link>}
        </div>
      </div>
    </div>
  </section>
}

/** An absolute http(s) URL opens in a new tab with the safe link props; an in-app path stays a Link. */
function ActionButton({ action, lead }: { action: Action; lead: boolean }) {
  const className = lead ? 'primary-button' : 'outline-button'
  const external = externalHref(action.href)
  if (external) {
    return <a className={className} href={external} {...EXTERNAL_LINK_PROPS}>
      {action.cta} <ArrowUpRight size={14} />
    </a>
  }
  return <Link className={className} href={action.href}>
    {action.cta} <ArrowRight size={14} />
  </Link>
}

/** The copilot drawer: the workspace, moved off the page and behind a scrim. */
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
    <aside className="ov-drawer" id="ov-copilot" data-open={open ? 'true' : undefined} role="dialog" aria-modal={open || undefined} aria-label="Your copilot" aria-hidden={!open}>
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
