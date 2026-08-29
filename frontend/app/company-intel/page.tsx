'use client'

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Building2, Calendar, ExternalLink, Github, Globe, Linkedin, Loader2, Mail,
  Plus, Search, Sparkles, Star, Trash2, Users, X,
} from 'lucide-react'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import {
  api, CompanyCard, CompanyContact, CompanyDetail, CompanyFacets, ContactCandidate, NewContact,
} from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'

/** Initials shown when a company has no logo on file. */
function initials(name: string) {
  const words = name.replace(/[^\w\s]/g, ' ').split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] || '') + (words[1]?.[0] || '')).toUpperCase() || name.slice(0, 2).toUpperCase()
}

function Logo({ company }: { company: { name: string; logo_url?: string | null } }) {
  return <span className="ci-logo">{company.logo_url
    ? <img src={company.logo_url} alt="" />
    : initials(company.name)}</span>
}

function TierBadge({ tier, label }: { tier?: string | null; label?: string | null }) {
  if (!tier) return null
  return <span className="ci-tier" data-tier={tier}>{label || tier.replace('_', ' ')}</span>
}

const CONTACT_KINDS = [
  ['recruiter', 'Recruiter'], ['referral', 'Referral'], ['careers_inbox', 'Careers inbox'],
  ['careers_page', 'Careers page'], ['other', 'Other'],
] as const

