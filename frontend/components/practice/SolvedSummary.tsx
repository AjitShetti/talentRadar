import FlapText from '@/components/FlapText'
import { PracticeHistory } from '@/lib/api'

const RADIUS = 66
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const GAP = 5
const KINDS = [
  { key: 'easy', label: 'Easy', tone: 'easy' },
  { key: 'medium', label: 'Medium', tone: 'medium' },
  { key: 'hard', label: 'Hard', tone: 'hard' },
  { key: 'design', label: 'System design', tone: 'design' },
] as const

/** The ring of everything solved, with one arc per kind in proportion, and a tile for each. */
export default function SolvedSummary({ history }: { history: PracticeHistory }) {
  const { solved, available } = history
  let at = -90
  const arcs = KINDS.map(kind => {
    const count = solved[kind.key]
    const span = solved.total ? (count / solved.total) * 360 : 0
    const start = at
    at += span
    return { ...kind, count, length: (span / 360) * CIRCUMFERENCE, start }
  })

  return <section className="pr-card" aria-label="How much you have solved">
    <p className="pr-lbl">Solved</p>
    <div className="pr-solved">
      <div className="pr-ring">
        <svg viewBox="0 0 150 150" aria-hidden="true">
          <circle className="pr-ring-track" cx="75" cy="75" r={RADIUS} />
          {arcs.filter(arc => arc.count > 0).map(arc => <circle
            key={arc.key}
            className="pr-ring-arc"
            data-tone={arc.tone}
            cx="75" cy="75" r={RADIUS}
            strokeDasharray={`${Math.max(0, arc.length - GAP)} ${CIRCUMFERENCE}`}
            transform={`rotate(${arc.start} 75 75)`}
          />)}
        </svg>
        <div className="pr-ring-c"><b><FlapText value={solved.total} /></b><small>{solved.total === 1 ? 'problem solved' : 'problems solved'}</small></div>
      </div>
      <div className="pr-tiles">
        {KINDS.map(kind => <div className="pr-tile" key={kind.key}>
          <span data-tone={kind.tone}>{kind.label}</span>
          <b>{solved[kind.key]}{available[kind.key] != null && <small> / {available[kind.key]}</small>}</b>
        </div>)}
      </div>
    </div>
  </section>
}
