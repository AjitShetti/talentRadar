'use client'

import { Fragment, useEffect, useState } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import PrepTabs from '@/components/PrepTabs'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, BoardRow, ChallengesOverview, ChallengeBoard, OverallBoard } from '@/lib/api'
import { dayTime, shortDate } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

type Tab = 'week' | 'overall' | 'past'
/** What the last read returned, tagged with what it was a read of, so a slow answer for a tab
 *  the reader has left is never shown on the one they are on. */
type Loaded = { key: string; board?: ChallengeBoard; overall?: OverallBoard; error?: string }

/** One row of either board, reduced to what the grid draws. */
type Line = { rank: number; name: string; total: string; detail: string; side: string; repo: string | null; you: boolean }

const rank2 = (rank: number) => String(rank).padStart(2, '0')
function ordinal(n: number) {
  const suffixes = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${suffixes[(v - 20) % 10] || suffixes[v] || suffixes[0]}`
}
const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

function challengeLine(row: BoardRow, you = row.you === true): Line {
  const hours = row.hours_used != null ? `${row.hours_used.toFixed(1)} h` : '—'
  const repo = externalHref(row.repo_url)
  return { rank: row.rank, name: row.name, total: row.total.toFixed(1), detail: `${hours}${repo ? ' · repo ↗' : ''}`, side: hours, repo, you }
}

function overallLine(row: OverallBoard['rows'][number], you = row.you === true): Line {
  const best = Math.min(4, row.entries)
  return { rank: row.rank, name: row.name, total: row.score.toFixed(1), detail: `best ${best} of ${plural(row.entries, 'entry', 'entries')}`, side: plural(row.entries, 'entry', 'entries'), repo: null, you }
}

function Rows({ lines, me }: { lines: Line[]; me: Line | null }) {
  const outside = !!me && !lines.some(line => line.you || (line.rank === me.rank && line.name === me.name))
  const all = outside && me ? [...lines, me] : lines
  return <div className="pr-board">
    {all.map((line, i) => <Fragment key={`${line.rank}-${line.name}-${i}`}>
      {outside && line === me && <div className="pr-board-gap">… not shown …</div>}
      <div className="pr-board-row" data-you={line.you ? '' : undefined}>
        <span className="pr-rank">{rank2(line.rank)}</span>
        <span className="pr-name"><b>{line.name}</b><small>{line.detail}</small></span>
        <span className="pr-total"><FlapText value={line.total} /></span>
        <span className="pr-hours">{line.side}</span>
        {line.repo
          ? <a className="row-arrow pr-repo" href={line.repo} aria-label={`${line.name}'s repo`} {...EXTERNAL_LINK_PROPS}>↗</a>
          : <span />}
      </div>
    </Fragment>)}
  </div>
}

export default function LeaderboardPage() {
  return <RequireAuth><AppShell><PrepTabs /><LeaderboardView /></AppShell></RequireAuth>
}

