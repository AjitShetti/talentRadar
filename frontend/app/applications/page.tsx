'use client';

import { useState, useEffect, useRef } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Briefcase, Lock, Trash, ArrowSquareOut, CaretDown, MagnifyingGlass, WarningCircle } from '@phosphor-icons/react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication, ApplicationStatus } from '@/lib/types';
import { useAuthModal } from '@/components/AuthModalProvider';

const STATUSES: { key: ApplicationStatus; label: string; color: string; bg: string }[] = [
  { key: 'saved', label: 'Saved', color: 'var(--text-muted)', bg: 'var(--bg-subtle)' },
  { key: 'applied', label: 'Applied', color: '#0284c7', bg: '#f0f9ff' },
  { key: 'screening', label: 'Screening', color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'interview', label: 'Interview', color: 'var(--accent)', bg: 'var(--accent-subtle)' },
  { key: 'offer', label: 'Offer', color: 'var(--success)', bg: 'var(--success-bg)' },
  { key: 'rejected', label: 'Rejected', color: 'var(--error)', bg: 'var(--error-bg)' },
];

export default function ApplicationsPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

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
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in to track applications
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Save jobs and organize your job search pipeline in one place.
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

  const grouped = STATUSES.reduce<Record<string, JobApplication[]>>((acc, s) => {
    acc[s.key] = applications.filter((a) => a.status === s.key);
    return acc;
  }, {} as Record<string, JobApplication[]>);

  return (
    <div style={{ paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Application tracking
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
          {applications.length} tracked application{applications.length !== 1 ? 's' : ''} across your search pipeline.
        </p>
      </div>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          <WarningCircle size={16} style={{ flexShrink: 0 }} />
          {error}
        </div>
      )}

      {loading && (
        <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem', padding: '2rem 0' }}>
          Loading applications...
        </p>
      )}

      {!loading && applications.length === 0 && !error && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', textAlign: 'center', padding: '4rem 2rem', background: 'var(--surface)' }}>
          <Briefcase size={40} style={{ margin: '0 auto 1rem auto', color: 'var(--text-subtle)' }} />
          <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.5rem' }}>No applications yet</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '1.5rem', maxWidth: '40ch', margin: '0 auto 1.5rem auto' }}>
            Search open roles and click the bookmark icon on any job card to start tracking.
          </p>
          <Link
            href="/search"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem 1rem',
              background: 'var(--accent)',
              color: '#fff',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
            }}
          >
            <MagnifyingGlass size={15} />
            <span>Search jobs</span>
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
                  background: 'var(--surface)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)',
                  padding: '1rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.75rem',
                }}
              >
                {/* Column Header */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    paddingBottom: '0.5rem',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <span style={{ fontSize: '0.8125rem', fontWeight: 600, color }}>
                    {label}
                  </span>
                  <span
                    style={{
                      fontSize: '0.75rem',
                      padding: '0.1rem 0.45rem',
                      borderRadius: 'var(--radius-full)',
                      background: 'var(--bg-subtle)',
                      color: 'var(--text-muted)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    {col.length}
                  </span>
                </div>

                {/* Cards in Column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
                  {col.map((app) => {
                    const statusInfo = STATUSES.find((s) => s.key === app.status?.toLowerCase()) || STATUSES[0];
                    return (
                      <div
                        key={app.id}
                        style={{
                          padding: '0.875rem',
                          background: 'var(--bg)',
                          border: '1px solid var(--border)',
                          borderRadius: 'var(--radius-sm)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.35rem',
                        }}
                      >
                        <div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--text)', lineHeight: 1.3 }}>
                          {app.job?.title ?? 'Job Title'}
                        </div>
                        {app.job?.company_name && (
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            {app.job.company_name}
                          </div>
                        )}
                        {app.job?.location_raw && (
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-subtle)' }}>
                            {app.job.location_raw}
                          </div>
                        )}

                        <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', marginTop: '0.5rem' }}>
                          <div style={{ position: 'relative', flex: 1 }}>
                            <button
                              onClick={() => setOpenDropdown(openDropdown === app.id ? null : app.id)}
                              style={{
                                width: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: '0.25rem',
                                padding: '0.25rem 0.5rem',
                                background: statusInfo.bg,
                                border: '1px solid var(--border)',
                                borderRadius: 'var(--radius-sm)',
                                color: statusInfo.color,
                                cursor: 'pointer',
                                fontSize: '0.75rem',
                                fontWeight: 500,
                              }}
                            >
                              <span>{statusInfo.label}</span>
                              <CaretDown size={11} />
                            </button>
                            {openDropdown === app.id && (
                              <div
                                style={{
                                  position: 'absolute',
                                  top: 'calc(100% + 4px)',
                                  left: 0,
                                  zIndex: 100,
                                  background: 'var(--surface)',
                                  border: '1px solid var(--border)',
                                  borderRadius: 'var(--radius-sm)',
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
                                      padding: '0.35rem 0.5rem',
                                      background: 'transparent',
                                      border: 'none',
                                      color: s.color,
                                      cursor: 'pointer',
                                      fontSize: '0.75rem',
                                      fontWeight: 500,
                                      borderRadius: '4px',
                                    }}
                                    onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-subtle)')}
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
                                color: 'var(--text-subtle)',
                                display: 'flex',
                                alignItems: 'center',
                                padding: '0.25rem',
                              }}
                              title="Open posting"
                            >
                              <ArrowSquareOut size={14} />
                            </a>
                          )}
                          <button
                            onClick={() => removeApp(app)}
                            style={{
                              background: 'none',
                              border: 'none',
                              cursor: 'pointer',
                              color: 'var(--text-subtle)',
                              display: 'flex',
                              alignItems: 'center',
                              padding: '0.25rem',
                            }}
                            title="Remove"
                          >
                            <Trash size={14} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {col.length === 0 && (
                    <div
                      style={{
                        padding: '1rem 0.5rem',
                        border: '1px dashed var(--border)',
                        borderRadius: 'var(--radius-sm)',
                        color: 'var(--text-subtle)',
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
