'use client';

import { useState, useEffect, use } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { ArrowLeft, Mic, Lock, Loader2 } from 'lucide-react';

import { getTrack, getDifficulty, scoreColour, scoreLabel } from '@/lib/interview-catalog';

interface AnswerScore {
  question_index: number;
  question_text: string;
  answer_summary: string | null;
  score_correctness: number;
  score_clarity: number;
  score_depth: number;
  was_followup: boolean;
}

interface SessionDetail {
  id: string;
  track: string;
  difficulty: string;
  total_score: number | null;
  completed: boolean;
  duration_seconds: number | null;
  created_at: string;
  score_breakdown: { correctness: number; clarity: number; depth: number };
  answer_scores: AnswerScore[];
}

export default function SessionDetailPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const { data: session, status } = useSession();
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) return;
    (async () => {
      try {
        const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const res = await fetch(`${API_BASE}/api/v1/interview/sessions/${sessionId}`, {
          headers: { Authorization: `Bearer ${session.accessToken}` },
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text);
        }
        setDetail(await res.json());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load session');
      } finally {
        setLoading(false);
      }
    })();
  }, [status, session, sessionId]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={32} color="var(--color-fg-muted)" style={{ marginBottom: '1rem' }} />
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '1.5rem' }}>Sign in to view session details.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', padding: '4rem 0' }}>
        <Loader2 size={24} className="animate-spin text-accent" /> LOADING SESSION...
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div style={{ maxWidth: 600, margin: '4rem auto' }}>
        <Link href="/interview/history" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', textDecoration: 'none', marginBottom: '1.5rem', fontFamily: 'var(--font-display)', fontSize: '0.82rem' }}>
          <ArrowLeft size={16} /> BACK TO HISTORY
        </Link>
        <div style={{ padding: '1.25rem', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', color: 'var(--color-accent)' }}>
          {error || 'Session not found.'}
        </div>
      </div>
    );
  }

  const score = detail.total_score;
  const getScoreColor = (s: number) => scoreColour(s);
  const getScoreLabel = (s: number) => scoreLabel(s);

  const breakdowns: { label: string; key: keyof typeof detail.score_breakdown; color: string }[] = [
    { label: 'Correctness', key: 'correctness', color: '#22c55e' },
    { label: 'Clarity',     key: 'clarity',     color: '#3b82f6' },
    { label: 'Depth',       key: 'depth',        color: 'var(--color-accent)' },
  ];

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '2rem 0' }}>
      <Link href="/interview/history" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', textDecoration: 'none', marginBottom: '2rem', fontFamily: 'var(--font-display)', fontSize: '0.82rem', letterSpacing: '0.06em' }}>
        <ArrowLeft size={14} /> BACK TO HISTORY
      </Link>

      <div className="panel" style={{ marginBottom: '2rem', padding: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(1.5rem, 3vw, 2rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.5rem' }}>
              {detail.track.replace(/_/g, ' ').toUpperCase()}<span style={{ color: 'var(--color-accent)' }}>.</span>
            </h1>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', color: 'var(--color-fg-muted)', fontSize: '0.85rem' }}>
              <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, textTransform: 'uppercase' }}>{detail.difficulty}</span>
              <span>·</span>
              <span>{new Date(detail.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</span>
              {detail.duration_seconds && <span>· {Math.ceil(detail.duration_seconds / 60)} min</span>}
              {!detail.completed && <span style={{ fontSize: '0.72rem', padding: '0.1rem 0.4rem', border: '1px solid var(--color-border)' }}>INCOMPLETE</span>}
            </div>
          </div>
          {score !== null && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 80 }}>
              <div style={{ fontFamily: 'var(--font-display)', fontSize: '2.5rem', fontWeight: 800, color: getScoreColor(score), lineHeight: 1 }}>{Math.round(score)}</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--color-fg-muted)' }}>/ 100</div>
              <div style={{ fontSize: '0.75rem', color: getScoreColor(score), fontWeight: 600 }}>{getScoreLabel(score)}</div>
            </div>
          )}
        </div>

        <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
          {breakdowns.map(({ label, key, color }) => {
            const val = Math.round(detail.score_breakdown[key] || 0);
            return (
              <div key={key}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', marginBottom: '0.3rem' }}>
                  <span>{label}</span><span style={{ color }}>{val}%</span>
                </div>
                <div style={{ height: 6, background: 'rgba(255,255,255,0.08)', borderRadius: 3 }}>
                  <div style={{ width: `${val}%`, height: '100%', background: color, borderRadius: 3 }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1rem', fontWeight: 700, letterSpacing: '0.05em', marginBottom: '1rem' }}>QUESTION BREAKDOWN</h2>

      {detail.answer_scores.length === 0 ? (
        <div className="panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-fg-muted)' }}>No question scores recorded.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '2.5rem' }}>
          {detail.answer_scores.map((s) => {
            const avg = (s.score_correctness + s.score_clarity + s.score_depth) / 3;
            return (
              <div key={s.question_index} className="panel" style={{ padding: '1.25rem' }}>
                <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
                  <div style={{ minWidth: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', border: `1px solid ${getScoreColor(avg * 10)}`, color: getScoreColor(avg * 10), fontFamily: 'var(--font-display)', fontSize: '0.78rem', fontWeight: 700, flexShrink: 0 }}>
                    Q{s.question_index + 1}
                  </div>
                  <div style={{ flex: 1 }}>
                    {s.was_followup && (
                      <span style={{ fontSize: '0.65rem', color: 'var(--color-fg-muted)', background: 'rgba(255,255,255,0.06)', border: '1px solid var(--color-border)', padding: '0.1rem 0.4rem', marginBottom: '0.5rem', display: 'inline-block' }}>FOLLOW-UP</span>
                    )}
                    <p style={{ fontSize: '0.88rem', fontWeight: 600, marginBottom: '0.5rem', lineHeight: 1.4 }}>{s.question_text}</p>
                    {s.answer_summary && (
                      <p style={{ fontSize: '0.8rem', color: 'var(--color-fg-muted)', marginBottom: '0.75rem', lineHeight: 1.5 }}>{s.answer_summary}</p>
                    )}
                    <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                      {[
                        { label: 'Correctness', val: s.score_correctness, color: '#22c55e' },
                        { label: 'Clarity',     val: s.score_clarity,     color: '#3b82f6' },
                        { label: 'Depth',       val: s.score_depth,       color: 'var(--color-accent)' },
                      ].map(({ label, val, color }) => (
                        <div key={label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 60 }}>
                          <div style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', fontWeight: 700, color, lineHeight: 1 }}>{val.toFixed(1)}</div>
                          <div style={{ fontSize: '0.62rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Link href="/interview" className="btn btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}>
        <Mic size={16} /> Practice Again
      </Link>
    </div>
  );
}