export default function CompanyIntelPage() {
  const [city, setCity] = usePersistentState('companyIntel.city', 'Bengaluru')
  const [tier, setTier] = usePersistentState('companyIntel.tier', '')
  const [industry, setIndustry] = usePersistentState('companyIntel.industry', '')
  const [query, setQuery] = usePersistentState('companyIntel.query', '')
  const [openRolesOnly, setOpenRolesOnly] = usePersistentState('companyIntel.openRolesOnly', false)

  const [companies, setCompanies] = useState<CompanyCard[]>([])
  const [facets, setFacets] = useState<CompanyFacets | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // The search box types faster than the API answers, so debounce the term the
  // request actually uses rather than firing on every keystroke.
  const [debouncedQuery, setDebouncedQuery] = useState(query)
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 250)
    return () => clearTimeout(timer)
  }, [query])

  useEffect(() => { api.company.facets(city).then(setFacets).catch(() => setFacets(null)) }, [city])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.company
      .directory({ city, tier: tier || undefined, industry: industry || undefined, q: debouncedQuery || undefined, hasOpenRoles: openRolesOnly })
      .then(result => { if (!cancelled) { setCompanies(result.companies); setError('') } })
      .catch(err => { if (!cancelled) { setCompanies([]); setError(err instanceof Error ? err.message : 'Could not load the company directory.') } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [city, tier, industry, debouncedQuery, openRolesOnly])

  const cities = facets?.cities?.length ? facets.cities : ['Bengaluru']

  return <RequireAuth><AppShell>
    <section className="page-heading">
      <p className="eyebrow">COMPANY INTELLIGENCE</p>
      <h1>Every tech employer hiring in {city}.</h1>
      <p>Big Tech, global capability centres, unicorns and startups — what they build, the stack they build it on, their open source, and how to reach their talent team.</p>
    </section>

    <div className="ci-toolbar">
      <div className="ci-searchbar">
        <Search size={19} />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by name, industry or what they do — e.g. payments, Rust, Razorpay" />
        <select value={city} onChange={e => { setCity(e.target.value); setIndustry('') }} aria-label="City">
          {cities.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={industry} onChange={e => setIndustry(e.target.value)} aria-label="Industry">
          <option value="">All industries</option>
          {(facets?.industries || []).map(i => <option key={i.value} value={i.value}>{i.value} ({i.count})</option>)}
        </select>
        <span className="ci-count">{loading ? '…' : `${companies.length} ${companies.length === 1 ? 'company' : 'companies'}`}</span>
      </div>

      <div className="ci-chips">
        <button className={`ci-chip ${tier ? '' : 'active'}`} onClick={() => setTier('')}>All</button>
        {(facets?.tiers || []).map(t =>
          <button key={t.value} className={`ci-chip ${tier === t.value ? 'active' : ''}`} onClick={() => setTier(tier === t.value ? '' : t.value)}>
            {t.label}<b>{t.count}</b>
          </button>)}
        <button className={`ci-chip ci-chip-toggle ${openRolesOnly ? 'active' : ''}`} onClick={() => setOpenRolesOnly(!openRolesOnly)}>
          <Sparkles size={12} />Open roles only
        </button>
        {industry && <button className="ci-chip active" onClick={() => setIndustry('')}>{industry}<X size={11} /></button>}
      </div>
    </div>

    {error && <p className="form-error">{error}</p>}

    {loading && companies.length === 0
      ? <div className="loading-state"><Loader2 size={18} className="spin" />Loading the directory…</div>
      : companies.length === 0
        ? <div className="empty-state">
            <Building2 size={24} />
            <h2>No companies match those filters.</h2>
            <p>Clear the filters, or seed the catalogue with <code>python scripts/seed_companies.py</code> if this is a fresh database.</p>
          </div>
        : <div className="ci-grid">
            {companies.map(company =>
              <button
                key={company.id}
                className={`ci-card ${selectedId === company.id ? 'selected' : ''}`}
                onClick={() => setSelectedId(company.id)}
              >
                <div className="ci-card-head">
                  <Logo company={company} />
                  <div className="ci-card-title">
                    <strong>{company.name}</strong>
                    <span>{company.industry || company.hq_city || '—'}</span>
                  </div>
                  <TierBadge tier={company.tier} label={company.tier_label} />
                </div>
                <p className="ci-card-desc">{company.description}</p>
                <div className="ci-card-foot">
                  <div className="ci-mini-chips">
                    {company.tech_stack.slice(0, 3).map(tech => <span key={tech}>{tech}</span>)}
                  </div>
                  {company.open_roles > 0 && <span className="ci-roles">{company.open_roles} open</span>}
                </div>
              </button>)}
          </div>}

    {selectedId && <CompanyDrawer
      companyId={selectedId}
      onClose={() => setSelectedId(null)}
      onPickIndustry={value => { setIndustry(value); setSelectedId(null) }}
    />}
  </AppShell></RequireAuth>
}

function CompanyDrawer({ companyId, onClose, onPickIndustry }: {
  companyId: string
  onClose: () => void
  onPickIndustry: (industry: string) => void
}) {
  const [detail, setDetail] = useState<CompanyDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setDetail(null); setError('')
    api.company.detail(companyId)
      .then(result => { if (!cancelled) setDetail(result) })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load this company.') })
    return () => { cancelled = true }
  }, [companyId])

  // Escape closes the drawer, and the body must not scroll behind it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = previous }
  }, [onClose])

  const setContacts = useCallback((contacts: CompanyContact[]) => {
    setDetail(current => (current ? { ...current, contacts } : current))
  }, [])

  return <>
    <button className="ci-backdrop" onClick={onClose} aria-label="Close company details" />
    <aside className="ci-drawer" role="dialog" aria-modal="true" aria-label={detail?.name || 'Company details'}>
      <header className="ci-drawer-head">
        {detail ? <Logo company={detail} /> : <span className="ci-logo"><Building2 size={18} /></span>}
        <div className="ci-drawer-title">
          <h2>{detail?.name || 'Loading…'}</h2>
          <div className="ci-sub">
            <TierBadge tier={detail?.tier} label={detail?.tier_label} />
            {detail?.industry && <button className="purple-link" onClick={() => onPickIndustry(detail.industry!)} style={{ background: 'transparent', fontSize: 11, padding: 0 }}>{detail.industry}</button>}
          </div>
        </div>
        <button className="ci-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </header>

      <div className="ci-drawer-body">
        {error && <p className="form-error" style={{ marginTop: 18 }}>{error}</p>}
        {!detail && !error && <div className="loading-state"><Loader2 size={18} className="spin" />Loading…</div>}

        {detail && <>
          <Facts detail={detail} />
          <LinkRow detail={detail} />

          <section className="ci-section">
            <h3><Building2 size={13} />What they do</h3>
            <p>{detail.description || 'No description on file for this company yet.'}</p>
            {detail.culture_summary && <p style={{ marginTop: 12 }}>{detail.culture_summary}</p>}
          </section>

          <TechSection detail={detail} />
          <GithubSection detail={detail} />
          <ContactsSection detail={detail} onContactsChange={setContacts} />
          <RolesSection detail={detail} />
        </>}
      </div>
    </aside>
  </>
}