function LeaderboardView() {
  const [overview, setOverview] = useState<ChallengesOverview | null>(null)
  const [overviewError, setOverviewError] = useState('')
  const [tab, setTab] = useState<Tab>('week')
  const [pastSlug, setPastSlug] = useState('')
  const [data, setData] = useState<Loaded | null>(null)

  useEffect(() => {
    let live = true
    api.practice.challenges()
      .then(next => { if (live) setOverview(next) })
      .catch(err => { if (live) setOverviewError(err instanceof Error ? err.message : 'Could not load the builds.') })
    return () => { live = false }
  }, [])

  const current = overview?.current ?? null
  const slug = tab === 'week' ? current?.slug ?? '' : tab === 'past' ? pastSlug : ''
  const wanted = tab === 'overall' ? 'overall' : slug ? `build:${slug}` : ''

  useEffect(() => {
    if (!wanted) return
    let live = true
    const fail = (err: unknown): Loaded => ({ key: wanted, error: err instanceof Error ? err.message : 'Could not load the leaderboard.' })
    const read: Promise<Loaded> = wanted === 'overall'
      ? api.practice.overall().then(overall => ({ key: wanted, overall }), fail)
      : api.practice.board(slug).then(board => ({ key: wanted, board }), fail)
    read.then(next => { if (live) setData(next) })
    return () => { live = false }
  }, [wanted, slug])

  const ready = !!wanted && data?.key === wanted ? data : null
  const waiting = (tab === 'week' && !overview && !overviewError) || (!!wanted && !ready)
  const board = ready?.board ?? null
  const overall = ready?.overall ?? null
  const title = tab === 'week' ? current?.brief.title : tab === 'past' ? overview?.past.find(item => item.slug === pastSlug)?.title : null

  const lines = board ? board.rows.map(row => challengeLine(row)) : overall ? overall.rows.map(row => overallLine(row)) : []
  const me = board?.me ? challengeLine(board.me, true) : overall?.me ? overallLine(overall.me, true) : null
  const empty = !!(board || overall) && lines.length === 0 && !me
  const queued = board?.queued ?? 0
  const openUntil = tab === 'week' && current ? `the build is open until ${dayTime(current.closes_at)} · ` : ''

  return <>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Practice · leaderboard</span>
        <h1>Who built it best<span>.</span></h1>
        <p>{tab === 'overall'
          ? `Each person's best four builds from the last ${overall?.window_challenges ?? 12} · judged on process first`
          : `${title ? `${title} · ` : ''}Judged on process first`}</p>
      </div>
      <div className="tracker-total">
        <strong>{overall ? <FlapText value={overall.window_challenges} /> : board ? <FlapText value={board.total_entries} /> : '–'}</strong>
        <span>{tab === 'overall' ? 'builds counted' : 'entries judged'}</span>
      </div>
    </section>

    <div className="status-tabs" role="group" aria-label="Leaderboard">
      <button type="button" className={tab === 'week' ? 'selected' : ''} aria-pressed={tab === 'week'} onClick={() => setTab('week')}>This week</button>
      <button type="button" className={tab === 'overall' ? 'selected' : ''} aria-pressed={tab === 'overall'} onClick={() => setTab('overall')}>Overall</button>
      {!!overview?.past.length && <select className="pr-past" aria-label="Past builds" data-on={tab === 'past' ? '' : undefined} value={tab === 'past' ? pastSlug : ''}
        onChange={event => { if (event.target.value) { setPastSlug(event.target.value); setTab('past') } }}>
        <option value="">Past builds</option>
        {overview.past.map(item => <option key={item.slug} value={item.slug}>{shortDate(item.opens_at)} · {item.title}</option>)}
      </select>}
    </div>

    {overviewError && tab === 'week' && <p className="form-error" role="alert">{overviewError}</p>}
    {ready?.error && <p className="form-error" role="alert">{ready.error}</p>}

    {waiting
      ? <div className="loading-state"><Loader2 className="spin" /> Loading the leaderboard…</div>
      : <>
        {tab === 'week' && overview && !current && <div className="empty-state"><h2>No build is open right now.</h2><p>The next one opens on Friday at 18:00 IST. Pick a past build above to see its board.</p></div>}

        {board?.provisional && <p style={{ margin: '14px 0' }}>
          <span className="pr-pill">Provisional</span>{' '}
          <span className="pr-meta">{openUntil}{plural(queued, 'entry', 'entries')} queued{me ? ` · you are ${ordinal(me.rank)} of ${board.total_entries}` : ''}</span>
        </p>}

        {empty && <div className="empty-state"><h2>No judged entries yet.</h2><p>Results appear as the judge works through the queue.</p></div>}

        {(board || overall) && !empty && <>
          <div className="pr-board-head">
            <span>Rank</span><span>Name</span><span>Score</span><span>{overall ? 'Entries' : 'Hours used'}</span><span />
          </div>
          <Rows lines={lines} me={me} />
          <div className="pr-foot">
            <span>{board
              ? `${plural(board.total_entries, 'entry', 'entries')} judged · ${queued.toLocaleString('en-US')} queued${board.held ? ` · ${board.held.toLocaleString('en-US')} under review` : ''} · names hidden unless chosen`
              : `Best four totals from the last ${overall?.window_challenges ?? 12} builds · names hidden unless chosen`}</span>
            <span><Link href="/practice">Change your leaderboard name in Practice settings</Link></span>
          </div>
        </>}
      </>}
  </>
}
