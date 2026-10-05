import type { Liveness } from '@/lib/api'

/**
 * Whether a role is still open, and what that is based on.
 *
 * The headline is the verdict; the disclosure holds the evidence lines the
 * backend derived it from (domain/liveness.py). The evidence is always one
 * click away because the verdict is a claim about someone else's hiring, and
 * a claim nobody can check is not worth trusting. State is carried by the
 * wording as well as the mark, never by colour alone.
 */
export default function LivenessStamp({ liveness, open = false }: { liveness?: Liveness | null; open?: boolean }) {
  if (!liveness) return null
  const lines = liveness.evidence || []
  const head = <><span className="liveness-mark" aria-hidden /><span className="liveness-headline">{liveness.headline}</span></>

  if (!lines.length) return <p className={`liveness liveness-${liveness.state}`}>{head}</p>

  return <details className={`liveness liveness-${liveness.state}`} open={open}>
    <summary>{head}<span className="liveness-why">Why</span></summary>
    <ul>{lines.map(line => <li key={line}>{line}</li>)}</ul>
  </details>
}
