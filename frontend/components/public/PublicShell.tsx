import Link from 'next/link'
import '@/app/public.css'

/**
 * Frame for the pages a stranger lands on from a search result or a shared
 * link: /r, /hiring, /check. Deliberately quieter than the landing page — the
 * visitor came for one fact about one role, so the chrome is a single ruled
 * strip and the content starts immediately.
 */
export default function PublicShell({ children }: { children: React.ReactNode }) {
  return <div className="pub">
    <header className="pub-nav">
      <Link className="pub-brand" href="/"><span className="pub-mark" aria-hidden>R</span><span>Talent<em>Radar</em></span></Link>
      <nav aria-label="Site">
        <Link href="/check">Check a posting</Link>
        <Link href="/login">Sign in</Link>
        <Link className="pub-nav-cta" href="/signup">Create account</Link>
      </nav>
    </header>
    <main className="pub-main">{children}</main>
    <footer className="pub-foot">
      <p>TalentRadar tracks tech roles hiring in India. Open/closed status comes from each employer&rsquo;s own careers feed; we show the evidence, not a guess.</p>
    </footer>
  </div>
}
