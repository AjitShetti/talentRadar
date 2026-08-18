'use client';

import { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { MagnifyingGlass, FileText, Microphone, Briefcase, ArrowRight, Lock } from '@phosphor-icons/react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication } from '@/lib/types';
import { useAuthModal } from '@/components/AuthModalProvider';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface SessionSummary {
  id: string;
  track: string;
  difficulty: string;
  total_score: number | null;
  completed: boolean;
  created_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  saved: 'Saved', applied: 'Applied', screening: 'Screening',
  interview: 'Interview', offer: 'Offer', rejected: 'Rejected',
};

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) { setLoading(false); return; }
    const token = session.accessToken as string;
    Promise.all([
      applicationsApi.list(token).catch(() => ({ applications: [], total: 0 })),
      fetch(`${API_BASE}/api/v1/interview/sessions/history`, { headers: { Authorization: `Bearer ${token}` } })
        .then((r) => (r.ok ? r.json() : { sessions: [] }))
        .catch(() => ({ sessions: [] })),
    ]).then(([appRes, sessionRes]) => {
      setApplications(appRes.applications || []);
      setSessions(sessionRes.sessions || []);
    }).finally(() => setLoading(false));
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in to view your dashboard
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Track your applications, interview scores, and career progress.
        </p>
        <button onClick={openLogin} style={{ padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, cursor: 'pointer' }}>
          Sign in
        </button>
      </div>
    );
  }

  const userName = session?.user?.name?.split(' ')[0] || session?.user?.email?.split('@')[0] || 'there';
  const recentApps = applications.slice(0, 5);
  const recentSessions = sessions.slice(0, 3);

  // Application status counts
  const statusCounts = applications.reduce((acc, app) => {
    acc[app.status] = (acc[app.status] || 0) + 1; return acc;
  }, {} as Record<string, number>);

  return (
    <div style={{ paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
      {/* Welcome */}
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.25rem' }}>
          Welcome back, {userName}.
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
          Here is where your search stands.
        </p>
      </div>

      {loading ? (
        <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>Loading...</p>
      ) : (
        <>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '1rem' }}>
            {[
              { label: 'Total applications', value: applications.length },
              { label: 'Active (interview)', value: statusCounts['interview'] || 0 },
              { label: 'Offers', value: statusCounts['offer'] || 0 },
              { label: 'Practice sessions', value: sessions.length },
            ].map(({ label, value }) => (
              <div key={label} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem 1.5rem', background: 'var(--surface)' }}>
                <p style={{ fontSize: '2rem', fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--text)', lineHeight: 1 }}>
                  {value}
                </p>
                <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>{label}</p>
              </div>
            ))}
          </div>

          {/* Recent activity + Quick actions */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '3rem', alignItems: 'start' }} className="dash-grid">
            {/* Recent applications */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
                <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)' }}>Recent applications</h2>
                <Link href="/applications" style={{ fontSize: '0.8125rem', color: 'var(--accent)', textDecoration: 'none' }}>
                  View all
                </Link>
              </div>
              {recentApps.length === 0 ? (
                <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>
                  No applications yet. <Link href="/search" style={{ color: 'var(--accent)' }}>Search for jobs</Link>.
                </p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0' }}>
                  {recentApps.map((app, i) => (
                    <div
                      key={app.id}
                      style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        padding: '0.875rem 0',
                        borderTop: i > 0 ? '1px solid var(--border)' : 'none',
                      }}
                    >
                      <div>
                        <p style={{ fontSize: '0.9375rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.1rem' }}>
                          {(app as any).job_title || 'Job application'}
                        </p>
                        <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
                          {new Date(app.applied_at || app.created_at || '').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                        </p>
                      </div>
                      <span
                        style={{
                          fontSize: '0.75rem',
                          fontWeight: 500,
                          padding: '0.2rem 0.625rem',
                          borderRadius: 'var(--radius-full)',
                          background: app.status === 'offer' ? 'var(--success-bg)' : app.status === 'rejected' ? 'var(--error-bg)' : 'var(--bg-subtle)',
                          color: app.status === 'offer' ? 'var(--success)' : app.status === 'rejected' ? 'var(--error)' : 'var(--text-muted)',
                          border: '1px solid var(--border)',
                        }}
                      >
                        {STATUS_LABELS[app.status] || app.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Quick links */}
            <div style={{ minWidth: '200px' }}>
              <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '1rem' }}>Quick access</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                {[
                  { href: '/search', icon: MagnifyingGlass, label: 'Search jobs' },
                  { href: '/resume-studio', icon: FileText, label: 'Resume studio' },
                  { href: '/interview', icon: Microphone, label: 'Practice interview' },
                  { href: '/applications', icon: Briefcase, label: 'Applications' },
                  { href: '/agent', icon: ArrowRight, label: 'Career agent' },
                ].map(({ href, icon: Icon, label }) => (
                  <Link
                    key={href}
                    href={href}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '0.625rem',
                      padding: '0.5rem 0.625rem',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '0.875rem', color: 'var(--text-muted)',
                      textDecoration: 'none',
                      transition: 'background 100ms, color 100ms',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-subtle)'; e.currentTarget.style.color = 'var(--text)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)'; }}
                  >
                    <Icon size={15} />
                    {label}
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </>
      )}

      <style>{`
        @media (max-width: 640px) { .dash-grid { grid-template-columns: 1fr !important; } }
      `}</style>
    </div>
  );
}
