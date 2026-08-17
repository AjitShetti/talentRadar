'use client';

import { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Search, Target, Mic, Briefcase, Trophy, ArrowRight, Loader2, Lock, LayoutDashboard } from 'lucide-react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication } from '@/lib/types';
import { useAuthModal } from '@/components/AuthModalProvider';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const APPLICATION_STATUSES = [
  { key: 'saved', label: 'Saved', color: 'var(--color-fg-muted)' },
  { key: 'applied', label: 'Applied', color: '#38bdf8' },
  { key: 'screening', label: 'Screening', color: '#a855f7' },
  { key: 'interview', label: 'Interview', color: 'var(--color-accent)' },
  { key: 'offer', label: 'Offer', color: '#10b981' },
  { key: 'rejected', label: 'Rejected', color: '#f87171' },
];

function getScoreColor(s: number) {
  return s >= 80 ? '#10b981' : s >= 60 ? 'var(--color-accent)' : s >= 40 ? '#38bdf8' : '#f87171';
}

function getScoreLabel(s: number) {
  return s >= 80 ? 'EXCELLENT' : s >= 60 ? 'GOOD' : s >= 40 ? 'FAIR' : 'NEEDS WORK';
}

interface SessionSummary {
  id: string;
  track: string;
  difficulty: string;
  total_score: number | null;
  completed: boolean;
  created_at: string;
}

