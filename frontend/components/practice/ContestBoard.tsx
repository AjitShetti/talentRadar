'use client'

import { Fragment, useEffect, useState } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import FlapText from '@/components/FlapText'
import { api, ApiError, BoardRow, ChallengeBoard, OverallBoard } from '@/lib/api'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

type Tab = 'week' | 'overall'
/** What the last read returned, tagged with what it was a read of, so a slow answer for a tab
 *  the reader has left is never shown on the one they are on. */
type Loaded = { key: string; board?: ChallengeBoard; overall?: OverallBoard; error?: string; notOpen?: boolean }

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

const same = (a: Line, b: Line) => a.rank === b.rank && a.name === b.name

function Rows({ lines, me }: { lines: Line[]; me: Line | null }) {
  const all = me ? [...lines, me] : lines
  return <div className="pr-board">
    {all.map((line, i) => <Fragment key={`${line.rank}-${line.name}-${i}`}>
      {line === me && <div className="pr-board-gap">… not shown …</div>}
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

/** Second, first, third, as a podium stands. The first three of `lines`, always three. */
function Podium({ lines }: { lines: Line[] }) {
  const [first, second, third] = lines
  return <ol className="pr-podium" aria-label="Top three">
    {[second, first, third].map(line => <li key={line.rank} className="pr-pod" data-p={line.rank} data-you={line.you ? '' : undefined}>
      <span className="pr-pod-rank">{line.rank}</span>
      <b>{line.name}{line.you ? ' · You' : ''}</b>
      <small><FlapText value={line.total} /></small>
    </li>)}
  </ol>
}

/** "Each person's best four contests from the last 12", said correctly when fewer have run. */
function windowLine(contests: number) {
  if (contests <= 1) return "Each person's contest from the last week"
  return `Each person's best ${contests < 4 ? contests : 'four'} contests from the last ${contests}`
}

/**
 * A contest's ranking. With `tabs`, a This week / Overall switch over `slug`'s board and the overall
 * one; without, just the board for `slug`. No slug on This week means no contest is open.
 */
export default function ContestBoard({ slug = null, tabs = false }: { slug?: string | null; tabs?: boolean }) {
  const [tab, setTab] = useState<Tab>('week')
  const [data, setData] = useState<Loaded | null>(null)
  const wanted = tab === 'overall' ? 'overall' : slug ? `contest:${slug}` : ''

  useEffect(() => {
    if (!wanted) return
    let live = true
    const fail = (err: unknown): Loaded => err instanceof ApiError && err.status === 404
      ? { key: wanted, notOpen: true }
      : { key: wanted, error: err instanceof Error ? err.message : 'Could not load the ranking.' }
    const read: Promise<Loaded> = wanted === 'overall'
      ? api.practice.overall().then(overall => ({ key: wanted, overall }), fail)
      : api.practice.board(slug as string).then(board => ({ key: wanted, board }), fail)
    read.then(next => { if (live) setData(next) })
    return () => { live = false }
  }, [wanted, slug])

  const ready = !!wanted && data?.key === wanted ? data : null
  const waiting = !!wanted && !ready
  const board = ready?.board ?? null
  const overall = ready?.overall ?? null
  const lines = board ? board.rows.map(row => challengeLine(row)) : overall ? overall.rows.map(row => overallLine(row)) : []
  const me = board?.me ? challengeLine(board.me, true) : overall?.me ? overallLine(overall.me, true) : null
  const empty = !!(board || overall) && lines.length === 0 && !me
  const queued = board?.queued ?? 0
  const podium = lines.length >= 3 && lines[2].rank <= 3 ? lines.slice(0, 3) : []
  const rest = lines.slice(podium.length)
  const outside = me && !lines.some(line => line.you || same(line, me)) ? me : null

  return <div className="pr-contest-board">
    {tabs && <div className="status-tabs" role="group" aria-label="Ranking">
      <button type="button" className={tab === 'week' ? 'selected' : ''} aria-pressed={tab === 'week'} onClick={() => setTab('week')}>This week</button>
      <button type="button" className={tab === 'overall' ? 'selected' : ''} aria-pressed={tab === 'overall'} onClick={() => setTab('overall')}>Overall</button>
    </div>}

    {ready?.error && <p className="form-error" role="alert">{ready.error}</p>}

    {waiting
      ? <div className="loading-state"><Loader2 className="spin" /> Loading the ranking…</div>
      : <>
        {!wanted && <div className="empty-state"><h2>No contest is open right now.</h2><p>The next one opens on Friday at 18:00 IST.</p></div>}

        {board?.provisional && <p className="pr-board-note">
          <span className="pr-pill">Provisional</span>{' '}
          <span className="pr-meta">{plural(queued, 'entry', 'entries')} queued{me ? ` · you are ${ordinal(me.rank)} of ${board.total_entries}` : ''}</span>
        </p>}

        {empty && <div className="empty-state"><h2>No judged entries yet.</h2><p>Results appear as the judge works through the queue.</p></div>}

        {(board || overall) && !empty && <>
          {podium.length > 0 && <Podium lines={podium} />}
          {(rest.length > 0 || outside) && <>
            <div className="pr-board-head">
              <span>Rank</span><span>Name</span><span>Score</span><span>{overall ? 'Entries' : 'Hours used'}</span><span />
            </div>
            <Rows lines={rest} me={outside} />
          </>}
          <div className="pr-foot">
            <span>{board
              ? `${plural(board.total_entries, 'entry', 'entries')} judged · ${queued.toLocaleString('en-US')} queued${board.held ? ` · ${board.held.toLocaleString('en-US')} under review` : ''} · names hidden unless chosen`
              : `${windowLine(overall?.window_challenges ?? 12)} · names hidden unless chosen`}</span>
            <span><Link href="/practice#settings">Change your leaderboard name in Practice settings</Link></span>
          </div>
        </>}
      </>}
  </div>
}
