'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Bookmark, Check, ExternalLink, FileText, Mic, Users } from 'lucide-react'
import AppShell from '@/components/AppShell'
import LivenessStamp from '@/components/LivenessStamp'
import ReferralAsk from '@/components/ReferralAsk'
import RequireAuth from '@/components/RequireAuth'
import { api, CompanyContact, Dossier } from '@/lib/api'
import { platformLabel } from '@/lib/filters'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'
import { track } from '@/lib/analytics'

/**
 * The Role Dossier.
 *
 * THESIS: one role, four questions, in the order a person actually asks them —
 * is it open, is it for me, how do I get in, am I ready. Each is a numbered
 * entry on the sheet with its own evidence, not four interchangeable cards.
 *
 * Every section comes from a different part of the system and may be missing.
 * A missing section says plainly that it is unavailable; it never borrows the
 * look of an answer.
 */
export default function RoleDossierPage({ params }: { params: { id: string } }) {
  return <RequireAuth><AppShell><DossierView jobId={params.id} /></AppShell></RequireAuth>
}

function DossierView({ jobId }: { jobId: string }) {
  const [dossier, setDossier] = useState<Dossier | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setError('')
    api.roles.dossier(jobId).then(result => { setDossier(result); return result }).catch(err => {
      setError(err instanceof Error ? err.message : 'This role could not be loaded.')
    })
  }, [jobId])
  useEffect(load, [load])
  useEffect(() => { track('dossier_opened') }, [jobId])

  async function setTracked(status: 'saved' | 'applied') {
    if (!dossier || saving) return
    setSaving(true); setError('')
    try {
      const existing = dossier.readiness?.application
      if (existing) await api.applications.update(existing.id, { status })
      else await api.applications.create(jobId, status)
      if (status === 'applied') track('marked_applied', { prepared: Boolean(dossier.readiness?.sessions) })
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The tracker could not be updated.')
    } finally { setSaving(false) }
  }

  if (error && !dossier) return <div className="empty-state"><h2>We could not open this role.</h2><p>{error}</p><a className="text-button" href="/search">Back to Find roles</a></div>
  if (!dossier) return <div className="loading-state">Gathering what we know about this role…</div>

  const { role, liveness, fit, way_in: wayIn, readiness } = dossier
  const closed = liveness?.state === 'closed'
  const status = readiness?.application?.status
  const prepHref = `/interview?job=${encodeURIComponent(role.id)}&role=${encodeURIComponent(role.title)}&company=${encodeURIComponent(role.company || '')}`
  const original = externalHref(role.source_url)

  return <>
    <a className="dossier-back" href="/search"><ArrowLeft size={13} /> Find roles</a>
    <section className="page-heading dossier-heading">
      <div>
        <span className="board-kicker">Role dossier</span>
        <h1>{role.title}<span>.</span></h1>
        <p>
          {role.company || 'Company not listed'}
          {role.location ? ` · ${role.location}` : role.is_remote ? ' · Remote' : ''}
          {role.salary_raw ? ` · ${role.salary_raw}` : ''}
          {platformLabel(role.platform) ? ` · via ${platformLabel(role.platform)}` : ''}
        </p>
      </div>
      {original && <a className="outline-button" href={original} {...EXTERNAL_LINK_PROPS}>View original <ExternalLink size={13} /></a>}
    </section>

    {error && <p className="form-error">{error}</p>}

    <ol className="dossier">
      <li className="dossier-entry">
        <span className="dossier-no" aria-hidden>01</span>
        <div>
          <h2>Is it open?</h2>
          {liveness
            ? <LivenessStamp liveness={liveness} open />
            : <p className="dossier-none">We hold no evidence about this posting yet. Check the original listing before you spend time on it.</p>}
          {closed && <p className="dossier-note">The employer has stopped listing this role. The rest of this page is kept for reference.</p>}
        </div>
      </li>

      <li className="dossier-entry">
        <span className="dossier-no" aria-hidden>02</span>
        <div>
          <h2>Is it for me?</h2>
          {!fit && <p className="dossier-none">The comparison with your resume is unavailable right now.</p>}
          {fit && !fit.available && fit.reason === 'no_resume' && <p className="dossier-none">Add a resume and this section compares it with what the role asks for. <a className="text-button" href="/resume-studio">Open Resume Studio</a></p>}
          {fit && !fit.available && fit.reason === 'no_role_skills' && <p className="dossier-none">This posting does not name its skills, so there is nothing to compare your resume against. Read the original listing.</p>}
          {fit && fit.available && <>
            <p className="dossier-lead"><strong>{fit.matched_skills.length} of {fit.matched_skills.length + fit.missing_skills.length}</strong> skills the role names appear in your resume.</p>
            <SkillRow label="In your resume" skills={fit.matched_skills} tone="have" />
            <SkillRow label="Not found in your resume" skills={fit.missing_skills} tone="miss" />
            {fit.missing_skills.length > 0 && <p className="dossier-note">Not found is not the same as not known — if you have used one of these, say so in the resume you send.</p>}
          </>}
        </div>
      </li>

      <li className="dossier-entry">
        <span className="dossier-no" aria-hidden>03</span>
        <div>
          <h2>What&rsquo;s my way in?</h2>
          {!wayIn && <p className="dossier-none">Contacts for this company are unavailable right now.</p>}
          {wayIn && <>
            <p className="dossier-lead">A referral is read before a cold application is. These are the people and channels on record for {role.company || 'this company'}.</p>
            {wayIn.contacts.length > 0
              ? <ul className="dossier-contacts">{wayIn.contacts.map(contact => <Contact key={contact.id} contact={contact} jobId={role.id} />)}</ul>
              : <>
                <p className="dossier-none">Nobody is on record for {role.company || 'this company'} yet. We do not guess at names or addresses.</p>
                <div className="dossier-unnamed-ask"><span>Already know someone there?</span><ReferralAsk jobId={role.id} /></div>
              </>}
            <div className="dossier-actions">
              <a className="outline-button" href="/company-intel"><Users size={13} /> Find or add a contact</a>
              {externalHref(wayIn.careers_url) && <a className="text-button" href={externalHref(wayIn.careers_url)!} {...EXTERNAL_LINK_PROPS}>Careers page <ExternalLink size={12} /></a>}
            </div>
          </>}
        </div>
      </li>

      <li className="dossier-entry">
        <span className="dossier-no" aria-hidden>04</span>
        <div>
          <h2>Am I ready?</h2>
          {readiness && readiness.last_score != null
            ? <p className="dossier-lead">Your last practice round for this role scored <strong>{Math.round(readiness.last_score)}</strong> out of 100{readiness.sessions > 1 ? `, across ${readiness.sessions} rounds` : ''}.</p>
            : <p className="dossier-lead">You have not practised for this role yet. A round takes about fifteen minutes and is built from this posting.</p>}
          <div className="dossier-actions">
            <a className="primary-button" href={prepHref}><Mic size={14} /> {readiness?.sessions ? 'Practise again' : 'Prepare for this role'}</a>
            <a className="outline-button" href="/resume-studio"><FileText size={13} /> Tailor my resume</a>
          </div>
        </div>
      </li>
    </ol>

    <section className="dossier-track">
      <div>
        <span className="board-kicker">Tracker</span>
        <p>{status ? <>This role is in your tracker as <strong>{status.replace(/_/g, ' ')}</strong>.</> : 'This role is not in your tracker.'}</p>
      </div>
      <div className="dossier-actions">
        {!status && <button className="outline-button" disabled={saving} onClick={() => setTracked('saved')}><Bookmark size={13} /> Save to tracker</button>}
        {(!status || status === 'saved') && <button className="outline-button" disabled={saving || closed} onClick={() => setTracked('applied')}><Check size={13} /> I applied</button>}
        {status && <a className="text-button" href="/applications">Open tracker</a>}
      </div>
    </section>

    {role.description && <details className="dossier-posting">
      <summary>The posting, as we have it</summary>
      <p>{role.description}</p>
    </details>}
  </>
}

