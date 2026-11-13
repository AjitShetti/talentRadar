import FlapText from '@/components/FlapText'

const RADIUS = 66
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const GAP = 5

export type MeterPart = { key: string; label: string; count: number; of?: number | null; tone: string }

/**
 * A LeetCode-style meter: a ring with one arc per part in proportion, the total in the middle,
 * and a tile for each part. `extra` adds one more tile after the parts.
 */
export default function RingMeter({ label, total, unit, parts, extra }: {
  label: string
  total: number
  unit: string
  parts: readonly MeterPart[]
  extra?: { label: string; value: string }
}) {
  const sum = parts.reduce((all, part) => all + part.count, 0)
  let at = -90
  const arcs = parts.map(part => {
    const span = sum ? (part.count / sum) * 360 : 0
    const start = at
    at += span
    return { ...part, length: (span / 360) * CIRCUMFERENCE, start }
  })

  return <section className="pr-card" aria-label={label}>
    <p className="pr-lbl">{label}</p>
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
        <div className="pr-ring-c"><b><FlapText value={total} /></b><small>{unit}</small></div>
      </div>
      <div className="pr-tiles">
        {parts.map(part => <div className="pr-tile" key={part.key}>
          <span data-tone={part.tone}>{part.label}</span>
          <b>{part.count}{part.of != null && <small> / {part.of}</small>}</b>
        </div>)}
        {extra && <div className="pr-tile"><span>{extra.label}</span><b>{extra.value}</b></div>}
      </div>
    </div>
  </section>
}
