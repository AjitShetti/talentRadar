'use client';

import { useState, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Loader2, Lock, Building2, Search, AlertCircle, MapPin, Users, Globe, Briefcase, Layers, ArrowRight } from 'lucide-react';
import { companyApi } from '@/lib/company-api';
import type { CompanyIntelResponse } from '@/lib/types';
import { useAuthModal } from '@/components/AuthModalProvider';

export default function CompanyIntelPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [company, setCompany] = useState<CompanyIntelResponse | null>(null);

  if (status === 'unauthenticated') {
    return (
      <div className="panel" style={{ maxWidth: 460, margin: '5rem auto', textAlign: 'center', padding: '3rem 2rem' }}>
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'var(--color-surface-elevated)',
            border: '1px solid var(--color-border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 1.5rem auto',
            color: 'var(--color-fg-muted)',
          }}
        >
          <Lock size={22} />
        </div>
        <h2 style={{ fontSize: '1.4rem', marginBottom: '0.75rem' }}>Sign In for Company Intelligence</h2>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.75rem', lineHeight: 1.6 }}>
          Research company engineering stacks, salary benchmarks, and open opportunities.
        </p>
        <button onClick={openLogin} className="btn btn-primary" style={{ width: '100%' }}>
          <span>Sign In / Create Account</span>
          <ArrowRight size={15} />
        </button>
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
      if (res.id) {
        const full = await companyApi.getById(session.accessToken, res.id);
        setCompany(full);
      } else {
        setError('No company found matching your query.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <Building2 size={13} />
          <span>ORGANIZATION INTELLIGENCE</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Company Tech & Salary Radar
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem', maxWidth: '65ch' }}>
          Research architecture stacks, compensation benchmarks, and hiring patterns before applying.
        </p>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.75rem' }}>
        <div className="input-wrap" style={{ flex: 1 }}>
          <Search size={17} className="input-icon" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search company by name (e.g. Stripe, Vercel, Datadog)..."
            className="input-with-icon"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !searchQuery.trim()}
          className="btn btn-primary"
        >
          {loading ? <Loader2 size={16} className="status-dot-pulse" /> : <Search size={15} />}
          <span>Search</span>
        </button>
      </form>

      {error && (
        <div className="auth-error">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {company && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          {/* Company header */}
          <div className="panel panel-elevated">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 'var(--border-radius-sm)',
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Building2 size={24} className="text-accent" />
                </div>
                <div>
                  <h2 style={{ fontSize: '1.4rem', fontWeight: 700, margin: 0 }}>{company.company.name}</h2>
                  <div style={{ display: 'flex', gap: '1rem', color: 'var(--color-fg-muted)', fontSize: '0.8125rem', marginTop: '0.35rem', flexWrap: 'wrap' }}>
                    {company.company.industry && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <Layers size={13} /> {company.company.industry}
                      </span>
                    )}
                    {(company.company.hq_city || company.company.hq_country) && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <MapPin size={13} /> {[company.company.hq_city, company.company.hq_country].filter(Boolean).join(', ')}
                      </span>
                    )}
                    {company.company.employee_count_range && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <Users size={13} /> {company.company.employee_count_range}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {company.company.website_url && (
                <a
                  href={company.company.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-secondary btn-sm"
                >
                  <Globe size={13} />
                  <span>Visit Website</span>
                </a>
              )}
            </div>
          </div>

          {/* Intelligence Grid */}
          {company.profile && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1.25rem' }}>
              {company.profile.tech_stack && company.profile.tech_stack.length > 0 && (
                <div className="panel">
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--color-accent)', marginBottom: '0.85rem' }}>
                    Identified Tech Stack
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                    {company.profile.tech_stack.map((tech) => (
                      <span
                        key={tech}
                        className="badge badge-accent"
                        style={{ fontFamily: 'var(--font-mono)' }}
                      >
                        {tech}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {company.profile.culture_summary && (
                <div className="panel">
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--color-accent)', marginBottom: '0.85rem' }}>
                    Culture & Engineering Values
                  </div>
                  <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', lineHeight: 1.6 }}>
                    {company.profile.culture_summary}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Open jobs */}
          {company.open_jobs.length > 0 && (
            <div className="panel">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
                <Briefcase size={16} className="text-accent" />
                <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>
                  Open Positions ({company.open_jobs.length})
                </h3>
              </div>
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {company.open_jobs.map((job) => (
                  <div
                    key={job.id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '0.85rem 1rem',
                      background: 'var(--color-surface-elevated)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 'var(--border-radius-sm)',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '0.9375rem', color: 'var(--color-fg)' }}>{job.title}</div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)', marginTop: '0.2rem' }}>
                        {[job.location_raw, job.is_remote ? 'Remote' : null].filter(Boolean).join(' · ')}
                        {job.seniority && ` · ${job.seniority}`}
                      </div>
                    </div>
                    {job.skills && job.skills.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                        {job.skills.slice(0, 3).map((s) => (
                          <span key={s} className="badge" style={{ fontSize: '0.6875rem', fontFamily: 'var(--font-mono)' }}>
                            {s}
                          </span>
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
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 2rem', color: 'var(--color-fg-subtle)' }}>
          <Building2 size={48} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
          <h3 style={{ color: 'var(--color-fg)', marginBottom: '0.5rem' }}>Search Company Intelligence</h3>
          <p style={{ fontSize: '0.875rem' }}>Search by company name to inspect their technology stack, engineering values, and active openings.</p>
        </div>
      )}
    </div>
  );
}
