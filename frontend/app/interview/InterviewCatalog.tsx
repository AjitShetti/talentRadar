'use client';

// frontend/app/interview/InterviewCatalog.tsx
// ─────────────────────────────────────────────────────────────────
// Interactive track + difficulty selection page.
// Uses the existing design system: dark bg, Safety Orange accent,
// glassmorphism panels, Space Grotesk headers, sharp corners.
// ─────────────────────────────────────────────────────────────────

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Code2, Server, Database, Network, Mic, ArrowRight, Lock, ChevronRight, type LucideIcon } from 'lucide-react';
import { TRACKS, DIFFICULTIES } from '@/lib/interview-catalog';
import type { InterviewTrack, InterviewDifficulty } from '@/lib/interview-types';
import { interviewApi } from '@/lib/interview-api';

const ICON_MAP: Record<string, LucideIcon> = {
  Code2, Server, Database, Network,
};

export default function InterviewCatalog() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const [selectedTrack, setSelectedTrack] = useState<InterviewTrack | null>(null);
  const [selectedDifficulty, setSelectedDifficulty] = useState<InterviewDifficulty | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isAuthenticated = status === 'authenticated';
  const canStart = isAuthenticated && selectedTrack && selectedDifficulty && !isPending;

  async function handleStart() {
    if (!canStart || !session?.accessToken) return;
    setError(null);

    startTransition(async () => {
      try {
        const res = await interviewApi.startSession(
          { track: selectedTrack!, difficulty: selectedDifficulty! },
          session.accessToken as string
        );
        // Store the initial agent state in sessionStorage for the session page
        sessionStorage.setItem(`interview:${res.session_id}`, JSON.stringify(res.agent_state));
        router.push(`/interview/${res.session_id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to start session.');
      }
    });
  }

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem 0' }}>
      {/* Header */}
      <div style={{ marginBottom: '3rem' }}>
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
          padding: '0.4rem 1rem',
          background: 'rgba(255,69,0,0.08)',
          border: '1px solid rgba(255,69,0,0.25)',
          fontFamily: 'var(--font-display)',
          fontSize: '0.75rem', letterSpacing: '0.1em', textTransform: 'uppercase',
          color: 'var(--color-accent)', marginBottom: '1.5rem'
        }}>
          <Mic size={12} />
          AI Voice Interviewer
        </div>
        <h1 style={{
          fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)',
          fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1,
          marginBottom: '1rem'
        }}>
          MOCK_INTERVIEWS<span style={{ color: 'var(--color-accent)' }}>.</span>
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.05rem', maxWidth: '520px' }}>
          Practice with a real-time AI voice interviewer. Get scored on correctness, clarity, and depth after every answer.
        </p>
      </div>

      {/* Step 1: Track Selection */}
      <section style={{ marginBottom: '2.5rem' }}>
        <h2 style={{
          fontFamily: 'var(--font-display)', fontSize: '0.8rem',
          letterSpacing: '0.12em', textTransform: 'uppercase',
          color: 'var(--color-fg-muted)', marginBottom: '1rem'
        }}>
          01 / Choose a track
        </h2>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem'
        }}>
          {TRACKS.map((track) => {
            const Icon = ICON_MAP[track.icon] ?? Code2;
            const isSelected = selectedTrack === track.id;
            return (
              <button
                key={track.id}
                onClick={() => setSelectedTrack(track.id)}
                style={{
                  textAlign: 'left', padding: '1.5rem',
                  background: isSelected ? 'rgba(255,69,0,0.08)' : 'var(--color-card-bg)',
                  border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                  cursor: 'pointer', transition: 'all 150ms ease',
                  position: 'relative', display: 'block', width: '100%',
                  color: 'var(--color-fg)',
                }}
              >
                <Icon size={22} color={isSelected ? '#FF4500' : 'var(--color-fg-muted)'} />
                <div style={{
                  fontFamily: 'var(--font-display)', fontWeight: 700, marginTop: '0.75rem',
                  marginBottom: '0.4rem', fontSize: '0.95rem'
                }}>
                  {track.label}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--color-fg-muted)', lineHeight: 1.5 }}>
                  {track.description}
                </div>
                {isSelected && (
                  <div style={{
                    position: 'absolute', top: '0.75rem', right: '0.75rem',
                    width: '8px', height: '8px', borderRadius: '50%',
                    background: 'var(--color-accent)'
                  }} />
                )}
              </button>
            );
          })}
        </div>

        {/* Topics preview */}
        {selectedTrack && (
          <div style={{
            marginTop: '1rem', padding: '1rem 1.25rem',
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--color-border)',
            display: 'flex', gap: '0.5rem', flexWrap: 'wrap' as const, alignItems: 'center'
          }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)', marginRight: '0.25rem', fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Topics:
            </span>
            {TRACKS.find(t => t.id === selectedTrack)?.topics.map(topic => (
              <span key={topic} style={{
                fontSize: '0.75rem', padding: '0.2rem 0.6rem',
                background: 'rgba(255,69,0,0.1)', border: '1px solid rgba(255,69,0,0.2)',
                color: 'var(--color-accent)', fontFamily: 'var(--font-display)'
              }}>
                {topic}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Step 2: Difficulty */}
      <section style={{ marginBottom: '2.5rem' }}>
        <h2 style={{
          fontFamily: 'var(--font-display)', fontSize: '0.8rem',
          letterSpacing: '0.12em', textTransform: 'uppercase',
          color: 'var(--color-fg-muted)', marginBottom: '1rem'
        }}>
          02 / Select difficulty
        </h2>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' as const }}>
          {DIFFICULTIES.map((diff) => {
            const isSelected = selectedDifficulty === diff.id;
            return (
              <button
                key={diff.id}
                onClick={() => setSelectedDifficulty(diff.id)}
                style={{
                  padding: '0.875rem 2rem',
                  background: isSelected ? 'var(--color-accent)' : 'transparent',
                  border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                  color: isSelected ? '#fff' : 'var(--color-fg-muted)',
                  cursor: 'pointer', transition: 'all 150ms ease',
                  fontFamily: 'var(--font-display)', fontWeight: 600,
                  fontSize: '0.9rem', letterSpacing: '0.05em', textTransform: 'uppercase' as const,
                  display: 'flex', alignItems: 'center', gap: '0.5rem'
                }}
              >
                {diff.label}
                <span style={{
                  fontSize: '0.7rem', opacity: 0.7,
                  fontWeight: 400, textTransform: 'none' as const, letterSpacing: 0
                }}>
                  {diff.questions} qs
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Divider */}
      <div style={{ height: '1px', background: 'var(--color-border)', marginBottom: '2rem' }} />

      {/* Auth gate + Start button */}
      {!isAuthenticated ? (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '1rem',
          padding: '1.25rem 1.5rem',
          background: 'rgba(255,255,255,0.03)', border: '1px solid var(--color-border)',
        }}>
          <Lock size={18} color="var(--color-fg-muted)" />
          <span style={{ color: 'var(--color-fg-muted)', fontSize: '0.9rem' }}>
            Sign in to start a mock interview session.
          </span>
          <a href="/login" style={{
            marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
            color: 'var(--color-accent)', fontFamily: 'var(--font-display)',
            fontWeight: 600, fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em'
          }}>
            Sign In <ChevronRight size={14} />
          </a>
        </div>
      ) : (
        <div>
          {error && (
            <div style={{
              marginBottom: '1rem', padding: '0.875rem 1.25rem',
              background: 'rgba(255,69,0,0.08)', border: '1px solid rgba(255,69,0,0.3)',
              color: 'var(--color-accent)', fontSize: '0.9rem'
            }}>
              {error}
            </div>
          )}
          <button
            onClick={handleStart}
            disabled={!canStart}
            className="btn-primary"
            style={{
              opacity: canStart ? 1 : 0.4,
              cursor: canStart ? 'pointer' : 'not-allowed',
              fontSize: '0.95rem', padding: '1rem 2.5rem',
              display: 'inline-flex', alignItems: 'center', gap: '0.75rem'
            }}
          >
            {isPending ? (
              <>Preparing interview...</>
            ) : (
              <>
                <Mic size={18} />
                Start Interview
                <ArrowRight size={16} />
              </>
            )}
          </button>
          <p style={{
            marginTop: '0.75rem', fontSize: '0.8rem', color: 'var(--color-fg-muted)'
          }}>
            {selectedTrack && selectedDifficulty
              ? `${DIFFICULTIES.find(d => d.id === selectedDifficulty)?.questions} questions · voice + text replies`
              : 'Select a track and difficulty to continue.'}
          </p>
        </div>
      )}
    </div>
  );
}
