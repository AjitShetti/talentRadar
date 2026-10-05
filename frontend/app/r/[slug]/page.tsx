import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { ArrowUpRight } from 'lucide-react'
import LivenessStamp from '@/components/LivenessStamp'
import PublicShell from '@/components/public/PublicShell'
import RoleViewBeacon from '@/components/public/RoleViewBeacon'
import { REVALIDATE_SECONDS, SITE_URL, publicRole, publicRoleList } from '@/lib/public-api'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

/*
  The public page for one role.

  THESIS: a stranger arrives from a search result with one question — is this
  job real? — so the verdict and its evidence are the page. Title, verdict,
  the facts behind it, then one offer: prepare for this role.

  Statically generated and revalidated in the background, so this page never
  waits on the API. Only roles from an employer's own ATS feed have one.
*/
export const revalidate = REVALIDATE_SECONDS
export const dynamicParams = true

export async function generateStaticParams() {
  // Pre-build what the index holds at deploy time; anything newer is built on
  // first request. An unreachable API yields an empty list, not a failed build.
  return (await publicRoleList(200)).map(role => ({ slug: role.slug }))
}

type Props = { params: { slug: string } }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const role = await publicRole(params.slug).catch(() => null)
  if (!role) return { title: 'Role not found — TalentRadar' }
  const where = [role.company, role.location].filter(Boolean).join(' · ')
  const title = `${role.title}${role.company ? ` at ${role.company}` : ''} — is it still open? | TalentRadar`
  const description = `${role.liveness.headline}. ${role.liveness.evidence.slice(0, 2).join('. ')}${where ? ` — ${where}` : ''}.`
  return {
    title, description,
    alternates: { canonical: `${SITE_URL}/r/${role.slug}` },
    // A closed role stays reachable for anyone holding the link, but there is
    // no reason to keep sending search traffic to it.
    robots: role.closed_at ? { index: false, follow: true } : undefined,
    openGraph: { title, description, type: 'article', siteName: 'TalentRadar' },
  }
}

export default async function PublicRolePage({ params }: Props) {
  const role = await publicRole(params.slug)
  if (!role) notFound()
  const original = externalHref(role.source_url)
  const closed = Boolean(role.closed_at)

  return <PublicShell>
    <RoleViewBeacon state={role.liveness.state} />
    <article className="pub-role">
      <p className="pub-kicker">{closed ? 'Role record' : 'Role hiring in India'}</p>
      <h1>{role.title}</h1>
      <p className="pub-where">
        {role.company_slug && role.company
          ? <Link href={`/hiring/${role.company_slug}`}>{role.company}</Link>
          : role.company || 'Company not listed'}
        {role.location ? ` · ${role.location}` : role.is_remote ? ' · Remote' : ''}
        {role.seniority ? ` · ${role.seniority.replace(/_/g, ' ')}` : ''}
      </p>

      <section className="pub-verdict" aria-labelledby="verdict-heading">
        <h2 id="verdict-heading">Is it still open?</h2>
        <LivenessStamp liveness={role.liveness} open />
        <p className="pub-method">Checked against the employer&rsquo;s own careers feed. We report what it says and when we last asked — nothing is estimated.</p>
      </section>

      {role.skills.length > 0 && <section className="pub-skills" aria-labelledby="skills-heading">
        <h2 id="skills-heading">What it asks for</h2>
        <div className="chips">{role.skills.map(skill => <span key={skill}>{skill}</span>)}</div>
      </section>}

      <section className="pub-next">
        {closed
          ? <>
            <h2>This one has closed.</h2>
            <p>The employer no longer lists it. {role.company_slug ? 'See what else they have open, or search roles that are confirmed live.' : 'Search roles that are confirmed live.'}</p>
            <div className="pub-actions">
              {role.company_slug && <Link className="pub-cta" href={`/hiring/${role.company_slug}`}>Open roles at {role.company}</Link>}
              <Link className="pub-link" href="/signup">Find live roles</Link>
            </div>
          </>
          : <>
            <h2>Walk in prepared.</h2>
            <p>Run a mock interview built from this posting, see how your resume lines up against what it asks for, and find out who you know at {role.company || 'the company'} before you apply.</p>
            <div className="pub-actions">
              <Link className="pub-cta" href={`/signup?next=${encodeURIComponent(`/roles/${role.id}`)}`}>Prepare for this role</Link>
              {original && <a className="pub-link" href={original} {...EXTERNAL_LINK_PROPS}>View the original posting <ArrowUpRight size={13} /></a>}
            </div>
          </>}
      </section>
    </article>
  </PublicShell>
}