function Facts({ detail }: { detail: CompanyDetail }) {
  const offices = detail.office_cities.length ? detail.office_cities.join(', ') : null
  return <div className="ci-facts">
    <div className="ci-fact"><span>Headquarters</span><strong>{[detail.hq_city, detail.hq_country].filter(Boolean).join(', ') || '—'}</strong></div>
    <div className="ci-fact"><span>Headcount</span><strong>{detail.employee_count_range || '—'}</strong></div>
    <div className="ci-fact"><span>Founded</span><strong>{detail.founded_year || '—'}</strong></div>
    <div className="ci-fact"><span>India offices</span><strong>{offices || '—'}</strong></div>
  </div>
}

function LinkRow({ detail }: { detail: CompanyDetail }) {
  const links: Array<[string, string | null | undefined, JSX.Element]> = [
    ['Website', detail.website_url, <Globe size={13} key="w" />],
    ['Careers', detail.careers_url, <ExternalLink size={13} key="c" />],
    ['LinkedIn', detail.linkedin_url, <Linkedin size={13} key="l" />],
    ['GitHub', detail.github?.html_url, <Github size={13} key="g" />],
  ]
  const shown = links.filter(([, href]) => href)
  if (!shown.length) return null
  return <div className="ci-links">
    {shown.map(([label, href, icon]) =>
      <a key={label} className="ci-link" href={href!} target="_blank" rel="noreferrer noopener">{icon}{label}</a>)}
  </div>
}

function TechSection({ detail }: { detail: CompanyDetail }) {
  const { tech_stack_curated: curated, tech_stack_from_postings: live } = detail
  if (!curated.length && !live.length) return null
  return <section className="ci-section">
    <h3><Sparkles size={13} />Technology they work on</h3>
    {curated.length > 0 && <>
      <p className="ci-stack-label">Known stack</p>
      <div className="ci-stack">{curated.map(tech => <span key={tech}>{tech}</span>)}</div>
    </>}
    {live.length > 0 && <>
      <p className="ci-stack-label">Named in their current openings</p>
      <div className="ci-stack">{live.map(tech => <span key={tech} className="live">{tech}</span>)}</div>
    </>}
  </section>
}

function GithubSection({ detail }: { detail: CompanyDetail }) {
  const gh = detail.github
  return <section className="ci-section">
    <h3><Github size={13} />Open source</h3>
    {!gh
      ? <p className="ci-empty-small">
          {detail.github_org
            ? `No public GitHub organisation resolved for “${detail.github_org}”. It may be private, renamed, or the lookup hit GitHub's rate limit.`
            : 'No public GitHub organisation on file for this company.'}
        </p>
      : <div className="ci-gh">
          <a className="ci-gh-head" href={gh.html_url} target="_blank" rel="noreferrer noopener">
            {gh.avatar_url && <img src={gh.avatar_url} alt="" />}
            <div>
              <strong>{gh.name}</strong>
              <span>@{gh.org} · {gh.public_repos} repos · {gh.followers.toLocaleString()} followers</span>
            </div>
            <ExternalLink size={14} color="var(--faint)" />
          </a>
          {gh.top_repos.map(repo =>
            <a key={repo.full_name} className="ci-repo" href={repo.html_url} target="_blank" rel="noreferrer noopener">
              <span className="ci-repo-top">
                <strong>{repo.name}</strong>
                <span className="ci-repo-meta">
                  {repo.language && <span>{repo.language}</span>}
                  <span><Star size={9} style={{ verticalAlign: -1 }} /> {repo.stars.toLocaleString()}</span>
                </span>
              </span>
              {repo.description && <p>{repo.description}</p>}
            </a>)}
        </div>}
  </section>
}

