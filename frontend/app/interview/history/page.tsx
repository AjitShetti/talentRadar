'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { format } from 'date-fns';
import { CaretRight, Microphone, Clock, Lock, WarningCircle } from '@phosphor-icons/react';
import { interviewApi } from '@/lib/interview-api';
import { getTrack, getDifficulty, scoreColour, scoreLabel } from '@/lib/interview-catalog';
import type { SessionSummary } from '@/lib/interview-types';
import { useAuthModal } from '@/components/AuthModalProvider';

export default function HistoryPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) return;

    (async () => {
      try {
        const res = await interviewApi.history(session.accessToken as string);
        setSessions(res.sessions);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load history.');
      } finally {
        setLoading(false);
      }
    })();
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in for interview history
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Review past interview performance, grading rubric scores, and question logs.
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

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
            Interview history
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
            Review past mock interview transcripts, grading breakdowns, and performance.
          </p>
        </div>
        <Link
          href="/interview"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.4rem',
            padding: '0.5rem 1rem',
            background: 'var(--accent)',
            color: '#fff',
            borderRadius: 'var(--radius-sm)',
            fontSize: '0.875rem',
            fontWeight: 500,
            textDecoration: 'none',
          }}
        >
          <Microphone size={15} />
          <span>New interview</span>
        </Link>
      </div>

      {/* Content */}
      {loading ? (
        <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>
          Loading interview history...
        </p>
      ) : error ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          <WarningCircle size={16} />
          <span>{error}</span>
        </div>
      ) : sessions.length === 0 ? (
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', textAlign: 'center', padding: '4rem 2rem', background: 'var(--surface)' }}>
          <Microphone size={40} style={{ margin: '0 auto 1rem auto', color: 'var(--text-subtle)' }} />
          <h3 style={{ fontSize: '1.125rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.5rem' }}>No interviews recorded yet</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '1.5rem', maxWidth: '40ch', margin: '0 auto 1.5rem auto' }}>
            Launch an interactive session to practice technical and behavioral questions.
          </p>
          <Link
            href="/interview"
            style={{
              display: 'inline-block',
              padding: '0.5rem 1rem',
              background: 'var(--accent)',
              color: '#fff',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.875rem',
              fontWeight: 500,
              textDecoration: 'none',
            }}
          >
            Start your first interview
          </Link>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {sessions.map((s) => {
            const track = getTrack(s.track);
            const difficulty = getDifficulty(s.difficulty);
            const score = s.total_score ?? null;
            const date = (() => {
              try {
                return format(new Date(s.created_at), 'MMM d, yyyy, h:mm a');
              } catch {
                return s.created_at;
              }
            })();

            return (
              <Link key={s.id} href={`/interview/history/${s.id}`} style={{ textDecoration: 'none' }}>
                <div
                  style={{
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1.25rem',
                    padding: '1rem 1.25rem',
                    transition: 'border-color 150ms ease',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--border-hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                >
                  {/* Score badge */}
                  <div
                    style={{
                      minWidth: '48px',
                      height: '48px',
                      borderRadius: 'var(--radius-sm)',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      border: `1px solid ${score !== null ? scoreColour(score) : 'var(--border)'}`,
                      background: 'var(--bg-subtle)',
                      flexShrink: 0,
                    }}
                  >
                    {score !== null ? (
                      <>
                        <span
                          style={{
                            fontWeight: 700,
                            fontSize: '1rem',
                            color: scoreColour(score),
                            lineHeight: 1,
                          }}
                        >
                          {Math.round(score)}
                        </span>
                        <span style={{ fontSize: '0.55rem', color: 'var(--text-subtle)', marginTop: '2px' }}>/ 100</span>
                      </>
                    ) : (
                      <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
                        N/A
                      </span>
                    )}
                  </div>

                  {/* Details */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: '0.9375rem',
                        color: 'var(--text)',
                        marginBottom: '0.2rem',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {track?.label ?? s.track}
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        {difficulty?.label ?? s.difficulty}
                      </span>
                      {score !== null && (
                        <span style={{ fontSize: '0.75rem', color: scoreColour(score), fontWeight: 500 }}>
                          {scoreLabel(score)}
                        </span>
                      )}
                      {!s.completed && (
                        <span style={{ fontSize: '0.75rem', color: 'var(--warning)', background: 'var(--warning-bg)', padding: '0.1rem 0.4rem', borderRadius: 'var(--radius-sm)' }}>
                          Incomplete
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Date + duration */}
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.2rem' }}>
                      {date}
                    </div>
                    {s.duration_seconds && (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.25rem',
                          justifyContent: 'flex-end',
                          fontSize: '0.75rem',
                          color: 'var(--text-subtle)',
                        }}
                      >
                        <Clock size={11} />
                        <span>{Math.ceil(s.duration_seconds / 60)} min</span>
                      </div>
                    )}
                  </div>

                  <CaretRight size={16} style={{ color: 'var(--text-subtle)' }} />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
