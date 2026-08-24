'use client'

import { FormEvent, useEffect, useRef, useState } from 'react'
import { Bookmark, ExternalLink, MapPin, RefreshCw, Search, SlidersHorizontal } from 'lucide-react'
import AppShell from '@/components/AppShell'
import { api, Job, signedIn } from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'
import { SuggestionProfile, pickSuggestions } from '@/lib/search-suggestions'
import { EXPERIENCE_BANDS, INDIAN_CITIES, bandForYears, matchCity } from '@/lib/filters'

type FilterProfile = SuggestionProfile & { years_experience?: unknown; is_remote_preferred?: unknown }

export default function SearchPage() {
  const [query, setQuery] = usePersistentState('search.query', '')
  const [location, setLocation, locationReady] = usePersistentState('search.location', '')
  const [experience, setExperience, experienceReady] = usePersistentState('search.experience', '')
  const [remote, setRemote, remoteReady] = usePersistentState('search.remote', false)
  const [jobs, setJobs, jobsReady] = usePersistentState<Job[]>('search.jobs', [])
  const [loading, setLoading] = useState(false); const [error, setError] = useState('')
  const [saved, setSaved] = usePersistentState<string[]>('search.saved', [])
  const [profile, setProfile] = useState<SuggestionProfile | null>(null); const [tips, setTips] = useState<string[]>([])

  // Profile defaults are applied once, and only to filters the user has not set
  // themselves — a stored choice always wins over the profile.
  const filtersReady = locationReady && experienceReady && remoteReady
  const prefilled = useRef(false)

  // Suggestions are randomised, so they are generated after mount (never during render)
  // and rotated on every search so the panel keeps offering new angles.
  useEffect(() => { setTips(current => pickSuggestions(null, current)) }, [])
  useEffect(() => {
    if (!signedIn()) return
    api.profile.get().then(result => {
      if (!result.profile) return
      setProfile(result.profile)
      setTips(current => pickSuggestions(result.profile, current))
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!profile || !filtersReady || prefilled.current) return
    prefilled.current = true
    const source = profile as FilterProfile
    const targets = Array.isArray(source.target_locations) ? source.target_locations : []
    const city = matchCity(typeof targets[0] === 'string' ? targets[0] as string : '')
    const years = typeof source.years_experience === 'number' ? source.years_experience : null
    setLocation(current => current || city)
    setExperience(current => current || bandForYears(years))
    setRemote(current => current || Boolean(source.is_remote_preferred))
  }, [profile, filtersReady, setLocation, setExperience, setRemote])

  function shuffleTips() { setTips(current => pickSuggestions(profile, current)) }
  function clearFilters() { setLocation(''); setExperience(''); setRemote(false) }

  const hasFilters = Boolean(location || experience || remote)

  async function find(e: FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true); setError('')
    try {
      // Filters need exact column matching, so they run against the relational
      // index; an unfiltered query goes to semantic search instead.
      if (hasFilters) {
        const response = await api.search.structured(query, { location, remote, experience })
        setJobs(response.jobs || [])
      } else {
        const response = await api.search.semantic(query)
        setJobs(response.results || [])
      }
      shuffleTips()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search could not be completed.')
    } finally {
      setLoading(false)
    }
  }

  async function save(job: Job) {
    if (!signedIn()) { window.location.assign('/login?next=/search'); return }
    try { await api.applications.create(job.id); setSaved(items => [...items, job.id]) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not save role.') }
  }

  return <AppShell>
    <section className="page-heading"><div><p className="eyebrow">OPPORTUNITY EXPLORER</p><h1>Find roles that fit your direction.</h1><p>Every result is a role hiring in India—use natural language, then narrow by city and experience.</p></div></section>
    <form className="search-form" onSubmit={find}>
      <Search size={20}/>
      <input value={query} onChange={e => setQuery(e.target.value)} placeholder="e.g. Senior product design roles in fintech" />
      <button className="primary-button" disabled={loading}>{loading ? 'Searching…' : 'Search roles'}</button>
    </form>
    <div className="filter-bar">
      <span className="filter-bar-label"><SlidersHorizontal size={13}/> Filters</span>
      <label>Location
        <input list="india-cities" value={location} onChange={e => setLocation(e.target.value)} placeholder="All India" />
        <datalist id="india-cities">{INDIAN_CITIES.map(city => <option key={city} value={city} />)}</datalist>
      </label>
      <label>Experience
        <select value={experience} onChange={e => setExperience(e.target.value)}>
          <option value="">Any experience</option>
          {EXPERIENCE_BANDS.map(band => <option key={band.key} value={band.key}>{band.label}</option>)}
        </select>
      </label>
      <label className="check-label"><input type="checkbox" checked={remote} onChange={e => setRemote(e.target.checked)} /> Remote only</label>
      {hasFilters && <button type="button" className="text-button" onClick={clearFilters}>Clear filters</button>}
    </div>
    {error && <p className="form-error">{error}</p>}
    <div className="results-layout">
      <aside className="search-tips">
        <div className="tips-head"><p className="eyebrow">SEARCH BETTER</p><button type="button" className="icon-refresh" title="Show different ideas" onClick={shuffleTips}><RefreshCw size={13}/></button></div>
        <strong>Try asking for:</strong>
        {tips.map(tip => <button type="button" key={tip} onClick={() => setQuery(tip)}>{tip}</button>)}
      </aside>
      <section className="job-results">
        {loading && <div className="loading-state">Scanning the Indian job market…</div>}
        {!loading && jobsReady && !jobs.length && <div className="empty-state"><Search size={25}/><h2>Start with a conversation.</h2><p>Describe your ideal role, skills, or working style to see relevant opportunities across India.</p></div>}
        {jobs.map(job => <article className="job-card" key={job.id}>
          <div className="job-card-top">
            <div className="company-logo violet">{(job.company_name || job.company || '?').slice(0, 1)}</div>
            <div><h2>{job.title}</h2><p>{job.company_name || job.company || 'Company not listed'}</p></div>
            {job.match_score != null && <span className="match-pill">{Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))}% match</span>}
          </div>
          <div className="job-meta">
            <span><MapPin size={14}/>{job.location_raw || (job.is_remote ? 'Remote' : 'Location not listed')}</span>
            {job.is_remote && <span>Remote-friendly</span>}
            {job.salary_raw && <span>{job.salary_raw}</span>}
          </div>
          <div className="chips">{(job.skills || []).slice(0, 5).map(skill => <span key={skill}>{skill}</span>)}</div>
          <div className="job-actions">
            <button className="outline-button" onClick={() => save(job)} disabled={saved.includes(job.id)}><Bookmark size={14}/>{saved.includes(job.id) ? 'Saved' : 'Save to tracker'}</button>
            {job.source_url && <a className="text-button" href={job.source_url} target="_blank">View original <ExternalLink size={14}/></a>}
          </div>
        </article>)}
      </section>
    </div>
  </AppShell>
}
