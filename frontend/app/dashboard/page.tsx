'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import AppShell from '@/components/AppShell'
import BriefingFeed from '@/components/BriefingFeed'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import { useDay, useDayLit, useIstMinute } from '@/components/DayLine'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import ApplyToday, { type SweepSummary } from '@/components/overview/ApplyToday'
import type { InterviewInsights } from '@/components/overview/StrengthsAndGaps'
import YourDay from '@/components/overview/YourDay'
import { AgendaItem, api, Briefing, BriefingCard, currentEmail, JobMatch, SkillsFocus } from '@/lib/api'
import { arrivalPending, markForAction, nextMark, publishBriefing, setDayLit, untilLabel } from '@/lib/day-line'
import { interviewPrepHref } from '@/lib/interview-prep'
import { clock as istClock } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

/** What the Overview reads of today's outreach pack: companies still to review and messages ready. */
type PackCounts = { companies: number; messages_ready: number }

/** One ranked action. `why` is evidence, never encouragement. */
type Action = { key: string; title: string; why: string; tags: { label: string; hot?: boolean }[]; cta: string; href: string }

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
 *   7. today's outreach pack, while a company in it is still to review;
 *   8. a role at interview stage with no time ahead, asking for one;
 *   9. the briefing's own next-best action;
 *  10. the week's event, then who the user met;
 *  11. the one company worth a note (the Outreach suggestion), only without a pack;
 *  12. the cheapest durable win (a first mock), then study;
 *  13. the one Practice suggestion (the weekly contest, else today's question).
 *
 * The agenda and the interview cards hold only times the user entered, so 1 and 4 can
 * state a clock. A role at interview stage with none names no time and no day, and
 * nothing says when it is, so it ranks after today's commitments and ahead of the
 * next-best action: only the user can fill in the time.
 * An event for today is the next dated commitment after an interview, and it
 * expires tonight, so it outranks setup. A follow-up is a date the user set
 * themselves, so it outranks the briefing's next-best action; an interview within a
 * day is a harder one, so the follow-up ranks below it. A take-home deadline is
 * dated too and a missed one cannot be recovered, so it ranks above the follow-up.
 * Today's pack is the day's outreach already prepared and is replaced tomorrow, so
 * it follows the follow-up and takes the suggestion's place. The week's event and the
 * people just met are time-bound, so they go ahead of the suggestion, which is a
 * nudge, not a commitment. Three at most - a fourth would make this a list to read
 * rather than a decision to act on. Last night's roles are not here: they have their
 * own box above, "Apply today".
 */
function buildActions(
  agenda: AgendaItem[],
  briefing: Briefing | null,
  interviews: InterviewInsights | null | undefined,
  focus: SkillsFocus | null | undefined,
  onboarded: boolean,
  pack: PackCounts | null = null,
): Action[] {
  const out: Action[] = []

  const today = agenda.filter(item => item.days_away === 0)
  if (today.length > 0) {
    const next = today[0]
    const at = istClock(next.scheduled_at)
    out.push({
      // Keyed as its briefing card, so the row and its mark on the day line find each other.
      key: `briefing-scheduled_interview:${next.application_id}`,
      title: `Prep for ${next.company}`,
      why: `Your ${next.role} interview is today${at ? ` at ${at} IST` : ''}. Everything below can wait until it is done.`,
      tags: [{ label: at ? `Today ${at}` : 'Today', hot: true }, ...(today.length > 1 ? [{ label: `${today.length} interviews today` }] : [])],
      cta: 'Open prep',
      href: interviewPrepHref(next.job_id, next.role, next.company),
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
  const interviewTodayShown = today.length > 0
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

  // Today's pack. The briefing's card says it once the pack exists; on the day's first
  // visit the Overview's own read builds the pack, and its counts stand in for the card.
  const packCard = cards.find(card => card.kind === 'outreach_pack')
  const packNow: PackCounts | null = packCard
    ? { companies: metaNumber(packCard, 'companies') ?? 0, messages_ready: metaNumber(packCard, 'messages_ready') ?? 0 }
    : pack
  if (packNow && packNow.companies > 0 && out.length < 3 && !(packCard && taken(packCard))) {
    const { companies, messages_ready: ready } = packNow
    out.push({
      key: packCard ? `briefing-${packCard.id}` : 'outreach-pack',
      title: "Review today's pack",
      why: `${companies === 1 ? 'One company' : `${companies} companies`} picked from your profile, each with who to write to${ready > 0 ? ' and a message ready to review' : ''}. You send it yourself.`,
      tags: [{ label: `${companies} ${companies === 1 ? 'company' : 'companies'}`, hot: true }, { label: `${ready} ready` }],
      cta: 'Review',
      href: '/outreach#today',
    })
  }
  const packShown = out.some(action => action.href === '/outreach#today')

  // A role at interview stage with no time ahead. The card names none, and asks for it.
  const atStage = cards.find(card => card.kind === 'interview_stage')
  if (atStage && out.length < 3 && !taken(atStage)) {
    out.push(fromCard(atStage, [{ label: 'Interview stage' }, { label: 'No time ahead' }], { cta: 'Add the time', href: '/applications' }))
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
  if (suggestion && !packShown && out.length < 3 && !taken(suggestion)) {
    out.push(fromCard(suggestion, [{ label: 'Outreach' }], { cta: 'See who to contact', href: '/outreach' }))
  }

  // When the briefing already sends the user to the Interview Lab, a second
  // interview row would spend one of three slots repeating it.
  const interviewLed = out.some(action => action.href.startsWith('/interview'))
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

  // The one Practice suggestion (services/copilot.py): the weekly contest, else today's
  // problem, DSA or system design. A suggestion, not a commitment, so it only takes a slot nothing else wanted.
  // A take-home due within hours is a `practice_deadline` card and already led, above.
  const practice = briefing?.cards?.find(card => card.kind === 'practice_contest' || card.kind === 'practice_today')
  if (practice && out.length < 3) {
    const action = practice.actions?.[0]
    out.push({
      key: `briefing-${practice.id}`,
      title: practice.title,
      why: practice.detail,
      tags: [{ label: practice.kind === 'practice_contest' ? 'Weekly contest' : practice.meta?.kind === 'design' ? 'System design' : 'Daily question' }],
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

export default function Dashboard() {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [briefingFailed, setBriefingFailed] = useState(false)
  const [pack, setPack] = useState<PackCounts | null>(null)
  const [error, setError] = useState('')
  const [copilotOpen, setCopilotOpen] = useState(false)
  const fabRef = useRef<HTMLButtonElement>(null)
  // Only read here: the day line records that it played. The tree it decides is client-only, behind RequireAuth.
  const [arrive] = useState(() => typeof window !== 'undefined' && arrivalPending())
  const { marks } = useDay()
  const lit = useDayLit()

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
    // The day's first read builds today's pack, so the action is there before the briefing knows of it.
    api.outreach.pack.get()
      .then(p => setPack({ companies: p.companies.filter(c => c.items.some(i => i.status === 'new')).length, messages_ready: p.counts.messages_ready }))
      .catch(() => setPack(null))
  }, [loadBriefing])

  // The line reads the same briefing rather than asking again, and a dismissed card leaves it too.
  useEffect(() => { if (briefing) publishBriefing(briefing, currentEmail()) }, [briefing])

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
  const skillsFocus = data?.skills_focus as SkillsFocus | null | undefined
  const interviews = data?.interviews as InterviewInsights | null | undefined
  const jobMatches = data?.job_matches as JobMatch[] | null | undefined
  const sweep = data?.sweep as SweepSummary | null | undefined
  const agenda = (data?.agenda as AgendaItem[] | undefined) || []

  const onboarded = Boolean(profile?.onboarding_completed)
  const actions = data ? buildActions(agenda, briefing, interviews, skillsFocus, onboarded, pack) : []

  // Cards already promoted into Up next must not appear again below it.
  const usedCardIds = new Set(actions.map(a => a.key.replace(/^briefing-/, '')).filter(k => k !== ''))
  // Before onboarding, "Finish your profile" in Up next is the same ask as the briefing's setup card.
  const restBriefing = briefing ? { ...briefing, cards: briefing.cards.filter(c => !usedCardIds.has(c.id) && !(!onboarded && c.meta.action === 'complete_onboarding')) } : null

  const first = profile?.full_name?.split(' ')[0]

  return <RequireAuth><AppShell>
    <div className="ov2">
      <header className="ov2-hero" data-arrive={arrive ? 'true' : undefined}>
        <div className="day-head">
          <h1>{greeting()}{first ? `, ${first}` : ''}.</h1>
          <span className="ov2-date">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</span>
        </div>
        <NextOnTheLine />
      </header>

      {error && <p className="form-error">{error}</p>}

      {/* ── what to apply for today: last night's sweep, as a stack to work through ── */}
      {jobMatches && jobMatches.length > 0 && <ApplyToday matches={jobMatches} sweep={sweep} />}

      {/* ── what to do next: the reason the page exists ─────────────────── */}
      {(data || !error) && <section className="ov2-section" aria-labelledby="ov2-next">
        <div className="ov2-head"><h2 id="ov2-next">Up next</h2>{actions.length > 0 && <span>{actions.length} thing{actions.length === 1 ? '' : 's'}, in order</span>}</div>
        {!data && !error
          ? <div className="ov2-card ov2-skeleton" aria-busy="true"><i /><i /><i /></div>
          : actions.length > 0
            ? <ol className="ov2-next">
              {actions.map((action, i) => {
                const mark = markForAction(action.key, marks)
                return <li
                  key={action.key}
                  data-lead={i === 0 ? 'true' : undefined}
                  data-lit={mark && lit === mark.id ? 'true' : undefined}
                  style={{ '--d': `${(arrive ? 380 : 0) + i * 90}ms` } as React.CSSProperties}
                  onPointerEnter={mark ? () => setDayLit(mark.id) : undefined}
                  onPointerLeave={mark ? () => setDayLit(null) : undefined}
                  onFocus={mark ? () => setDayLit(mark.id) : undefined}
                  onBlur={mark ? () => setDayLit(null) : undefined}
                >
                  <span className="ov2-num"><FlapText value={i + 1} /></span>
                  <div className="ov2-next-body">
                    <h3>{action.title}</h3>
                    <p>{action.why}</p>
                    {action.tags.length > 0 && <div className="ov2-tags">{action.tags.map(tag => <span key={tag.label} data-hot={tag.hot ? 'true' : undefined}>{tag.label}</span>)}</div>}
                  </div>
                  <ActionButton action={action} lead={i === 0} />
                </li>
              })}
            </ol>
            : <div className="ov2-card ov2-clear">All clear. Nothing scheduled and nothing going stale.</div>}
      </section>}

      {/* ── the routine, to tick off: it is not here until the day line has read it ── */}
      <YourDay />

      {/* Anything the briefing flagged that did not make Up next. */}
      {(restBriefing?.cards.length || briefingFailed) ? <section className="ov2-section">
        <div className="ov2-head"><h2>Also on your radar</h2></div>
        <BriefingFeed briefing={restBriefing} failed={briefingFailed} onDismiss={dismiss} />
      </section> : null}
    </div>

    <button
      ref={fabRef}
      className="outline-button ov2-copilot"
      type="button"
      aria-expanded={copilotOpen}
      aria-controls="ov-copilot"
      onClick={() => setCopilotOpen(open => !open)}
    >Ask copilot</button>

    <CopilotDrawer open={copilotOpen} onClose={() => { setCopilotOpen(false); fabRef.current?.focus() }} />
  </AppShell></RequireAuth>
}

/** The next dated thing today, counted down on the minute. Nothing dated, nothing said. */
function NextOnTheLine() {
  const now = useIstMinute()
  const { marks } = useDay()
  const next = now === null ? null : nextMark(marks, now)
  if (now === null || !next) return null
  return <p className="ov2-quiet"><Link className="ov2-when" href={next.href}>{next.label} in <b>{untilLabel(next.at - now)}</b></Link></p>
}

/** An absolute http(s) URL opens in a new tab with the safe link props; an in-app path stays a Link. */
function ActionButton({ action, lead }: { action: Action; lead: boolean }) {
  const className = lead ? 'primary-button' : 'outline-button'
  const external = externalHref(action.href)
  if (external) {
    return <a className={className} href={external} {...EXTERNAL_LINK_PROPS}>
      {action.cta} <span aria-hidden="true">↗</span>
    </a>
  }
  return <Link className={className} href={action.href}>
    {action.cta}
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
        <button className="ov-close" type="button" onClick={onClose} aria-label="Close copilot">Close</button>
      </div>
      <div className="ov-drawer-body" ref={panel}>
        {/* Mounted only while open: CopilotWorkspace fetches on mount, and an
            always-mounted drawer would fire that request on every page load. */}
        {open && <CopilotWorkspace />}
      </div>
    </aside>
  </>
}