export default function DashboardPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) {
      setLoading(false);
      return;
    }
    const token = session.accessToken as string;
    Promise.all([
      applicationsApi.list(token).catch(() => ({ applications: [], total: 0 })),
      fetch(`${API_BASE}/api/v1/interview/sessions/history`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => (r.ok ? r.json() : { sessions: [] }))
        .catch(() => ({ sessions: [] })),
    ])
      .then(([appRes, sessionRes]) => {
        setApplications(appRes.applications);
        setSessions(sessionRes.sessions || []);
      })
      .finally(() => setLoading(false));
  }, [status, session]);

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
        <h2 style={{ fontSize: '1.4rem', marginBottom: '0.75rem' }}>Sign In to View Dashboard</h2>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.75rem', lineHeight: 1.6 }}>
          Track application progress, review recent AI interview scores, and access quick actions.
        </p>
        <button onClick={openLogin} className="btn btn-primary" style={{ width: '100%' }}>
          <span>Sign In / Create Account</span>
          <ArrowRight size={15} />
        </button>
      </div>
    );
  }

  const bestScore = sessions.reduce<number | null>((best, s) => {
    if (s.total_score == null) return best;
    return best === null ? s.total_score : Math.max(best, s.total_score);
  }, null);

  const activeApps = applications.filter((a) => !['rejected', 'withdrawn'].includes(a.status));
  const statusCounts = APPLICATION_STATUSES.reduce<Record<string, number>>((acc, s) => {
    acc[s.key] = applications.filter((a) => a.status === s.key).length;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <LayoutDashboard size={13} />
          <span>OVERVIEW</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Career Dashboard
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem' }}>
          Welcome back. Here is your recent activity across applications and interview prep.
        </p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--color-fg-muted)', padding: '2rem 0' }}>
          <Loader2 size={20} className="status-dot-pulse" />
          <span>Loading Telemetry Dashboard...</span>
        </div>
      ) : (
        <>
          {/* Key Metric Tiles */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: '1.25rem',
            }}
          >
            {[
              {
                icon: Briefcase,
                label: 'Tracked Jobs',
                value: String(applications.length),
                sub: `${activeApps.length} active pipelines`,
                color: '#38bdf8',
              },
              {
                icon: Mic,
                label: 'Mock Interviews',
                value: String(sessions.length),
                sub: 'sessions completed',
                color: 'var(--color-accent)',
              },
              {
                icon: Trophy,
                label: 'Best Score',
                value: bestScore !== null ? `${Math.round(bestScore)}` : 'N/A',
                sub: bestScore !== null ? getScoreLabel(bestScore) : 'No tests recorded',
                color: bestScore !== null ? getScoreColor(bestScore) : 'var(--color-fg-muted)',
              },
            ].map(({ icon: Icon, label, value, sub, color }) => (
              <div key={label} className="panel panel-elevated" style={{ padding: '1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.85rem' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--color-fg-subtle)' }}>
                    {label}
                  </span>
                  <Icon size={16} style={{ color }} />
                </div>
                <div style={{ fontSize: '2rem', fontWeight: 800, color, lineHeight: 1, fontFamily: 'var(--font-mono)', marginBottom: '0.35rem' }}>
                  {value}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>{sub}</div>
              </div>
            ))}
          </div>

          {/* Pipeline Snapshot */}
          {applications.length > 0 && (
            <div className="panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
                <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Application Pipeline</h2>
                <Link href="/applications" className="btn btn-ghost btn-sm" style={{ color: 'var(--color-accent)', fontSize: '0.75rem' }}>
                  <span>View Board</span>
                  <ArrowRight size={13} />
                </Link>
              </div>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                {APPLICATION_STATUSES.map(({ key, label, color }) => (
                  <div
                    key={key}
                    style={{
                      flex: 1,
                      minWidth: '90px',
                      padding: '0.75rem',
                      background: 'var(--color-surface-elevated)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 'var(--border-radius-sm)',
                      textAlign: 'center',
                    }}
                  >
                    <div style={{ fontSize: '1.25rem', fontWeight: 800, color: statusCounts[key] > 0 ? color : 'var(--color-fg-muted)', fontFamily: 'var(--font-mono)' }}>
                      {statusCounts[key]}
                    </div>
                    <div style={{ fontSize: '0.6875rem', color: 'var(--color-fg-subtle)', textTransform: 'uppercase', marginTop: '0.2rem' }}>
                      {label}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Interviews */}
          {sessions.length > 0 && (
            <div className="panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
                <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Recent Interview Sessions</h2>
                <Link href="/interview/history" className="btn btn-ghost btn-sm" style={{ color: 'var(--color-accent)', fontSize: '0.75rem' }}>
                  <span>Full History</span>
                  <ArrowRight size={13} />
                </Link>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
                {sessions.slice(0, 3).map((s) => {
                  const scoreVal = s.total_score;
                  return (
                    <Link key={s.id} href={`/interview/history/${s.id}`} style={{ textDecoration: 'none' }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '1rem',
                          padding: '0.85rem 1rem',
                          background: 'var(--color-surface-elevated)',
                          border: '1px solid var(--color-border)',
                          borderRadius: 'var(--border-radius-sm)',
                          transition: 'border-color var(--transition-fast)',
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-hover)')}
                        onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
                      >
                        <div
                          style={{
                            width: '40px',
                            height: '40px',
                            borderRadius: 'var(--border-radius-sm)',
                            border: `1px solid ${scoreVal !== null ? getScoreColor(scoreVal) : 'var(--color-border)'}`,
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                          }}
                        >
                          {scoreVal !== null ? (
                            <span style={{ fontWeight: 800, fontSize: '0.875rem', color: getScoreColor(scoreVal), fontFamily: 'var(--font-mono)' }}>
                              {Math.round(scoreVal)}
                            </span>
                          ) : (
                            <span style={{ fontSize: '0.6875rem', color: 'var(--color-fg-muted)' }}>N/A</span>
                          )}
                        </div>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--color-fg)' }}>
                            {(s.track || 'Interview').replace(/_/g, ' ')}
                          </div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>
                            {new Date(s.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                          </div>
                        </div>
                        <ArrowRight size={14} style={{ color: 'var(--color-fg-subtle)' }} />
                      </div>
                    </Link>
                  );
                })}
              </div>
            </div>
          )}

          {/* Quick Actions Grid */}
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--color-fg-subtle)', marginBottom: '0.85rem' }}>
              Quick Launch Tools
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
              {[
                { icon: Search, label: 'Search Live Jobs', href: '/search', color: '#38bdf8' },
                { icon: Target, label: 'Match Resume', href: '/resume-studio?tab=match', color: '#a855f7' },
                { icon: Mic, label: 'Practice Interview', href: '/interview', color: 'var(--color-accent)' },
                { icon: Briefcase, label: 'ATS Resume Studio', href: '/resume-studio', color: '#10b981' },
              ].map(({ icon: Icon, label, href, color }) => (
                <Link key={href} href={href} style={{ textDecoration: 'none' }}>
                  <div
                    className="panel panel-interactive"
                    style={{
                      padding: '1.15rem',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.85rem',
                    }}
                  >
                    <Icon size={18} style={{ color, flexShrink: 0 }} />
                    <span style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--color-fg)' }}>{label}</span>
                    <ArrowRight size={13} style={{ color: 'var(--color-fg-subtle)', marginLeft: 'auto' }} />
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
