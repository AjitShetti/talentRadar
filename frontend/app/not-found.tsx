import type { Metadata } from 'next'
import Link from 'next/link'
import PublicShell from '@/components/public/PublicShell'

export const metadata: Metadata = { title: 'Page not found — TalentRadar', robots: { index: false } }

/** Any unknown address, and a /r or /hiring slug that is not (or no longer) published. */
export default function NotFound() {
  return <PublicShell>
    <article className="pub-role">
      <p className="pub-kicker">Not found</p>
      <h1>This page is not here.</h1>
      <p className="pub-where">The link may be mistyped, or the role it pointed to is no longer listed.</p>
      <div className="pub-actions">
        <Link className="pub-cta" href="/">Go to TalentRadar</Link>
        <Link className="pub-link" href="/check">Check a posting</Link>
      </div>
    </article>
  </PublicShell>
}
