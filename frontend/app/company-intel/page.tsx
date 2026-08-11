'use client';

import { useState, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Loader2, Lock, Building2, Search, AlertCircle, MapPin, Users, Globe, Briefcase, Layers } from 'lucide-react';
import { companyApi } from '@/lib/company-api';
import type { CompanyIntelResponse } from '@/lib/types';

export default function CompanyIntelPage() {
  const { data: session, status } = useSession();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [company, setCompany] = useState<CompanyIntelResponse | null>(null);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', marginBottom: '1rem' }}>SIGN IN TO CONTINUE</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Research companies before you apply.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  const handleSearch = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken || !searchQuery.trim()) return;
    setLoading(true);
    setError(null);
    setCompany(null);
    try {
      const res = await companyApi.search(session.accessToken, searchQuery.trim());
      // Search returns a lightweight object — fetch full intel by id
      if (res.id) {
        const full = await companyApi.getById(session.accessToken, res.id);
        setCompany(full);
      } else {
        setError('No company found matching your search.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: '2.5rem' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          COMPANY<span style={{ color: 'var(--color-accent)' }}>_</span>INTEL
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.1rem', marginTop: '0.75rem' }}>Research tech stack, salaries, and interview patterns.</p>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.75rem', marginBottom: '2.5rem' }}>
        <div style={{ flex: 1, position: 'relative' }}>
          <Search size={18} style={{ position: 'absolute', left: '1rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--color-fg-muted)' }} />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search company name..."
            style={{ width: '100%', padding: '0.875rem 1rem 0.875rem 2.75rem', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)', fontSize: '0.95rem', outline: 'none' }}
          />
        </div>
        <button type="submit" disabled={loading || !searchQuery.trim()} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
          {loading ? 'SEARCHING...' : 'SEARCH'}
        </button>
      </form>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '1rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5', fontFamily: 'var(--font-display)', fontSize: '0.85rem', marginBottom: '1.5rem' }}>
          <AlertCircle size={18} /> {error}
        </div>
      )}

      {company && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          {/* Company header */}
          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
              {company.company.website_url ? (
                <img src={`https://logo.clearbit.com/${company.company.domain}`} alt="" style={{ width: 48, height: 48, border: '1px solid var(--color-border)' }} onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }} />
              ) : (
                <div style={{ width: 48, height: 48, border: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Building2 size={24} className="text-accent" />
                </div>
              )}
              <div>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', fontWeight: 700 }}>{company.company.name}</h2>
                <div style={{ display: 'flex', gap: '1rem', color: 'var(--color-fg-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
                  {company.company.industry && <span><Layers size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />{company.company.industry}</span>}
                  {(company.company.hq_city || company.company.hq_country) && <span><MapPin size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />{[company.company.hq_city, company.company.hq_country].filter(Boolean).join(', ')}</span>}
                  {company.company.employee_count_range && <span><Users size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />{company.company.employee_count_range}</span>}
                </div>
              </div>
            </div>
            {company.company.website_url && (
              <a href={company.company.website_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)', fontSize: '0.85rem', fontFamily: 'var(--font-display)', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
                <Globe size={14} /> VISIT WEBSITE
              </a>
            )}
          </div>

          {/* Intelligence */}
          {company.profile && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1.5rem' }}>
              {company.profile.tech_stack && company.profile.tech_stack.length > 0 && (
                <div className="panel">
                  <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: 'var(--color-accent)', marginBottom: '1rem' }}>TECH STACK</h3>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                    {company.profile.tech_stack.map(tech => (
                      <span key={tech} style={{ padding: '0.3rem 0.75rem', background: 'rgba(255,69,0,0.08)', border: '1px solid rgba(255,69,0,0.25)', fontSize: '0.8rem', fontFamily: 'var(--font-display)' }}>{tech}</span>
                    ))}
                  </div>
                </div>
              )}
              {company.profile.salary_ranges && (
                <div className="panel">
                  <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: 'var(--color-accent)', marginBottom: '1rem' }}>SALARY RANGES</h3>
                  <pre style={{ fontFamily: 'var(--font-body)', fontSize: '0.85rem', color: 'var(--color-fg-muted)', whiteSpace: 'pre-wrap' }}>{JSON.stringify(company.profile.salary_ranges, null, 2)}</pre>
                </div>
              )}
              {company.profile.culture_summary && (
                <div className="panel" style={{ gridColumn: '1 / -1' }}>
                  <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: 'var(--color-accent)', marginBottom: '1rem' }}>CULTURE</h3>
                  <p style={{ color: 'var(--color-fg-muted)', lineHeight: 1.7 }}>{company.profile.culture_summary}</p>
                </div>
              )}
            </div>
          )}

          {/* Open jobs */}
          {company.open_jobs.length > 0 && (
            <div className="panel">
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: 'var(--color-accent)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <Briefcase size={16} /> OPEN POSITIONS ({company.open_jobs.length})
              </h3>
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {company.open_jobs.map(job => (
                  <div key={job.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.875rem 1rem', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--color-border)' }}>
                    <div>
                      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}>{job.title}</div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--color-fg-muted)', marginTop: '0.25rem' }}>
                        {[job.location_raw, job.is_remote ? 'Remote' : null].filter(Boolean).join(' · ')}
                        {job.seniority && ` · ${job.seniority}`}
                      </div>
                    </div>
                    {job.skills.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', maxWidth: 200, justifyContent: 'flex-end' }}>
                        {job.skills.slice(0, 3).map(s => (
                          <span key={s} style={{ fontSize: '0.7rem', padding: '0.15rem 0.4rem', border: '1px solid var(--color-border)', color: 'var(--color-fg-muted)' }}>{s}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {!company && !loading && !error && (
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 2rem' }}>
          <Building2 size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1rem' }} />
          <h3 style={{ fontFamily: 'var(--font-display)', marginBottom: '0.5rem' }}>SEARCH A COMPANY</h3>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.9rem' }}>Enter a company name to see their tech stack, salaries, and open roles.</p>
        </div>
      )}
    </div>
  );
}
