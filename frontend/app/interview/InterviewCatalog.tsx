'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Code2, Server, Database, Network, Mic, ArrowRight, Lock, ChevronRight, type LucideIcon, Sparkles } from 'lucide-react';
import { TRACKS, DIFFICULTIES } from '@/lib/interview-catalog';
import type { InterviewTrack, InterviewDifficulty } from '@/lib/interview-types';
import { interviewApi } from '@/lib/interview-api';
import { useAuthModal } from '@/components/AuthModalProvider';

const ICON_MAP: Record<string, LucideIcon> = {
  Code2, Server, Database, Network,
};

export default function InterviewCatalog() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const { openLogin } = useAuthModal();
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
        sessionStorage.setItem(`interview:${res.session_id}`, JSON.stringify(res.agent_state));
        router.push(`/interview/${res.session_id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to start interview session.');
      }
    });
  }

  return (
    <div style={{ maxWidth: '860px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
      {/* Header */}
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <Mic size={13} />
          <span>AI VOICE INTERVIEWER</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Technical & Behavioral Mock Interviews
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem', maxWidth: '65ch' }}>
          Practice with an adaptive AI voice coach. Receive instant rubric grading on correctness, system architecture, and communication clarity.
        </p>
      </div>

      {/* Step 1: Track Selection */}
      <section>
        <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--color-fg-subtle)', marginBottom: '0.85rem' }}>
          01 / Choose Track
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
          {TRACKS.map((track) => {
            const Icon = ICON_MAP[track.icon] ?? Code2;
            const isSelected = selectedTrack === track.id;
            return (
              <button
                key={track.id}
                onClick={() => setSelectedTrack(track.id)}
                className="panel panel-interactive"
                style={{
                  textAlign: 'left',
                  padding: '1.25rem',
                  background: isSelected ? 'var(--color-accent-subtle)' : 'var(--color-surface)',
                  borderColor: isSelected ? 'var(--color-accent)' : 'var(--color-border)',
                  boxShadow: isSelected ? '0 0 16px var(--color-accent-glow)' : 'none',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.4rem',
                  alignItems: 'flex-start',
                }}
              >
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: 'var(--border-radius-sm)',
                    background: isSelected ? 'var(--color-accent)' : 'var(--color-surface-elevated)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: isSelected ? '#ffffff' : 'var(--color-fg)',
                    marginBottom: '0.4rem',
                  }}
                >
                  <Icon size={18} />
                </div>
                <div style={{ fontWeight: 700, fontSize: '0.9375rem', color: '#ffffff' }}>
                  {track.label}
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--color-fg-muted)', lineHeight: 1.5 }}>
                  {track.description}
                </div>
              </button>
            );
          })}
        </div>

        {/* Topics preview */}
        {selectedTrack && (
          <div
            className="glass-panel"
            style={{
              marginTop: '1rem',
              padding: '0.85rem 1.15rem',
              display: 'flex',
              gap: '0.5rem',
              flexWrap: 'wrap',
              alignItems: 'center',
            }}
          >
            <span style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600 }}>
              Included Topics:
            </span>
            {TRACKS.find((t) => t.id === selectedTrack)?.topics.map((topic) => (
              <span
                key={topic}
                className="badge badge-accent"
                style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)' }}
              >
                {topic}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Step 2: Difficulty */}
      <section>
        <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--color-fg-subtle)', marginBottom: '0.85rem' }}>
          02 / Select Difficulty
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {DIFFICULTIES.map((diff) => {
            const isSelected = selectedDifficulty === diff.id;
            return (
              <button
                key={diff.id}
                onClick={() => setSelectedDifficulty(diff.id)}
                className={`btn ${isSelected ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '0.75rem 1.5rem', display: 'flex', alignItems: 'center', gap: '0.65rem' }}
              >
                <span>{diff.label}</span>
                <span style={{ fontSize: '0.75rem', opacity: 0.8, fontFamily: 'var(--font-mono)' }}>
                  ({diff.questions} qs)
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Auth gate or Launch button */}
      {!isAuthenticated ? (
        <div
          className="panel"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '1rem',
            padding: '1.25rem 1.5rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Lock size={18} style={{ color: 'var(--color-fg-muted)' }} />
            <span style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem' }}>
              Sign in to initiate interactive mock interview sessions.
            </span>
          </div>
          <button onClick={openLogin} className="btn btn-primary btn-sm">
            <span>Sign In</span>
            <ChevronRight size={14} />
          </button>
        </div>
      ) : (
        <div>
          {error && (
            <div className="auth-error" style={{ marginBottom: '1rem' }}>
              <span>{error}</span>
            </div>
          )}
          <button
            onClick={handleStart}
            disabled={!canStart}
            className="btn btn-primary btn-lg"
            style={{ width: '100%', maxWidth: '340px' }}
          >
            {isPending ? (
              <>Preparing Session...</>
            ) : (
              <>
                <Mic size={18} />
                <span>Launch Interview Simulation</span>
                <ArrowRight size={16} />
              </>
            )}
          </button>
          <p style={{ marginTop: '0.65rem', fontSize: '0.8125rem', color: 'var(--color-fg-subtle)' }}>
            {selectedTrack && selectedDifficulty
              ? `${DIFFICULTIES.find((d) => d.id === selectedDifficulty)?.questions} questions with live voice transcription`
              : 'Choose your desired track and difficulty level to begin.'}
          </p>
        </div>
      )}
    </div>
  );
}