function SkillRow({ label, skills, tone }: { label: string; skills: string[]; tone: 'have' | 'miss' }) {
  if (!skills.length) return null
  return <div className={`dossier-skills dossier-skills-${tone}`}>
    <span className="dossier-skills-label">{label}</span>
    <div className="chips">{skills.map(skill => <span key={skill}>{skill}</span>)}</div>
  </div>
}

/**
 * One contact, with where it came from. Provenance is part of the row, not a
 * tooltip: "published by the company" and "you added this" are different
 * kinds of fact and the reader should not have to hover to tell them apart.
 */
function Contact({ contact, jobId }: { contact: CompanyContact; jobId: string }) {
  const link = externalHref(contact.linkedin_url) || externalHref(contact.source_url)
  return <li>
    <div>
      <strong>{contact.name || contact.email || contact.kind.replace(/_/g, ' ')}</strong>
      {contact.title && <span>{contact.title}</span>}
    </div>
    <span className="dossier-provenance">{contact.is_curated ? 'Published' : 'Added by you'}{contact.verified ? ' · verified' : ''}</span>
    {link && <a className="text-button" href={link} {...EXTERNAL_LINK_PROPS}>Open <ExternalLink size={12} /></a>}
    {/* A careers inbox is not a person to ask for a referral. */}
    {contact.name && <div className="dossier-contact-ask"><ReferralAsk jobId={jobId} recipientName={contact.name} recipientTitle={contact.title} /></div>}
  </li>
}
