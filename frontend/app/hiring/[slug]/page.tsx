import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import PublicShell from '@/components/public/PublicShell'
import { REVALIDATE_SECONDS, SITE_URL, publicCompany } from '@/lib/public-api'

/*
  One company's roles, read as a ledger: how long each has been open and
  whether the employer still lists it. A long-open or repeatedly re-listed
  role is visible at a glance because every row carries the same two facts.
*/
export const revalidate = REVALIDATE_SECONDS
export const dynamicParams = true

export async function generateStaticParams() { return [] }

type Props = { params: { slug: string } }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const company = await publicCompany(params.slug).catch(() => null)
  if (!company) return { title: 'Company not found — TalentRadar' }
  const title = `${company.company} — roles hiring in India, and which are still open | TalentRadar`
  return {
    title,
    description: `${company.open_count} role${company.open_count === 1 ? '' : 's'} at ${company.company} currently listed, with how long each has been open and when it was last confirmed.`,
    alternates: { canonical: `${SITE_URL}/hiring/${company.slug}` },
  }
}

export default async function HiringPage({ params }: Props) {
  const company = await publicCompany(params.slug)
  if (!company) notFound()

  return <PublicShell>
    <article className="pub-role">
      <p className="pub-kicker">Hiring in India</p>
      <h1>{company.company}</h1>
      <p className="pub-where">{company.open_count} of {company.roles.length} tracked role{company.roles.length === 1 ? '' : 's'} currently listed by the employer.</p>

      {company.roles.length === 0
        ? <p className="pub-method">We are not tracking any roles from this company&rsquo;s careers feed right now.</p>
        : <table className="pub-ledger">
          <thead><tr><th scope="col">Role</th><th scope="col">Where</th><th scope="col">Open for</th><th scope="col">Status</th></tr></thead>
          <tbody>{company.roles.map(role => <tr key={role.id} className={role.closed_at ? 'is-closed' : undefined}>
            <th scope="row"><Link href={`/r/${role.slug}`}>{role.title}</Link></th>
            <td>{role.location || (role.is_remote ? 'Remote' : '—')}</td>
            <td className="pub-num">{role.liveness.open_days != null ? `${role.liveness.open_days} d` : '—'}</td>
            <td><span className={`pub-state liveness-${role.liveness.state}`}>{role.liveness.headline}</span></td>
          </tr>)}</tbody>
        </table>}

      <section className="pub-next">
        <h2>Applying here?</h2>
        <p>Open any role to see the evidence behind its status, then prepare with a mock interview built from that posting.</p>
        <div className="pub-actions"><Link className="pub-cta" href="/signup">Create a free account</Link></div>
      </section>
    </article>
  </PublicShell>
}
