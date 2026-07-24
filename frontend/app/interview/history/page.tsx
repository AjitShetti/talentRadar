'use client';

// frontend/app/interview/history/page.tsx
// ─────────────────────────────────────────────────────────────────
// Interview history page — shows all past sessions with scores.
// Client component because it needs the auth token from next-auth.
// ─────────────────────────────────────────────────────────────────

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { format } from 'date-fns';
import { ChevronRight, Mic, Trophy, Clock, Lock } from 'lucide-react';
import { interviewApi } from '@/lib/interview-api';
import { getTrack, getDifficulty, scoreColour, scoreLabel } from '@/lib/interview-catalog';
import type { SessionSummary } from '@/lib/interview-types';

export default function HistoryPage() {
  const { data: session, status } = useSession();
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
      <div style={{ maxWidth: '640px', margin: '4rem auto', textAlign: 'center' }}>
        <Lock size={32} color="var(--color-fg-muted)" style={{ marginBottom: '1rem' }} />
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '1.5rem' }}>
          Sign in to view your interview history.
        </p>
        <Link href="/login" className="btn-primary">Sign In</Link>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 0' }}>
      {/* Header */}
      <div style={{ marginBottom: '2.5rem', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{
            fontFamily: 'var(--font-display)', fontSize: 'clamp(1.75rem, 4vw, 2.5rem)',
            fontWeight: 800, letterSpacing: '-0.03em', marginBottom: '0.5rem'
          }}>
            INTERVIEW_HISTORY<span style={{ color: 'var(--color-accent)' }}>.</span>
          </h1>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.9rem' }}>
            All your past mock interview sessions.
          </p>
        </div>
        <Link
          href="/interview"
          className="btn-primary"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
            textDecoration: 'none', fontSize: '0.85rem'
          }}
        >
          <Mic size={15} /> New Interview
        </Link>
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>
          LOADING...
        </div>
      ) : error ? (
        <div style={{
          padding: '1.25rem', background: 'rgba(255,69,0,0.08)',
          border: '1px solid rgba(255,69,0,0.3)', color: 'var(--color-accent)'
        }}>
          {error}
        </div>
      ) : sessions.length === 0 ? (
        <div className="panel" style={{ textAlign: 'center', padding: '3rem' }}>
          <Trophy size={32} color="var(--color-fg-muted)" style={{ marginBottom: '1rem' }} />
          <p style={{ color: 'var(--color-fg-muted)', marginBottom: '1.5rem' }}>
            No interviews yet. Start your first session!
          </p>
          <Link href="/interview" className="btn-primary" style={{ textDecoration: 'none' }}>
            Start Interview
          </Link>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem' }}>
          {sessions.map((s) => {
            const track = getTrack(s.track);
            const difficulty = getDifficulty(s.difficulty);
            const score = s.total_score ?? null;
            const date = (() => {
              try { return format(new Date(s.created_at), 'MMM d, yyyy · h:mm a'); }
              catch { return s.created_at; }
            })();

            return (
              <Link
                key={s.id}
                href={`/interview/history/${s.id}`}
                style={{ textDecoration: 'none' }}
              >
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '1.5rem',
                  padding: '1.25rem 1.5rem',
                  background: 'var(--color-card-bg)',
                  border: '1px solid var(--color-border)',
                  transition: 'border-color 150ms ease', cursor: 'pointer'
                }}
                  onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
                  onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--color-border)')}
                >
                  {/* Score badge */}
                  <div style={{
                    minWidth: '56px', height: '56px',
                    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                    border: `2px solid ${score !== null ? scoreColour(score) : 'var(--color-border)'}`,
                    flexShrink: 0
                  }}>
                    {score !== null ? (
                      <>
                        <span style={{
                          fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '1.15rem',
                          color: scoreColour(score), lineHeight: 1
                        }}>
                          {Math.round(score)}
                        </span>
                        <span style={{ fontSize: '0.6rem', color: 'var(--color-fg-muted)' }}>/ 100</span>
                      </>
                    ) : (
                      <span style={{ fontSize: '0.65rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        N/A
                      </span>
                    )}
                  </div>

                  {/* Details */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '0.95rem',
                      marginBottom: '0.3rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
                    }}>
                      {track?.label ?? s.track}
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' as const }}>
                      <span style={{
                        fontSize: '0.75rem', color: 'var(--color-fg-muted)',
                        fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em'
                      }}>
                        {difficulty?.label ?? s.difficulty}
                      </span>
                      {score !== null && (
                        <span style={{ fontSize: '0.75rem', color: scoreColour(score) }}>
                          {scoreLabel(score)}
                        </span>
                      )}
                      {!s.completed && (
                        <span style={{
                          fontSize: '0.7rem', padding: '0.1rem 0.4rem',
                          background: 'rgba(255,255,255,0.06)', border: '1px solid var(--color-border)',
                          color: 'var(--color-fg-muted)'
                        }}>
                          INCOMPLETE
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Date + duration */}
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: '0.78rem', color: 'var(--color-fg-muted)', marginBottom: '0.25rem' }}>
                      {date}
                    </div>
                    {s.duration_seconds && (
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: '0.3rem', justifyContent: 'flex-end',
                        fontSize: '0.75rem', color: 'var(--color-fg-muted)'
                      }}>
                        <Clock size={11} />
                        {Math.ceil(s.duration_seconds / 60)} min
                      </div>
                    )}
                  </div>

                  <ChevronRight size={16} color="var(--color-fg-muted)" />
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
