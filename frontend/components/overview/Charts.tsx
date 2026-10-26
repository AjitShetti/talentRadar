'use client'

/**
 * The Overview's small charts. Plain SVG and CSS — no chart library — and every
 * one is drawn from a count the server returned, never an estimate.
 */

export type Segment = { key: string; label: string; value: number; color: string }

/** One bar split into the parts of a whole, with a legend under it. */
export function StackBar({ segments, label }: { segments: Segment[]; label: string }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const shown = segments.filter(s => s.value > 0)
  return <div className="ch-stack">
    <div className="ch-stack-bar" role="img" aria-label={`${label}: ${shown.map(s => `${s.value} ${s.label}`).join(', ') || 'none yet'}`}>
      {total === 0
        ? <span className="ch-stack-empty" />
        : shown.map((s, i) => <span key={s.key} style={{ flexGrow: s.value, background: s.color, animationDelay: `${120 + i * 60}ms` }} />)}
    </div>
    <ul className="ch-legend">
      {segments.map(s => <li key={s.key} data-zero={s.value === 0 ? 'true' : undefined}>
        <i style={{ background: s.color }} /><span>{s.label}</span><b>{s.value}</b>
      </li>)}
    </ul>
  </div>
}

/** An activity ring: how far along `value` is towards `max`. */
export function Ring({ value, max, color, size = 112, stroke = 12, children }: { value: number; max: number; color: string; size?: number; stroke?: number; children?: React.ReactNode }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0
  return <div className="ch-ring" style={{ width: size, height: size }}>
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeOpacity={0.18} strokeWidth={stroke} />
      <circle
        className="ch-ring-arc"
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={c}
        style={{ ['--ring-full' as string]: `${c}`, ['--ring-to' as string]: `${c * (1 - share)}`, strokeDashoffset: c * (1 - share) }}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
    <div className="ch-ring-label">{children}</div>
  </div>
}

/** A small line of scores over time, the last point marked. */
export function Sparkline({ points, width = 220, height = 56, color = 'var(--accent)' }: { points: number[]; width?: number; height?: number; color?: string }) {
  if (points.length < 2) return <svg className="ch-spark" width={width} height={height} aria-hidden="true" />
  const max = 100, min = 0, pad = 5
  const x = (i: number) => pad + (i / (points.length - 1)) * (width - pad * 2)
  const y = (v: number) => pad + (1 - (v - min) / (max - min)) * (height - pad * 2)
  const line = points.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(points.length - 1).toFixed(1)},${height} L${x(0).toFixed(1)},${height} Z`
  const last = points[points.length - 1]
  return <svg className="ch-spark" width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={color} stopOpacity=".32" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
    <path d={area} fill="url(#spark-fill)" />
    <path className="ch-spark-line" d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    <circle cx={x(points.length - 1)} cy={y(last)} r="3.5" fill={color} />
  </svg>
}

/** Horizontal bars out of 100, for interview dimensions. */
export function ScoreBars({ rows }: { rows: { key: string; label: string; score: number; tone: 'good' | 'weak' | 'plain' }[] }) {
  return <ul className="ch-bars">
    {rows.map((row, i) => <li key={row.key} data-tone={row.tone}>
      <span className="ch-bars-label">{row.label}</span>
      <span className="ch-bars-track"><i style={{ width: `${Math.max(2, Math.min(100, row.score))}%`, animationDelay: `${150 + i * 70}ms` }} /></span>
      <b>{Math.round(row.score)}</b>
    </li>)}
  </ul>
}
