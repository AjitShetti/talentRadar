'use client';

import { useState, useEffect, use } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { ArrowLeft, Microphone, Lock, WarningCircle } from '@phosphor-icons/react';
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
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in to view session details
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Sign in to inspect full rubric evaluation and answers.
        </p>
        <Link href="/login" style={{ display: 'inline-block', padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, textDecoration: 'none' }}>
          Sign in
        </Link>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ color: 'var(--text-subtle)', padding: '4rem 0', textAlign: 'center' }}>
        Loading session details...
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div style={{ maxWidth: '600px', margin: '4rem auto' }}>
        <Link href="/interview/history" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-muted)', textDecoration: 'none', marginBottom: '1.5rem', fontSize: '0.875rem' }}>
          <ArrowLeft size={16} /> Back to history
        </Link>
        <div style={{ padding: '1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          {error || 'Session not found.'}
        </div>
      </div>
    );
  }

  const score = detail.total_score;
  const getScoreColor = (s: number) => scoreColour(s);
  const getScoreLabel = (s: number) => scoreLabel(s);

  const breakdowns: { label: string; key: keyof typeof detail.score_breakdown; color: string }[] = [
    { label: 'Correctness', key: 'correctness', color: '#16a34a' },
    { label: 'Clarity', key: 'clarity', color: 'var(--accent)' },
    { label: 'Depth', key: 'depth', color: '#7c3aed' },
  ];

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <Link href="/interview/history" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', color: 'var(--text-muted)', textDecoration: 'none', fontSize: '0.875rem' }}>
        <ArrowLeft size={16} /> Back to history
      </Link>

      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.75rem', background: 'var(--surface)' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--text)', marginBottom: '0.5rem' }}>
              {(detail.track || 'interview').replace(/_/g, ' ')}
            </h1>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', color: 'var(--text-muted)', fontSize: '0.875rem' }}>
              <span style={{ fontWeight: 500 }}>{detail.difficulty}</span>
              <span>•</span>
              <span>{new Date(detail.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</span>
              {detail.duration_seconds && <span>• {Math.ceil(detail.duration_seconds / 60)} min</span>}
              {!detail.completed && <span style={{ fontSize: '0.75rem', padding: '0.1rem 0.4rem', background: 'var(--warning-bg)', color: 'var(--warning)', borderRadius: 'var(--radius-sm)' }}>Incomplete</span>}
            </div>
          </div>
          {score !== null && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '80px' }}>
              <div style={{ fontSize: '2.5rem', fontWeight: 700, color: getScoreColor(score), lineHeight: 1 }}>{Math.round(score)}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-subtle)', marginTop: '0.2rem' }}>/ 100</div>
              <div style={{ fontSize: '0.75rem', color: getScoreColor(score), fontWeight: 600 }}>{getScoreLabel(score)}</div>
            </div>
          )}
        </div>

        <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {breakdowns.map(({ label, key, color }) => {
            const val = Math.round(detail.score_breakdown[key] || 0);
            return (
              <div key={key}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>
                  <span>{label}</span><span style={{ color, fontWeight: 600 }}>{val}%</span>
                </div>
                <div style={{ height: '6px', background: 'var(--bg-subtle)', borderRadius: '99px', overflow: 'hidden' }}>
                  <div style={{ width: `${val}%`, height: '100%', background: color, borderRadius: '99px' }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <h2 style={{ fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)' }}>Question breakdown</h2>

      {detail.answer_scores.length === 0 ? (
        <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-subtle)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)' }}>
          No question scores recorded.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {detail.answer_scores.map((s) => {
            const avg = (s.score_correctness + s.score_clarity + s.score_depth) / 3;
            return (
              <div key={s.question_index} style={{ padding: '1.25rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)' }}>
                <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
                  <div style={{ minWidth: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 'var(--radius-sm)', border: `1px solid ${getScoreColor(avg * 10)}`, color: getScoreColor(avg * 10), fontSize: '0.8125rem', fontWeight: 700, flexShrink: 0 }}>
                    Q{s.question_index + 1}
                  </div>
                  <div style={{ flex: 1 }}>
                    {s.was_followup && (
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', background: 'var(--bg-subtle)', border: '1px solid var(--border)', padding: '0.1rem 0.4rem', borderRadius: 'var(--radius-sm)', marginBottom: '0.5rem', display: 'inline-block' }}>Follow-up</span>
                    )}
                    <p style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.35rem', lineHeight: 1.4 }}>{s.question_text}</p>
                    {s.answer_summary && (
                      <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '0.75rem', lineHeight: 1.5 }}>{s.answer_summary}</p>
                    )}
                    <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                      {[
                        { label: 'Correctness', val: s.score_correctness, color: '#16a34a' },
                        { label: 'Clarity', val: s.score_clarity, color: 'var(--accent)' },
                        { label: 'Depth', val: s.score_depth, color: '#7c3aed' },
                      ].map(({ label, val, color }) => (
                        <div key={label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: '60px' }}>
                          <div style={{ fontSize: '1.1rem', fontWeight: 700, color, lineHeight: 1 }}>{val.toFixed(1)}</div>
                          <div style={{ fontSize: '0.6875rem', color: 'var(--text-subtle)', marginTop: '0.2rem' }}>{label}</div>
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

      <div>
        <Link href="/interview" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', fontWeight: 500, textDecoration: 'none' }}>
          <Microphone size={16} /> Practice again
        </Link>
      </div>
    </div>
  );
}
