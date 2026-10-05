import type { Metadata } from 'next'
import PublicShell from '@/components/public/PublicShell'
import PostingCheck from '@/components/public/PostingCheck'
import { SITE_URL } from '@/lib/public-api'

export const metadata: Metadata = {
  title: 'Is this job posting still open? — TalentRadar',
  description: 'Paste a link to a tech job posting and find out whether the employer still lists it, how long it has been open, and whether it has been re-listed. Free, no account needed.',
  alternates: { canonical: `${SITE_URL}/check` },
}

export default function CheckPage() {
  return <PublicShell>
    <article className="pub-role">
      <p className="pub-kicker">Posting check</p>
      <h1>Is this job still open?</h1>
      <p className="pub-where">Paste the link. We ask the employer&rsquo;s own careers feed and tell you what it says.</p>

      <PostingCheck />

      <section className="pub-method-list" aria-labelledby="how-heading">
        <h2 id="how-heading">How the answer is worked out</h2>
        <dl>
          <div><dt>Still served</dt><dd>We ask the employer&rsquo;s applicant-tracking system for that exact posting. If it answers, the role is listed right now.</dd></div>
          <div><dt>How long</dt><dd>The publish date comes from the employer&rsquo;s feed, not from a job board&rsquo;s &ldquo;posted 2 days ago&rdquo;, which resets every time a listing is refreshed.</dd></div>
          <div><dt>Re-listed</dt><dd>For roles we already track, we notice when the same title at the same company returns under a new posting id.</dd></div>
          <div><dt>What we won&rsquo;t do</dt><dd>Give a score, or an answer for a site we cannot ask. If we cannot verify a posting, we say so.</dd></div>
        </dl>
      </section>
    </article>
  </PublicShell>
}