function ContactsSection({ detail, onContactsChange }: {
  detail: CompanyDetail
  onContactsChange: (contacts: CompanyContact[]) => void
}) {
  const [candidates, setCandidates] = useState<ContactCandidate[] | null>(null)
  const [lookupMessage, setLookupMessage] = useState('')
  const [searching, setSearching] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')

  useEffect(() => { setCandidates(null); setLookupMessage(''); setShowForm(false); setFormError('') }, [detail.id])

  const add = async (payload: NewContact) => {
    setBusy(true); setFormError('')
    try {
      const saved = await api.company.addContact(detail.id, payload)
      onContactsChange([...detail.contacts, saved])
      setShowForm(false)
      setCandidates(current => current?.filter(c => c.email !== payload.email) ?? null)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not save that contact.')
    } finally { setBusy(false) }
  }

  const remove = async (contactId: string) => {
    try {
      await api.company.removeContact(detail.id, contactId)
      onContactsChange(detail.contacts.filter(c => c.id !== contactId))
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not remove that contact.')
    }
  }

  const runLookup = async () => {
    setSearching(true); setLookupMessage(''); setFormError('')
    try {
      const result = await api.company.discoverContacts(detail.id)
      setCandidates(result.candidates)
      setLookupMessage(result.message || (result.available ? '' : 'Web lookup is not configured.'))
    } catch (err) {
      setLookupMessage(err instanceof Error ? err.message : 'The lookup failed.')
    } finally { setSearching(false) }
  }

  return <section className="ci-section">
    <h3><Mail size={13} />Talent contacts</h3>

    <p className="ci-note">
      <strong>Nothing here is guessed.</strong> Contacts are either a channel the company
      publishes itself, or something found on a real page — each shows where it came from.
      Named recruiters and their addresses are never invented from a naming pattern, so use
      the lookup or add one you already have.
    </p>

    {detail.contacts.length === 0 && <p className="ci-empty-small">No contacts stored yet for {detail.name}.</p>}

    {detail.contacts.map(contact =>
      <div key={contact.id} className="ci-contact">
        <span className="ci-contact-icon">{contact.email ? <Mail size={14} /> : <Globe size={14} />}</span>
        <div className="ci-contact-body">
          <strong>
            {contact.name || contact.title || contact.kind.replace('_', ' ')}
            {contact.is_curated && <span className="ci-badge curated">published</span>}
            <span className={`ci-badge ${contact.verified ? 'verified' : 'unverified'}`}>{contact.verified ? 'verified' : 'unverified'}</span>
          </strong>
          {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}
          {contact.linkedin_url && <a href={contact.linkedin_url} target="_blank" rel="noreferrer noopener">{contact.linkedin_url}</a>}
          {!contact.email && !contact.linkedin_url && contact.source_url &&
            <a href={contact.source_url} target="_blank" rel="noreferrer noopener">{contact.source_url}</a>}
          {contact.notes && <small>{contact.notes}</small>}
          {contact.source_url && (contact.email || contact.linkedin_url) &&
            <small>Source: <a href={contact.source_url} target="_blank" rel="noreferrer noopener">{contact.source_url}</a></small>}
        </div>
        {!contact.is_curated &&
          <button className="ci-close" onClick={() => remove(contact.id)} aria-label="Remove contact"><Trash2 size={14} /></button>}
      </div>)}

    {formError && <p className="form-error" style={{ marginTop: 12 }}>{formError}</p>}

    <div className="ci-contact-actions">
      <button className="outline-button" onClick={runLookup} disabled={searching}>
        {searching ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
        {searching ? 'Searching the web…' : 'Find contacts on the web'}
      </button>
      <button className="outline-button" onClick={() => setShowForm(!showForm)}>
        <Plus size={13} />{showForm ? 'Cancel' : 'Add a contact'}
      </button>
    </div>

    {lookupMessage && <p className="ci-empty-small" style={{ marginTop: 12 }}>{lookupMessage}</p>}

    {candidates?.map(candidate =>
      <div key={candidate.email || candidate.source_url} className="ci-candidate" style={{ marginTop: 10 }}>
        <span className="ci-contact-icon"><Mail size={14} /></span>
        <div>
          <a href={`mailto:${candidate.email}`}>{candidate.email}</a>
          <small>
            {candidate.kind === 'careers_inbox' ? 'Shared careers inbox' : 'Address on the company domain'} · found on{' '}
            <a href={candidate.source_url || '#'} target="_blank" rel="noreferrer noopener">{candidate.source_title || candidate.source_url}</a>
          </small>
        </div>
        <button disabled={busy} onClick={() => add({ kind: candidate.kind, email: candidate.email, source_url: candidate.source_url, verified: false })}>Save</button>
      </div>)}

    {showForm && <ContactForm busy={busy} onSubmit={add} />}
  </section>
}

function ContactForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: NewContact) => void }) {
  const [form, setForm] = useState<NewContact>({ kind: 'recruiter', name: '', title: '', email: '', linkedin_url: '', notes: '' })
  const set = (key: keyof NewContact) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value })
  const canSubmit = useMemo(() => Boolean(form.name || form.email || form.linkedin_url), [form])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit({ ...form, verified: true })
  }

  return <form className="ci-contact-form" onSubmit={submit}>
    <input placeholder="Name (optional)" value={form.name || ''} onChange={set('name')} />
    <input placeholder="Title, e.g. Talent Partner" value={form.title || ''} onChange={set('title')} />
    <input className="ci-span2" type="email" placeholder="Email" value={form.email || ''} onChange={set('email')} />
    <input className="ci-span2" placeholder="LinkedIn URL" value={form.linkedin_url || ''} onChange={set('linkedin_url')} />
    <select value={form.kind} onChange={set('kind')}>
      {CONTACT_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
    <input placeholder="Where did you get this?" value={form.source_url || ''} onChange={set('source_url')} />
    <textarea className="ci-span2" rows={2} placeholder="Notes — how you met, what they cover" value={form.notes || ''} onChange={set('notes')} />
    <div className="ci-form-actions">
      <button className="primary-button" disabled={busy || !canSubmit}>{busy ? 'Saving…' : 'Save contact'}</button>
      <span className="ci-empty-small">Saved only to your account.</span>
    </div>
  </form>
}

function RolesSection({ detail }: { detail: CompanyDetail }) {
  return <section className="ci-section">
    <h3><Users size={13} />Open roles on TalentRadar</h3>
    {detail.jobs.length === 0
      ? <p className="ci-empty-small">
          No live postings ingested for {detail.name} yet. Their careers page is the fastest route
          {detail.careers_url ? <> — <a className="purple-link" href={detail.careers_url} target="_blank" rel="noreferrer noopener">open it</a>.</> : '.'}
        </p>
      : detail.jobs.map(job =>
          <a key={job.id} className="ci-job" href={job.source_url || '#'} target="_blank" rel="noreferrer noopener">
            <strong>{job.title}</strong>
            <span>
              {[job.location, job.is_remote ? 'Remote' : null, job.seniority, job.salary_raw]
                .filter(Boolean).join(' · ') || 'Details on the posting'}
            </span>
          </a>)}
    {detail.jobs.length > 0 && detail.careers_url &&
      <div className="ci-links"><a className="ci-link" href={detail.careers_url} target="_blank" rel="noreferrer noopener"><Calendar size={13} />All roles on their careers page</a></div>}
  </section>
}
