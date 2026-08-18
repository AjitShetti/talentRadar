'use client';

import { useState, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Buildings, Lock, MagnifyingGlass, WarningCircle, MapPin, Users, Globe, Briefcase, Stack, ArrowRight } from '@phosphor-icons/react';
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
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in for company intelligence
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Research company engineering stacks, compensation benchmarks, and open opportunities.
        </p>
        <button
          onClick={openLogin}
          style={{
            padding: '0.625rem 1.25rem',
            background: 'var(--accent)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            fontSize: '0.9375rem',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Sign in
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
    <div style={{ maxWidth: '960px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Company intelligence
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
          Research architecture stacks, culture, and active openings before applying.
        </p>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.75rem' }}>
        <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
          <MagnifyingGlass
            size={18}
            style={{
              position: 'absolute',
              left: '0.875rem',
              color: 'var(--text-muted)',
              pointerEvents: 'none',
            }}
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by company name (e.g. Stripe, Vercel, Datadog)..."
            style={{ paddingLeft: '2.75rem' }}
          />
        </div>
        <button
          type="submit"
          disabled={loading || !searchQuery.trim()}
          style={{
            padding: '0.625rem 1.25rem',
            background: loading || !searchQuery.trim() ? 'var(--border-hover)' : 'var(--accent)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            fontSize: '0.9375rem',
            fontWeight: 500,
            cursor: loading || !searchQuery.trim() ? 'not-allowed' : 'pointer',
            transition: 'background 150ms ease',
            whiteSpace: 'nowrap',
          }}
        >
          {loading ? 'Searching...' : 'Search'}
        </button>
      </form>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          <WarningCircle size={16} style={{ flexShrink: 0 }} />
          {error}
        </div>
      )}

      {company && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {/* Company header */}
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                <div
                  style={{
                    width: '48px',
                    height: '48px',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-subtle)',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--accent)',
                  }}
                >
                  <Buildings size={24} />
                </div>
                <div>
                  <h2 style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text)', margin: 0 }}>{company.company.name}</h2>
                  <div style={{ display: 'flex', gap: '1rem', color: 'var(--text-muted)', fontSize: '0.8125rem', marginTop: '0.35rem', flexWrap: 'wrap' }}>
                    {company.company.industry && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                        <Stack size={13} /> {company.company.industry}
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
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.35rem',
                    padding: '0.4rem 0.85rem',
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.8125rem',
                    color: 'var(--text)',
                    textDecoration: 'none',
                  }}
                >
                  <Globe size={13} />
                  <span>Visit website</span>
                </a>
              )}
            </div>
          </div>

          {/* Intelligence Grid */}
          {company.profile && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1.25rem' }}>
              {company.profile.tech_stack && company.profile.tech_stack.length > 0 && (
                <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', background: 'var(--surface)' }}>
                  <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
                    Identified tech stack
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                    {company.profile.tech_stack.map((tech) => (
                      <span
                        key={tech}
                        style={{
                          padding: '0.2rem 0.6rem',
                          background: 'var(--accent-subtle)',
                          borderRadius: 'var(--radius-sm)',
                          fontSize: '0.75rem',
                          fontWeight: 500,
                          color: 'var(--accent)',
                        }}
                      >
                        {tech}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {company.profile.culture_summary && (
                <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', background: 'var(--surface)' }}>
                  <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
                    Culture and engineering values
                  </div>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', lineHeight: 1.6 }}>
                    {company.profile.culture_summary}
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Open jobs */}
          {company.open_jobs.length > 0 && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', background: 'var(--surface)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
                <Briefcase size={16} style={{ color: 'var(--accent)' }} />
                <h3 style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)' }}>
                  Open positions ({company.open_jobs.length})
                </h3>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {company.open_jobs.map((job) => (
                  <div
                    key={job.id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '0.75rem 1rem',
                      background: 'var(--bg)',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 500, fontSize: '0.875rem', color: 'var(--text)' }}>{job.title}</div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                        {[job.location_raw, job.is_remote ? 'Remote' : null, job.seniority].filter(Boolean).join(', ')}
                      </div>
                    </div>
                    {job.skills && job.skills.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                        {job.skills.slice(0, 3).map((s) => (
                          <span key={s} style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-full)', color: 'var(--text-muted)' }}>
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
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', textAlign: 'center', padding: '4rem 2rem', background: 'var(--surface)', color: 'var(--text-subtle)' }}>
          <Buildings size={40} style={{ margin: '0 auto 1rem auto', opacity: 0.4 }} />
          <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.5rem' }}>Search company intelligence</h3>
          <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', maxWidth: '44ch', margin: '0 auto' }}>
            Look up a company to inspect their technology stack, engineering values, and active openings.
          </p>
        </div>
      )}
    </div>
  );
}
