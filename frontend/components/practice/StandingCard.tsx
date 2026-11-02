import FlapText from '@/components/FlapText'
import { OverallBoard } from '@/lib/api'

const BAND_WIDTH = 20

/** The person's judged totals as a line, scaled to 0-100. Drawn from two points up. */
function ScoreLine({ scores }: { scores: number[] }) {
  if (scores.length < 2) return null
  const width = 520
  const height = 150
  const pad = 14
  const x = (i: number) => pad + (i * (width - pad * 2)) / (scores.length - 1)
  const y = (score: number) => height - pad - (score / 100) * (height - pad * 2)
  const points = scores.map((score, i) => `${x(i).toFixed(1)},${y(score).toFixed(1)}`).join(' ')
  return <svg className="pr-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Score by contest: ${scores.join(', ')}`}>
    <polyline points={points} />
    {scores.map((score, i) => <circle key={i} cx={x(i)} cy={y(score)} r="3.5" />)}
  </svg>
}

/**
 * Where the person stands, from judged contests only. No judged contest: an invitation and no chart.
 * Under ten people ranked the board sends no percent and no spread, so neither is drawn.
 */
export default function StandingCard({ board, scores, error }: { board: OverallBoard | null; scores: number[]; error?: string }) {
  if (error) return <section className="pr-card" aria-label="Where you stand"><p className="form-error" role="alert">{error}</p></section>
  if (!board) return null
  const me = board.me
  if (!me) return <section className="pr-card" aria-label="Where you stand">
    <p className="pr-lbl">Where you stand</p>
    <p className="muted-copy">Enter a weekly contest to get a rank.</p>
  </section>

  const latest = scores[scores.length - 1]
  const spread = board.distribution
  const tallest = spread ? Math.max(...spread, 1) : 1
  const myBand = Math.min(Math.floor(me.score / BAND_WIDTH), (spread?.length ?? 1) - 1)

  return <section className="pr-card pr-stand" aria-label="Where you stand">
    <div>
      <div className="pr-stand-nums">
        <div><p className="pr-lbl">Contest rank</p><strong><FlapText value={me.rank} /> <small>of {board.total_ranked}</small></strong></div>
        <div><p className="pr-lbl">Contests</p><strong><FlapText value={me.entries} /></strong></div>
        {latest != null && <div><p className="pr-lbl">Latest score</p><strong><FlapText value={latest.toFixed(1)} /> <small>of 100</small></strong></div>}
      </div>
      <ScoreLine scores={scores} />
    </div>
    {board.top_percent != null && spread && <div>
      <p className="pr-lbl">Where you stand</p>
      <div className="pr-big">Top <FlapText value={`${board.top_percent}%`} /></div>
      <div className="pr-bars" role="img" aria-label="How contest scores are spread, your position highlighted">
        {spread.map((count, band) => <i key={band} data-you={band === myBand ? '' : undefined} style={{ height: `${Math.max(count ? 6 : 2, (count / tallest) * 100)}%` }} />)}
      </div>
      <div className="pr-bars-axis"><span>0</span><span>Best 4 contests, total score</span><span>400</span></div>
    </div>}
  </section>
}
