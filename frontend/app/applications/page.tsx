'use client';

import { useState, useEffect, useRef } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Briefcase, Lock, Loader2, Trash2, ExternalLink, ChevronDown, Search, ArrowRight } from 'lucide-react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication, ApplicationStatus } from '@/lib/types';
import { useAuthModal } from '@/components/AuthModalProvider';

const STATUSES: { key: ApplicationStatus; label: string; color: string; bg: string }[] = [
  { key: 'saved', label: 'Saved', color: 'var(--color-fg-muted)', bg: 'rgba(255, 255, 255, 0.05)' },
  { key: 'applied', label: 'Applied', color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.1)' },
  { key: 'screening', label: 'Screening', color: '#a855f7', bg: 'rgba(168, 85, 247, 0.1)' },
  { key: 'interview', label: 'Interview', color: 'var(--color-accent)', bg: 'var(--color-accent-subtle)' },
  { key: 'offer', label: 'Offer', color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' },
  { key: 'rejected', label: 'Rejected', color: '#f87171', bg: 'rgba(239, 68, 68, 0.1)' },
];

export default function ApplicationsPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpenDropdown(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const res = await applicationsApi.list(session.accessToken as string);
        setApplications(res.applications);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load applications');
      } finally {
        setLoading(false);
      }
    })();
  }, [status, session]);

  const updateStatus = async (app: JobApplication, newStatus: ApplicationStatus) => {
    if (!session?.accessToken) return;
    try {
      const updated = await applicationsApi.update(session.accessToken as string, app.id, { status: newStatus });
      setApplications((prev) => prev.map((a) => (a.id === app.id ? updated : a)));
    } catch {
      /* silent */
    }
    setOpenDropdown(null);
  };

  const removeApp = async (app: JobApplication) => {
    if (!session?.accessToken) return;
    if (!confirm(`Remove "${app.job?.title ?? 'this job'}" from your tracker?`)) return;
    try {
      await applicationsApi.remove(session.accessToken as string, app.id);
      setApplications((prev) => prev.filter((a) => a.id !== app.id));
    } catch {
      /* silent */
    }
  };

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
        <h2 style={{ fontSize: '1.4rem', marginBottom: '0.75rem' }}>Sign In to Track Applications</h2>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.75rem', lineHeight: 1.6 }}>
          Save jobs across multiple boards and organize your hiring pipeline in one place.
        </p>
        <button onClick={openLogin} className="btn btn-primary" style={{ width: '100%' }}>
          <span>Sign In / Create Account</span>
          <ArrowRight size={15} />
        </button>
      </div>
    );
  }

  const grouped = STATUSES.reduce<Record<string, JobApplication[]>>((acc, s) => {
    acc[s.key] = applications.filter((a) => a.status === s.key);
    return acc;
  }, {} as Record<string, JobApplication[]>);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <Briefcase size={13} />
          <span>HIRING PIPELINE</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Application Tracking
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem', maxWidth: '65ch' }}>
          {applications.length} saved and tracked application{applications.length !== 1 ? 's' : ''} across all stages.
        </p>
      </div>

      {error && (
        <div className="auth-error">
          <span>{error}</span>
        </div>
      )}

      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--color-fg-muted)', padding: '2rem 0' }}>
          <Loader2 size={20} className="status-dot-pulse" />
          <span>Loading Application Pipeline...</span>
        </div>
      )}

      {!loading && applications.length === 0 && !error && (
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 2rem' }}>
          <Briefcase size={48} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Tracked Applications Yet</h3>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
            Browse open opportunities and click the bookmark icon to start tracking applications.
          </p>
          <Link href="/search" className="btn btn-primary">
            <Search size={15} />
            <span>Search Live Jobs</span>
          </Link>
        </div>
      )}

      {!loading && applications.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
            gap: '1.25rem',
            alignItems: 'start',
          }}
          ref={dropdownRef}
        >
          {STATUSES.map(({ key, label, color, bg }) => {
            const col = grouped[key] || [];
            return (
              <div
                key={key}
                style={{
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--border-radius)',
                  padding: '1rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.85rem',
                }}
              >
                {/* Column Header */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    paddingBottom: '0.65rem',
                    borderBottom: '1px solid var(--color-border-subtle)',
                  }}
                >
                  <span style={{ fontSize: '0.8125rem', fontWeight: 700, color, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                    {label}
                  </span>
                  <span
                    className="badge"
                    style={{ fontSize: '0.6875rem', padding: '0.1rem 0.45rem', fontFamily: 'var(--font-mono)' }}
                  >
                    {col.length}
                  </span>
                </div>

                {/* Cards in Column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
                  {col.map((app) => {
                    const statusInfo = STATUSES.find((s) => s.key === app.status?.toLowerCase()) || STATUSES[0];
                    return (
                      <div
                        key={app.id}
                        style={{
                          padding: '0.85rem',
                          background: 'var(--color-surface-elevated)',
                          border: '1px solid var(--color-border)',
                          borderRadius: 'var(--border-radius-sm)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.4rem',
                        }}
                      >
                        <div style={{ fontWeight: 600, fontSize: '0.875rem', color: '#ffffff', lineHeight: 1.3 }}>
                          {app.job?.title ?? 'Job Title'}
                        </div>
                        {app.job?.company_name && (
                          <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>
                            {app.job.company_name}
                          </div>
                        )}
                        {app.job?.location_raw && (
                          <div style={{ fontSize: '0.6875rem', color: 'var(--color-fg-subtle)' }}>
                            {app.job.location_raw}
                          </div>
                        )}

                        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', marginTop: '0.4rem' }}>
                          <div style={{ position: 'relative', flex: 1 }}>
                            <button
                              onClick={() => setOpenDropdown(openDropdown === app.id ? null : app.id)}
                              style={{
                                width: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: '0.25rem',
                                padding: '0.3rem 0.5rem',
                                background: 'var(--color-surface)',
                                border: '1px solid var(--color-border)',
                                borderRadius: '4px',
                                color: statusInfo.color,
                                cursor: 'pointer',
                                fontSize: '0.6875rem',
                                fontWeight: 600,
                              }}
                            >
                              <span>{statusInfo.label}</span>
                              <ChevronDown size={11} />
                            </button>
                            {openDropdown === app.id && (
                              <div
                                style={{
                                  position: 'absolute',
                                  top: 'calc(100% + 4px)',
                                  left: 0,
                                  zIndex: 100,
                                  background: 'var(--color-surface-elevated)',
                                  border: '1px solid var(--color-border)',
                                  borderRadius: 'var(--border-radius-sm)',
                                  padding: '4px',
                                  minWidth: '130px',
                                  boxShadow: 'var(--shadow-lg)',
                                }}
                              >
                                {STATUSES.map((s) => (
                                  <button
                                    key={s.key}
                                    onClick={() => updateStatus(app, s.key)}
                                    style={{
                                      display: 'block',
                                      width: '100%',
                                      textAlign: 'left',
                                      padding: '0.45rem 0.65rem',
                                      background: 'transparent',
                                      border: 'none',
                                      color: s.color,
                                      cursor: 'pointer',
                                      fontSize: '0.75rem',
                                      fontWeight: 600,
                                      borderRadius: '4px',
                                    }}
                                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
                                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                                  >
                                    {s.label}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>

                          {app.job?.source_url && (
                            <a
                              href={app.job.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{
                                color: 'var(--color-fg-muted)',
                                display: 'flex',
                                alignItems: 'center',
                                padding: '0.3rem',
                              }}
                            >
                              <ExternalLink size={13} />
                            </a>
                          )}
                          <button
                            onClick={() => removeApp(app)}
                            style={{
                              background: 'none',
                              border: 'none',
                              cursor: 'pointer',
                              color: 'var(--color-fg-subtle)',
                              display: 'flex',
                              alignItems: 'center',
                              padding: '0.3rem',
                            }}
                            title="Remove"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {col.length === 0 && (
                    <div
                      style={{
                        padding: '1.25rem 0.5rem',
                        border: '1px dashed var(--color-border-subtle)',
                        borderRadius: 'var(--border-radius-sm)',
                        color: 'var(--color-fg-subtle)',
                        fontSize: '0.75rem',
                        textAlign: 'center',
                      }}
                    >
                      No items
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
