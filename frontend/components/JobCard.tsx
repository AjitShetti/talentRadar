'use client';

import { Job } from '@/lib/types';
import { formatSalary, formatDate, getSeniorityLabel, getEmploymentTypeLabel } from '@/lib/utils';
import {
  MapPin,
  Building2,
  DollarSign,
  Calendar,
  ExternalLink,
  Briefcase,
  Star,
  Bookmark,
  BookmarkCheck,
  Globe2,
} from 'lucide-react';
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
  const companyInitial = companyName.charAt(0).toUpperCase();

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

  return (
    <div
      className="panel panel-interactive"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '1.15rem',
        position: 'relative',
        background: 'var(--color-surface)',
      }}
    >
      {/* Top Row: Company Avatar + Job Title + Match Score / Save Button */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.85rem' }}>
          {/* Company Avatar Initial */}
          <div
            style={{
              width: '42px',
              height: '42px',
              borderRadius: 'var(--border-radius-sm)',
              background: 'var(--color-surface-elevated)',
              border: '1px solid var(--color-border)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: '1.1rem',
              color: 'var(--color-fg)',
              flexShrink: 0,
            }}
          >
            {companyInitial}
          </div>

          <div>
            <h3 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '0.2rem', lineHeight: 1.3 }}>
              <Link
                href={`/jobs/${job.id}`}
                style={{ color: 'var(--color-fg)', textDecoration: 'none' }}
                onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-fg)')}
              >
                {job.title}
              </Link>
            </h3>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--color-fg-muted)', fontSize: '0.875rem' }}>
              <Building2 size={14} style={{ color: 'var(--color-fg-subtle)' }} />
              <span style={{ fontWeight: 500 }}>{companyName}</span>
            </div>
          </div>
        </div>

        {/* Top Right Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
          {showScore && job.match_score !== undefined && (
            <div
              className={`badge ${job.match_score >= 0.7 ? 'badge-accent' : 'badge-emerald'}`}
              style={{ fontSize: '0.8125rem', padding: '0.25rem 0.6rem' }}
            >
              <Star size={13} fill="currentColor" />
              <span>{Math.round(job.match_score * 100)}% Fit</span>
            </div>
          )}

          <button
            onClick={handleSave}
            title={saved ? 'Saved to applications' : 'Save job'}
            style={{
              background: 'var(--color-surface-elevated)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--border-radius-sm)',
              cursor: session?.accessToken ? 'pointer' : 'default',
              color: saved ? 'var(--color-accent)' : 'var(--color-fg-muted)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '0.4rem',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              if (session?.accessToken) {
                e.currentTarget.style.borderColor = 'var(--color-border-hover)';
                e.currentTarget.style.color = 'var(--color-fg)';
              }
            }}
            onMouseLeave={(e) => {
              if (session?.accessToken) {
                e.currentTarget.style.borderColor = 'var(--color-border)';
                e.currentTarget.style.color = saved ? 'var(--color-accent)' : 'var(--color-fg-muted)';
              }
            }}
          >
            {saved ? <BookmarkCheck size={16} /> : <Bookmark size={16} />}
          </button>
        </div>
      </div>

      {/* Telemetry Grid: Location, Salary, Seniority, Posted */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
          gap: '0.65rem',
          fontSize: '0.8125rem',
          color: 'var(--color-fg-muted)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {job.is_remote ? (
            <>
              <Globe2 size={14} className="text-accent" />
              <span style={{ color: '#38bdf8', fontWeight: 500 }}>Remote</span>
            </>
          ) : (
            <>
              <MapPin size={14} style={{ color: 'var(--color-fg-subtle)' }} />
              <span>{job.location_raw || job.city || 'Location unspecified'}</span>
            </>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <DollarSign size={14} style={{ color: 'var(--color-fg-subtle)' }} />
          <span style={{ fontFamily: 'var(--font-mono)' }}>
            {formatSalary(job.salary_min, job.salary_max, job.salary_currency)}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Briefcase size={14} style={{ color: 'var(--color-fg-subtle)' }} />
          <span>{getSeniorityLabel(job.seniority)}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Calendar size={14} style={{ color: 'var(--color-fg-subtle)' }} />
          <span>{formatDate(job.posted_at)}</span>
        </div>
      </div>

      {/* Skills Tags */}
      {job.skills && job.skills.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
          {job.skills.slice(0, 5).map((skill) => (
            <span
              key={skill}
              style={{
                padding: '0.2rem 0.55rem',
                background: 'rgba(255, 255, 255, 0.04)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--border-radius-sm)',
                fontSize: '0.75rem',
                color: 'var(--color-fg-muted)',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {skill}
            </span>
          ))}
          {job.skills.length > 5 && (
            <span
              style={{
                padding: '0.2rem 0.4rem',
                fontSize: '0.75rem',
                color: 'var(--color-fg-subtle)',
              }}
            >
              +{job.skills.length - 5} more
            </span>
          )}
        </div>
      )}

      {/* Card Footer: Employment Type + Direct Apply Button */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginTop: '0.25rem',
          paddingTop: '0.85rem',
          borderTop: '1px solid var(--color-border-subtle)',
        }}
      >
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          {job.employment_type && (
            <span
              style={{
                fontSize: '0.6875rem',
                padding: '0.15rem 0.5rem',
                borderRadius: 'var(--border-radius-sm)',
                background: 'var(--color-surface-elevated)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-fg-subtle)',
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}
            >
              {getEmploymentTypeLabel(job.employment_type)}
            </span>
          )}
        </div>

        {job.source_url && (
          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-secondary btn-sm"
            style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }}
          >
            <span>Apply</span>
            <ExternalLink size={12} />
          </a>
        )}
      </div>
    </div>
  );
}
