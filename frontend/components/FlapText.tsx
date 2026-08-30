'use client'

/**
 * Renders a value the way a split-flap board reveals one: characters settle
 * into place in a short staggered cascade instead of just appearing. Keyed by
 * value so a changed score or count replays the cascade rather than sitting
 * static — the board's one authored motion, used everywhere a number lives.
 */
export default function FlapText({ value, className = '' }: { value: string | number; className?: string }) {
  const text = String(value)
  return (
    <span className={`flap-text ${className}`} key={text}>
      {text.split('').map((ch, i) => (
        <span className="flap-char" style={{ '--i': i } as React.CSSProperties} key={i}>{ch}</span>
      ))}
    </span>
  )
}
