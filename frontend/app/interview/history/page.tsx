'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { format } from 'date-fns';
import { ChevronRight, Mic, Trophy, Clock, Lock, History, ArrowRight } from 'lucide-react';
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
        <h2 style={{ fontSize: '1.4rem', marginBottom: '0.75rem' }}>Sign In for Interview History</h2>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.75rem', lineHeight: 1.6 }}>
          Review past interview performance, grading rubric scores, and question logs.
        </p>
        <button onClick={openLogin} className="btn btn-primary" style={{ width: '100%' }}>
          <span>Sign In / Create Account</span>
          <ArrowRight size={15} />
        </button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
            <History size={13} />
            <span>SESSION ARCHIVE</span>
          </div>
          <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
            Interview History
          </h1>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem' }}>
            Review past mock interview transcripts, grading breakdowns, and performance trends.
          </p>
        </div>
        <Link href="/interview" className="btn btn-primary">
          <Mic size={15} />
          <span>New Interview</span>
        </Link>
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ color: 'var(--color-fg-muted)', padding: '2rem 0' }}>
          Loading interview archive...
        </div>
      ) : error ? (
        <div className="auth-error">
          <span>{error}</span>
        </div>
      ) : sessions.length === 0 ? (
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 2rem' }}>
          <Trophy size={40} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Interviews Recorded Yet</h3>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
            Launch an interactive session to practice technical and behavioral questions.
          </p>
          <Link href="/interview" className="btn btn-primary">
            <span>Start Your First Interview</span>
            <ArrowRight size={15} />
          </Link>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
          {sessions.map((s) => {
            const track = getTrack(s.track);
            const difficulty = getDifficulty(s.difficulty);
            const score = s.total_score ?? null;
            const date = (() => {
              try {
                return format(new Date(s.created_at), 'MMM d, yyyy · h:mm a');
              } catch {
                return s.created_at;
              }
            })();

            return (
              <Link key={s.id} href={`/interview/history/${s.id}`} style={{ textDecoration: 'none' }}>
                <div
                  className="panel panel-interactive"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1.25rem',
                    padding: '1.15rem 1.35rem',
                  }}
                >
                  {/* Score badge */}
                  <div
                    style={{
                      minWidth: '50px',
                      height: '50px',
                      borderRadius: 'var(--border-radius-sm)',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      border: `1px solid ${score !== null ? scoreColour(score) : 'var(--color-border)'}`,
                      background: 'var(--color-surface-elevated)',
                      flexShrink: 0,
                    }}
                  >
                    {score !== null ? (
                      <>
                        <span
                          style={{
                            fontFamily: 'var(--font-mono)',
                            fontWeight: 800,
                            fontSize: '1.1rem',
                            color: scoreColour(score),
                            lineHeight: 1,
                          }}
                        >
                          {Math.round(score)}
                        </span>
                        <span style={{ fontSize: '0.55rem', color: 'var(--color-fg-subtle)', marginTop: '2px' }}>/ 100</span>
                      </>
                    ) : (
                      <span style={{ fontSize: '0.6875rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase' }}>
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
                        color: 'var(--color-fg)',
                        marginBottom: '0.25rem',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {track?.label ?? s.track}
                    </div>
                    <div style={{ display: 'flex', gap: '0.65rem', alignItems: 'center', flexWrap: 'wrap' }}>
                      <span className="badge" style={{ fontSize: '0.6875rem', textTransform: 'uppercase' }}>
                        {difficulty?.label ?? s.difficulty}
                      </span>
                      {score !== null && (
                        <span style={{ fontSize: '0.75rem', color: scoreColour(score), fontWeight: 600 }}>
                          {scoreLabel(score)}
                        </span>
                      )}
                      {!s.completed && (
                        <span className="badge badge-accent" style={{ fontSize: '0.6875rem' }}>
                          Incomplete
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Date + duration */}
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)', marginBottom: '0.2rem' }}>
                      {date}
                    </div>
                    {s.duration_seconds && (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.3rem',
                          justifyContent: 'flex-end',
                          fontSize: '0.75rem',
                          color: 'var(--color-fg-subtle)',
                        }}
                      >
                        <Clock size={11} />
                        <span>{Math.ceil(s.duration_seconds / 60)} min</span>
                      </div>
                    )}
                  </div>

                  <ChevronRight size={16} style={{ color: 'var(--color-fg-subtle)' }} />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
