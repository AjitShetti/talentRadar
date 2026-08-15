'use client';

import { useState, useEffect, useRef } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Briefcase, Lock, Loader2, Trash2, ExternalLink, ChevronDown, Search } from 'lucide-react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication, ApplicationStatus } from '@/lib/types';

const STATUSES: { key: ApplicationStatus; label: string; color: string }[] = [
  { key: 'saved',      label: 'Saved',      color: 'var(--color-fg-muted)' },
  { key: 'applied',   label: 'Applied',    color: '#3b82f6' },
  { key: 'screening', label: 'Screening',  color: '#a855f7' },
  { key: 'interview', label: 'Interview',  color: 'var(--color-accent)' },
  { key: 'offer',     label: 'Offer',      color: '#22c55e' },
  { key: 'rejected',  label: 'Rejected',   color: '#ef4444' },
];

export default function ApplicationsPage() {
  const { data: session, status } = useSession();
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
    if (status !== 'authenticated' || !session?.accessToken) return;
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
      setApplications(prev => prev.map(a => a.id === app.id ? updated : a));
    } catch { /* silent */ }
    setOpenDropdown(null);
  };

  const removeApp = async (app: JobApplication) => {
    if (!session?.accessToken) return;
    if (!confirm(`Remove "${app.job?.title ?? 'this job'}" from your tracker?`)) return;
    try {
      await applicationsApi.remove(session.accessToken as string, app.id);
      setApplications(prev => prev.filter(a => a.id !== app.id));
    } catch { /* silent */ }
  };

  if (status === 'loading') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', padding: '4rem 0' }}>
        <Loader2 size={24} className="animate-spin text-accent" /> LOADING...
      </div>
    );
  }

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', marginBottom: '1rem' }}>SIGN IN TO TRACK APPLICATIONS</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Save jobs and track your application pipeline in one place.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  const grouped = STATUSES.reduce<Record<string, JobApplication[]>>((acc, s) => {
    acc[s.key] = applications.filter(a => a.status === s.key);
    return acc;
  }, {} as Record<string, JobApplication[]>);

  return (
    <div>
      <div style={{ marginBottom: '3rem' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', marginBottom: '0.75rem' }}>
          APPLICATION<span style={{ color: 'var(--color-accent)' }}>_</span>TRACKER
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.1rem' }}>
          {applications.length} application{applications.length !== 1 ? 's' : ''} tracked.
        </p>
      </div>

      {error && (
        <div style={{ padding: '1rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5', marginBottom: '2rem' }}>{error}</div>
      )}

      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>
          <Loader2 size={24} className="animate-spin text-accent" /> LOADING...
        </div>
      )}

      {!loading && applications.length === 0 && !error && (
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 2rem' }}>
          <Briefcase size={48} color="var(--color-fg-muted)" style={{ margin: '0 auto 1.5rem' }} />
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.25rem', marginBottom: '1rem' }}>NO APPLICATIONS YET</h3>
          <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Browse jobs and save the ones you want to track.</p>
          <Link href="/search" className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}>
            <Search size={16} /> Search Jobs
          </Link>
        </div>
      )}

      {!loading && applications.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '1.5rem' }} ref={dropdownRef}>
          {STATUSES.map(({ key, label, color }) => {
            const col = grouped[key] || [];
            return (
              <div key={key}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem', paddingBottom: '0.75rem', borderBottom: `2px solid ${color}` }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '0.85rem', letterSpacing: '0.08em', color }}>{label.toUpperCase()}</span>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', color: 'var(--color-fg-muted)', marginLeft: 'auto' }}>{col.length}</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {col.map(app => {
                    const statusInfo = STATUSES.find(s => s.key === app.status?.toLowerCase()) || STATUSES[0];
                    return (
                      <div key={app.id} className="panel" style={{ padding: '1rem', position: 'relative' }}>
                        <div style={{ marginBottom: '0.4rem', fontWeight: 600, fontSize: '0.9rem', lineHeight: 1.3 }}>
                          {app.job?.title ?? 'Unknown Job'}
                        </div>
                        {app.job?.company_name && (
                          <div style={{ fontSize: '0.78rem', color: 'var(--color-fg-muted)', marginBottom: '0.25rem' }}>{app.job.company_name}</div>
                        )}
                        {app.job?.location_raw && (
                          <div style={{ fontSize: '0.72rem', color: 'var(--color-fg-muted)', marginBottom: '0.6rem' }}>{app.job.location_raw}</div>
                        )}
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                          <div style={{ position: 'relative', flex: 1 }}>
                            <button
                              onClick={() => setOpenDropdown(openDropdown === app.id ? null : app.id)}
                              style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.25rem', padding: '0.35rem 0.6rem', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--color-border)', color: statusInfo.color, cursor: 'pointer', fontFamily: 'var(--font-display)', fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.06em' }}
                            >
                              {statusInfo.label} <ChevronDown size={12} />
                            </button>
                            {openDropdown === app.id && (
                              <div style={{ position: 'absolute', top: '100%', left: 0, zIndex: 100, background: 'var(--color-bg)', border: '1px solid var(--color-border)', minWidth: '140px', boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}>
                                {STATUSES.map(s => (
                                  <button key={s.key} onClick={() => updateStatus(app, s.key)}
                                    style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.6rem 0.75rem', background: 'transparent', border: 'none', color: s.color, cursor: 'pointer', fontFamily: 'var(--font-display)', fontSize: '0.75rem', fontWeight: 600 }}
                                  >{s.label}</button>
                                ))}
                              </div>
                            )}
                          </div>
                          {app.job?.source_url && (
                            <a href={app.job.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-fg-muted)', display: 'flex', alignItems: 'center' }}>
                              <ExternalLink size={14} />
                            </a>
                          )}
                          <button onClick={() => removeApp(app)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-fg-muted)', display: 'flex', alignItems: 'center' }}>
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {col.length === 0 && (
                    <div style={{ padding: '1rem', border: '1px dashed var(--color-border)', color: 'var(--color-fg-muted)', fontSize: '0.78rem', textAlign: 'center', fontFamily: 'var(--font-display)' }}>EMPTY</div>
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
