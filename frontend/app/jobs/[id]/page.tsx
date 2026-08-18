'use client';

import { useState, useEffect, use } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import {
  formatSalaryFull,
  formatDate,
  getSeniorityLabel,
  getEmploymentTypeLabel,
} from '@/lib/utils';
import {
  Buildings,
  MapPin,
  CurrencyDollar,
  Calendar,
  ArrowSquareOut,
  Briefcase,
  WarningCircle,
  ArrowLeft,
  Globe,
  Clock,
  Tag,
} from '@phosphor-icons/react';
import Link from 'next/link';

function sanitizeText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

export default function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadJob() {
      setLoading(true);
      setError(null);

      try {
        const response = await api.search.detail(id, controller.signal);
        setJob(response.job);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : 'An unexpected error occurred';
        setError(message);
      } finally {
        setLoading(false);
      }
    }

    loadJob();

    return () => {
      controller.abort();
    };
  }, [id]);

  if (loading) {
    return (
      <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '4rem', textAlign: 'center', color: 'var(--text-subtle)' }}>
        Loading job details...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem' }}>
        <Link
          href="/search"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.4rem',
            color: 'var(--text-muted)',
            textDecoration: 'none',
            fontSize: '0.875rem',
            marginBottom: '1.5rem',
          }}
        >
          <ArrowLeft size={16} />
          <span>Back to search</span>
        </Link>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          <WarningCircle size={16} style={{ flexShrink: 0 }} />
          <div>
            <p style={{ fontWeight: 600, marginBottom: '0.2rem' }}>Failed to load job</p>
            <p style={{ color: 'var(--error)', fontSize: '0.8125rem' }}>{error}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem' }}>
        <Link
          href="/search"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.4rem',
            color: 'var(--text-muted)',
            textDecoration: 'none',
            fontSize: '0.875rem',
            marginBottom: '1.5rem',
          }}
        >
          <ArrowLeft size={16} />
          <span>Back to search</span>
        </Link>
        <div style={{ textAlign: 'center', padding: '4rem 2rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)' }}>
          <Briefcase size={40} style={{ margin: '0 auto 1rem auto', color: 'var(--text-subtle)' }} />
          <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.5rem' }}>Job not found</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>This job may have been removed or is no longer available.</p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Back link */}
      <div>
        <Link
          href="/search"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.4rem',
            color: 'var(--text-muted)',
            textDecoration: 'none',
            fontSize: '0.875rem',
          }}
        >
          <ArrowLeft size={16} />
          <span>Back to search</span>
        </Link>
      </div>

      {/* Job Header */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem', marginBottom: '1.25rem' }}>
          <div style={{ flex: 1, minWidth: '240px' }}>
            <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)', marginBottom: '0.35rem', lineHeight: 1.25 }}>
              {job.title}
            </h1>
            {job.company_name && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--text-muted)', fontSize: '1rem' }}>
                <Buildings size={18} />
                <span style={{ fontWeight: 500 }}>{job.company_name}</span>
              </div>
            )}
          </div>
          {job.source_url && (
            <a
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.4rem',
                padding: '0.5rem 1.25rem',
                background: 'var(--accent)',
                color: '#fff',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.875rem',
                fontWeight: 500,
                textDecoration: 'none',
                transition: 'background 150ms ease',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-hover)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--accent)')}
            >
              <span>Apply now</span>
              <ArrowSquareOut size={15} />
            </a>
          )}
        </div>

        {/* Meta Grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.85rem', color: 'var(--text-muted)', fontSize: '0.875rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <MapPin size={16} />
            <span>{job.is_remote ? 'Remote' : job.location_raw || job.city || 'Location not specified'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <CurrencyDollar size={16} />
            <span>{formatSalaryFull(job.salary_min, job.salary_max, job.salary_currency)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Briefcase size={16} />
            <span>{getSeniorityLabel(job.seniority)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Calendar size={16} />
            <span>Posted {formatDate(job.posted_at)}</span>
          </div>
        </div>

        {/* Tags */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '1.25rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border)' }}>
          {job.seniority && (
            <span style={{ padding: '0.2rem 0.6rem', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-full)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              {getSeniorityLabel(job.seniority)}
            </span>
          )}
          {job.employment_type && (
            <span style={{ padding: '0.2rem 0.6rem', background: 'var(--bg-subtle)', borderRadius: 'var(--radius-full)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              {getEmploymentTypeLabel(job.employment_type)}
            </span>
          )}
          <span style={{ padding: '0.2rem 0.6rem', background: 'var(--accent-subtle)', borderRadius: 'var(--radius-full)', fontSize: '0.75rem', color: 'var(--accent)', fontWeight: 500 }}>
            {job.source}
          </span>
        </div>
      </div>

      {/* Skills */}
      {job.skills && job.skills.length > 0 && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Tag size={18} />
            <span>Required skills</span>
          </h2>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {job.skills.map((skill) => (
              <span
                key={skill}
                style={{
                  padding: '0.25rem 0.65rem',
                  background: 'var(--bg-subtle)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '0.8125rem',
                  color: 'var(--text)',
                }}
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Job Description */}
      {job.description_clean && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <Globe size={18} />
            <span>Job description</span>
          </h2>
          <div style={{ color: 'var(--text)', lineHeight: 1.7, fontSize: '0.9375rem', whiteSpace: 'pre-wrap' }}>
            {job.description_clean.split('\n').map((line, idx) => {
              if (line.trim() === '') return <br key={`desc-empty-${idx}`} />;
              return <p key={`desc-${line.slice(0, 40)}-${idx}`} style={{ marginBottom: '0.5rem' }}>{sanitizeText(line)}</p>;
            })}
          </div>
        </div>
      )}

      {/* Additional Info */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Clock size={18} />
          <span>Additional information</span>
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', fontSize: '0.875rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: '0.5rem', borderBottom: '1px solid var(--border)' }}>
            <span style={{ color: 'var(--text-muted)' }}>Source</span>
            <span style={{ fontWeight: 500, color: 'var(--text)' }}>{job.source}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: '0.5rem', borderBottom: '1px solid var(--border)' }}>
            <span style={{ color: 'var(--text-muted)' }}>Job ID</span>
            <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono, monospace)', fontSize: '0.8125rem' }}>{job.id}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: 'var(--text-muted)' }}>Last updated</span>
            <span style={{ color: 'var(--text)' }}>{formatDate(job.created_at)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
