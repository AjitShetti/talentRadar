'use client';

import { useState, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Search, Target, Mic, Briefcase, Trophy, ArrowRight, Loader2, Lock, TrendingUp } from 'lucide-react';
import { applicationsApi } from '@/lib/applications-api';
import type { JobApplication } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const APPLICATION_STATUSES = [
  { key: 'saved',      label: 'Saved',     color: 'var(--color-fg-muted)' },
  { key: 'applied',   label: 'Applied',   color: '#3b82f6' },
  { key: 'screening', label: 'Screening', color: '#a855f7' },
  { key: 'interview', label: 'Interview', color: 'var(--color-accent)' },
  { key: 'offer',     label: 'Offer',     color: '#22c55e' },
  { key: 'rejected',  label: 'Rejected',  color: '#ef4444' },
];

function getScoreColor(s: number) {
  return s >= 80 ? '#22c55e' : s >= 60 ? 'var(--color-accent)' : s >= 40 ? '#3b82f6' : '#ef4444';
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
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) return;
    const token = session.accessToken as string;
    Promise.all([
      applicationsApi.list(token).catch(() => ({ applications: [], total: 0 })),
      fetch(`${API_BASE}/api/v1/interview/sessions/history`, {
        headers: { Authorization: `Bearer ${token}` },
      }).then(r => r.ok ? r.json() : { sessions: [] }).catch(() => ({ sessions: [] })),
    ]).then(([appRes, sessionRes]) => {
      setApplications(appRes.applications);
      setSessions(sessionRes.sessions || []);
    }).finally(() => setLoading(false));
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', marginBottom: '1rem' }}>SIGN IN TO VIEW DASHBOARD</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Track your applications, interviews, and progress.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  const bestScore = sessions.reduce<number | null>((best, s) => {
    if (s.total_score == null) return best;
    return best === null ? s.total_score : Math.max(best, s.total_score);
  }, null);

  const activeApps = applications.filter(a => !['rejected', 'withdrawn'].includes(a.status));
  const statusCounts = APPLICATION_STATUSES.reduce<Record<string, number>>((acc, s) => {
    acc[s.key] = applications.filter(a => a.status === s.key).length;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div>
      <div style={{ marginBottom: '3rem' }}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.12em', color: 'var(--color-fg-muted)', marginBottom: '0.75rem' }}>WELCOME BACK</div>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          YOUR CAREER<span style={{ color: 'var(--color-accent)' }}>_</span>DASHBOARD
        </h1>
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>
          <Loader2 size={24} className="animate-spin text-accent" /> LOADING...
        </div>
      ) : (
        <>
          {/* Stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '2.5rem' }}>
            {[
              { icon: Briefcase, label: 'Applications', value: String(applications.length), sub: `${activeApps.length} active`, color: '#3b82f6' },
              { icon: Mic,       label: 'Interviews',   value: String(sessions.length), sub: 'sessions practiced', color: 'var(--color-accent)' },
              { icon: Trophy,    label: 'Best Score',   value: bestScore !== null ? String(Math.round(bestScore)) : '—', sub: bestScore !== null ? getScoreLabel(bestScore) : 'No sessions yet', color: bestScore !== null ? getScoreColor(bestScore) : 'var(--color-fg-muted)' },
            ].map(({ icon: Icon, label, value, sub, color }) => (
              <div key={label} className="panel" style={{ padding: '1.25rem' }}>
                <Icon size={20} style={{ color, marginBottom: '0.75rem' }} />
                <div style={{ fontFamily: 'var(--font-display)', fontSize: '2rem', fontWeight: 800, color, lineHeight: 1, marginBottom: '0.25rem' }}>{value}</div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: '0.72rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)', marginBottom: '0.15rem', textTransform: 'uppercase' }}>{label}</div>
                <div style={{ fontSize: '0.72rem', color: 'var(--color-fg-muted)' }}>{sub}</div>
              </div>
            ))}
          </div>

          {/* Pipeline */}
          {applications.length > 0 && (
            <div className="panel" style={{ marginBottom: '2.5rem', padding: '1.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '0.85rem', fontWeight: 700, letterSpacing: '0.08em' }}>APPLICATION PIPELINE</h2>
                <Link href="/applications" style={{ color: 'var(--color-accent)', textDecoration: 'none', fontFamily: 'var(--font-display)', fontSize: '0.72rem', letterSpacing: '0.06em' }}>VIEW ALL →</Link>
              </div>
              <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap' }}>
                {APPLICATION_STATUSES.map(({ key, label, color }) => (
                  <div key={key} style={{ textAlign: 'center', minWidth: 60 }}>
                    <div style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', fontWeight: 800, color: statusCounts[key] > 0 ? color : 'var(--color-fg-muted)', lineHeight: 1 }}>{statusCounts[key]}</div>
                    <div style={{ fontSize: '0.65rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Interviews */}
          {sessions.length > 0 && (
            <div className="panel" style={{ marginBottom: '2.5rem', padding: '1.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '0.85rem', fontWeight: 700, letterSpacing: '0.08em' }}>RECENT INTERVIEWS</h2>
                <Link href="/interview/history" style={{ color: 'var(--color-accent)', textDecoration: 'none', fontFamily: 'var(--font-display)', fontSize: '0.72rem', letterSpacing: '0.06em' }}>VIEW ALL →</Link>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {sessions.slice(0, 3).map(s => {
                  const scoreVal = s.total_score;
                  return (
                    <Link key={s.id} href={`/interview/history/${s.id}`} style={{ textDecoration: 'none' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.875rem', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}>
                        <div style={{ minWidth: 44, height: 44, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', border: `1px solid ${scoreVal !== null ? getScoreColor(scoreVal) : 'var(--color-border)'}`, flexShrink: 0 }}>
                          {scoreVal !== null ? (
                            <><span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '1rem', color: getScoreColor(scoreVal), lineHeight: 1 }}>{Math.round(scoreVal)}</span><span style={{ fontSize: '0.55rem', color: 'var(--color-fg-muted)' }}>/100</span></>
                          ) : <span style={{ fontSize: '0.6rem', color: 'var(--color-fg-muted)' }}>N/A</span>}
                        </div>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '0.85rem' }}>{(s.track || 'interview').replace(/_/g, ' ').toUpperCase()}</div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--color-fg-muted)' }}>{new Date(s.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</div>
                        </div>
                        <ArrowRight size={14} color="var(--color-fg-muted)" />
                      </div>
                    </Link>
                  );
                })}
              </div>
            </div>
          )}

          {/* Quick Actions */}
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '0.85rem', fontWeight: 700, letterSpacing: '0.08em', marginBottom: '1rem' }}>QUICK ACTIONS</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem' }}>
            {[
              { icon: Search,     label: 'Search Jobs',         href: '/search',      color: 'var(--color-fg-muted)' },
              { icon: Target,     label: 'Match Resume',        href: '/match',       color: '#a855f7' },
              { icon: Mic,        label: 'Practice Interview',   href: '/interview',   color: 'var(--color-accent)' },
              { icon: TrendingUp, label: 'Market Trends',       href: '/trends',      color: '#22c55e' },
            ].map(({ icon: Icon, label, href, color }) => (
              <Link key={href} href={href} style={{ textDecoration: 'none' }}>
                <div className="panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', gap: '0.875rem' }}>
                  <Icon size={20} style={{ color, flexShrink: 0 }} />
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.78rem', fontWeight: 700, letterSpacing: '0.05em' }}>{label}</span>
                  <ArrowRight size={14} color="var(--color-fg-muted)" style={{ marginLeft: 'auto' }} />
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
