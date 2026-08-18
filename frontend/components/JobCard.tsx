'use client';

import { Job } from '@/lib/types';
import { formatSalary, formatDate, getSeniorityLabel, getEmploymentTypeLabel } from '@/lib/utils';
import { MapPin, CurrencyDollar, Calendar, ArrowSquareOut, Bookmark, BookmarkSimple } from '@phosphor-icons/react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { applicationsApi } from '@/lib/applications-api';
import { useState } from 'react';

interface JobCardProps {
  job: Job;
  showScore?: boolean;
}

export default function JobCard({ job, showScore = false }: JobCardProps) {
  const { data: session } = useSession();
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const companyName = job.company_name || job.company || 'Unknown Company';

  const handleSave = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!session?.accessToken || saving) return;
    setSaving(true);
    try {
      await applicationsApi.create(session.accessToken as string, { job_id: job.id });
      setSaved(true);
    } catch (err) {
      if (err instanceof Error && err.message.includes('already exists')) setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  const skills = job.skills?.slice(0, 5) || [];
  const score = job.match_score;

  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '1.25rem 1.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.875rem',
        transition: 'border-color 150ms ease, box-shadow 150ms ease',
        position: 'relative',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'var(--border-hover)';
        e.currentTarget.style.boxShadow = 'var(--shadow-sm)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = 'var(--border)';
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      {/* Save button */}
      {session && (
        <button
          onClick={handleSave}
          disabled={saving}
          title={saved ? 'Saved' : 'Save job'}
          style={{
            position: 'absolute',
            top: '1rem',
            right: '1rem',
            background: 'transparent',
            border: 'none',
            cursor: saving ? 'wait' : 'pointer',
            color: saved ? 'var(--accent)' : 'var(--text-subtle)',
            padding: '0.25rem',
            lineHeight: 0,
            transition: 'color 150ms ease',
          }}
        >
          {saved ? <Bookmark size={18} weight="fill" /> : <BookmarkSimple size={18} />}
        </button>
      )}

      {/* Header: company + role */}
      <div style={{ paddingRight: '2rem' }}>
        <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.25rem', fontWeight: 500 }}>
          {companyName}
        </p>
        <h3 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', lineHeight: 1.3 }}>
          {job.title}
        </h3>
      </div>

      {/* Meta row */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'center' }}>
        {(job.location_raw || job.is_remote || job.city) && (
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
            <MapPin size={13} />
            {job.is_remote ? 'Remote' : job.location_raw || job.city}
          </span>
        )}
        {(job.salary_min || job.salary_max) && (
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
            <CurrencyDollar size={13} />
            {formatSalary(job.salary_min, job.salary_max, job.salary_currency)}
          </span>
        )}
        {job.posted_at && (
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8125rem', color: 'var(--text-subtle)' }}>
            <Calendar size={13} />
            {formatDate(job.posted_at)}
          </span>
        )}
        {job.seniority && (
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-subtle)' }}>
            {getSeniorityLabel(job.seniority)}
          </span>
        )}
        {job.employment_type && (
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-subtle)' }}>
            {getEmploymentTypeLabel(job.employment_type)}
          </span>
        )}
        {showScore && score != null && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: '0.8125rem',
              fontWeight: 600,
              color: 'var(--accent)',
            }}
          >
            {Math.round(score * 100)}% match
          </span>
        )}
      </div>

      {/* Skills */}
      {skills.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
          {skills.map((skill) => (
            <span
              key={skill}
              style={{
                padding: '0.2rem 0.625rem',
                background: 'var(--bg-subtle)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-full)',
                fontSize: '0.75rem',
                color: 'var(--text-muted)',
              }}
            >
              {skill}
            </span>
          ))}
          {(job.skills?.length || 0) > 5 && (
            <span style={{ fontSize: '0.75rem', color: 'var(--text-subtle)', alignSelf: 'center' }}>
              +{(job.skills?.length || 0) - 5} more
            </span>
          )}
        </div>
      )}

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: '0.25rem', borderTop: '1px solid var(--border)' }}>
        <span
          style={{
            fontSize: '0.75rem',
            padding: '0.2rem 0.5rem',
            background: 'var(--bg-subtle)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-subtle)',
          }}
        >
          {job.source || 'Direct'}
        </span>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Link
            href={`/jobs/${job.id}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              fontSize: '0.8125rem',
              color: 'var(--accent)',
              fontWeight: 500,
              textDecoration: 'none',
            }}
          >
            View details
          </Link>
          {job.source_url && (
            <a
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                padding: '0.25rem',
                color: 'var(--text-subtle)',
                lineHeight: 0,
              }}
              title="Open original posting"
            >
              <ArrowSquareOut size={15} />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
