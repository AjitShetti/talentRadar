'use client';

import { Job } from '@/lib/types';
import { formatSalary, formatDate, getSeniorityLabel, getEmploymentTypeLabel } from '@/lib/utils';
import { MapPin, Building2, DollarSign, Calendar, ExternalLink, Briefcase, Star, Bookmark, BookmarkCheck } from 'lucide-react';
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

  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', position: 'relative' }}>
      
      <div style={{ position: 'absolute', top: '1rem', right: '1rem', zIndex: 10 }}>
        <button
          onClick={async (e) => {
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
          }}
          title={saved ? 'Saved' : 'Save job'}
          style={{
            background: 'none',
            border: 'none',
            cursor: session?.accessToken ? 'pointer' : 'default',
            color: saved ? 'var(--color-accent)' : 'var(--color-fg-muted)',
            display: 'flex',
            alignItems: 'center',
            padding: '0.25rem',
            transition: 'color 150ms ease',
          }}
        >
          {saved ? <BookmarkCheck size={16} /> : <Bookmark size={16} />}
        </button>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', paddingRight: '2.5rem' }}>
        <div>
          <h3 style={{ fontSize: '1.25rem', marginBottom: '0.25rem' }}>
            <Link href={`/jobs/${job.id}`} style={{ textDecoration: 'none', color: 'var(--color-fg)' }}>
              {job.title}
            </Link>
          </h3>
          {(job.company_name || job.company) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)' }}>
              <Building2 size={16} />
              <span>{job.company_name || job.company}</span>
            </div>
          )}
        </div>
        {showScore && job.match_score !== undefined && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.25rem',
            padding: '0.25rem 0.5rem',
            border: `1px solid ${job.match_score > 0.7 ? 'var(--color-accent)' : 'var(--color-border)'}`,
            color: job.match_score > 0.7 ? 'var(--color-accent)' : 'var(--color-fg-muted)',
            fontFamily: 'var(--font-display)',
            fontSize: '0.875rem'
          }}>
            <Star size={14} />
            {Math.round(job.match_score * 100)}%
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.75rem', fontSize: '0.875rem', color: 'var(--color-fg-muted)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <MapPin size={14} />
          <span>{job.is_remote ? '🌐 Remote' : job.location_raw || job.city || 'Not specified'}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <DollarSign size={14} />
          <span>{formatSalary(job.salary_min, job.salary_max, job.salary_currency)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Briefcase size={14} />
          <span>{getSeniorityLabel(job.seniority)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Calendar size={14} />
          <span>{formatDate(job.posted_at)}</span>
        </div>
      </div>

      {job.skills.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.5rem' }}>
          {job.skills.slice(0, 6).map((skill) => (
            <span key={skill} style={{
              padding: '0.25rem 0.5rem',
              background: 'rgba(255,255,255,0.05)',
              border: '1px solid var(--color-border)',
              fontSize: '0.75rem'
            }}>
              {skill}
            </span>
          ))}
          {job.skills.length > 6 && (
            <span style={{ padding: '0.25rem 0.5rem', background: 'transparent', fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>
              +{job.skills.length - 6} more
            </span>
          )}
        </div>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--color-border)'
      }}>
        <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>
          {job.seniority && (
            <span style={{ padding: '0.25rem 0.5rem', border: '1px solid var(--color-border)' }}>
              {getSeniorityLabel(job.seniority)}
            </span>
          )}
          {job.employment_type && (
            <span style={{ padding: '0.25rem 0.5rem', border: '1px solid var(--color-border)' }}>
              {getEmploymentTypeLabel(job.employment_type)}
            </span>
          )}
        </div>
        {job.source_url && (
          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.875rem', color: 'var(--color-accent)', textDecoration: 'none', fontFamily: 'var(--font-display)', fontWeight: 600 }}
          >
            APPLY NOW
            <ExternalLink size={14} />
          </a>
        )}
      </div>
    </div>
  );
}
