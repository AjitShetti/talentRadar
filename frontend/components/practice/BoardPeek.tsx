'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ArrowRight, Trophy } from 'lucide-react'
import { api, OverallBoard } from '@/lib/api'

/** The top of the overall leaderboard, and where the viewer stands, on the Practice page. */
export default function BoardPeek() {
  const [board, setBoard] = useState<OverallBoard | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    api.practice.overall().then(setBoard).catch(() => setFailed(true))
  }, [])

  if (failed) return null
  const top = board?.rows.slice(0, 5) ?? []
  const meShown = top.some(row => row.you)

  return <section className="pr-section board-peek" aria-labelledby="board-peek-title">
    <div className="pr-section-head">
      <h2 id="board-peek-title">Top of the leaderboard</h2>
      {board && board.window_challenges > 0 && <span>{board.window_challenges === 1
        ? 'from the last build'
        : `best ${Math.min(4, board.window_challenges)} of the last ${board.window_challenges} builds`}</span>}
      <Link className="pr-section-link" href="/practice/leaderboard">See all <ArrowRight size={13} /></Link>
    </div>
    {!board
      ? <div className="ov2-card ov2-skeleton" aria-busy="true"><i /><i /></div>
      : top.length === 0
        ? <p className="muted-copy">Nobody has been judged yet. Finish this week&apos;s build to be first on the board.</p>
        : <ol className="ov2-card peek-list">
          {top.map(row => <li key={`${row.rank}-${row.name}`} data-you={row.you ? 'true' : undefined}>
            <span className="peek-rank" data-medal={row.rank <= 3 ? row.rank : undefined}>{row.rank <= 3 ? <Trophy size={14} /> : row.rank}</span>
            <span className="peek-name">{row.name}{row.you && <small>You</small>}</span>
            <span className="peek-entries">{row.entries} build{row.entries === 1 ? '' : 's'}</span>
            <b>{row.score.toFixed(1)}</b>
          </li>)}
          {!meShown && board.me && <li data-you="true" className="peek-me">
            <span className="peek-rank">{board.me.rank}</span>
            <span className="peek-name">{board.me.name}<small>You</small></span>
            <span className="peek-entries">{board.me.entries} build{board.me.entries === 1 ? '' : 's'}</span>
            <b>{board.me.score.toFixed(1)}</b>
          </li>}
        </ol>}
  </section>
}
