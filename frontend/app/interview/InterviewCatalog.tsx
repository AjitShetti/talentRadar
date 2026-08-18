'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Code, HardDrives, Database, Globe, Microphone, ArrowRight, Lock, WarningCircle } from '@phosphor-icons/react';
import { TRACKS, DIFFICULTIES } from '@/lib/interview-catalog';
import type { InterviewTrack, InterviewDifficulty } from '@/lib/interview-types';
import { interviewApi } from '@/lib/interview-api';
import { useAuthModal } from '@/components/AuthModalProvider';

const ICON_MAP: Record<string, any> = {
  Code2: Code,
  Server: HardDrives,
  Database: Database,
  Network: Globe,
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
    <div style={{ maxWidth: '860px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Technical mock interviews
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
          Practice with an AI voice interviewer and get instant rubric feedback on your responses.
        </p>
      </div>

      {/* Track Selection */}
      <section>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '1rem' }}>
          Choose a track
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
          {TRACKS.map((track) => {
            const Icon = ICON_MAP[track.icon] ?? Code;
            const isSelected = selectedTrack === track.id;
            return (
              <button
                key={track.id}
                onClick={() => setSelectedTrack(track.id)}
                style={{
                  textAlign: 'left',
                  padding: '1.25rem',
                  background: isSelected ? 'var(--accent-subtle)' : 'var(--surface)',
                  border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                  borderRadius: 'var(--radius)',
                  cursor: 'pointer',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.5rem',
                  alignItems: 'flex-start',
                  transition: 'border-color 150ms ease, background 150ms ease',
                }}
              >
                <div
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: 'var(--radius-sm)',
                    background: isSelected ? 'var(--accent)' : 'var(--bg-subtle)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: isSelected ? '#ffffff' : 'var(--text)',
                    marginBottom: '0.25rem',
                  }}
                >
                  <Icon size={18} />
                </div>
                <div style={{ fontWeight: 600, fontSize: '0.9375rem', color: 'var(--text)' }}>
                  {track.label}
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                  {track.description}
                </div>
              </button>
            );
          })}
        </div>

        {/* Topics preview */}
        {selectedTrack && (
          <div
            style={{
              marginTop: '1rem',
              padding: '0.875rem 1.25rem',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--surface)',
              display: 'flex',
              gap: '0.5rem',
              flexWrap: 'wrap',
              alignItems: 'center',
            }}
          >
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', fontWeight: 500 }}>
              Topics covered:
            </span>
            {TRACKS.find((t) => t.id === selectedTrack)?.topics.map((topic) => (
              <span
                key={topic}
                style={{
                  fontSize: '0.75rem',
                  padding: '0.2rem 0.55rem',
                  background: 'var(--bg-subtle)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-full)',
                  color: 'var(--text-muted)',
                }}
              >
                {topic}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Difficulty */}
      <section>
        <h2 style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text)', marginBottom: '1rem' }}>
          Select difficulty
        </h2>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {DIFFICULTIES.map((diff) => {
            const isSelected = selectedDifficulty === diff.id;
            return (
              <button
                key={diff.id}
                onClick={() => setSelectedDifficulty(diff.id)}
                style={{
                  padding: '0.625rem 1.25rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  background: isSelected ? 'var(--accent)' : 'var(--surface)',
                  color: isSelected ? '#ffffff' : 'var(--text)',
                  border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`,
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: 500,
                  transition: 'border-color 150ms ease, background 150ms ease',
                }}
              >
                <span>{diff.label}</span>
                <span style={{ fontSize: '0.75rem', opacity: isSelected ? 0.9 : 0.6 }}>
                  ({diff.questions} questions)
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Auth gate or Launch button */}
      {!isAuthenticated ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '1rem',
            padding: '1.25rem 1.5rem',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            background: 'var(--surface)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Lock size={18} style={{ color: 'var(--text-muted)' }} />
            <span style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
              Sign in to start mock interview simulations.
            </span>
          </div>
          <button
            onClick={openLogin}
            style={{
              padding: '0.45rem 1rem',
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Sign in
          </button>
        </div>
      ) : (
        <div>
          {error && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem', marginBottom: '1rem' }}>
              <WarningCircle size={16} style={{ flexShrink: 0 }} />
              {error}
            </div>
          )}
          <button
            onClick={handleStart}
            disabled={!canStart}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.5rem',
              padding: '0.625rem 1.5rem',
              background: canStart ? 'var(--accent)' : 'var(--border-hover)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.9375rem',
              fontWeight: 500,
              cursor: canStart ? 'pointer' : 'not-allowed',
              transition: 'background 150ms ease',
            }}
            onMouseEnter={(e) => canStart && (e.currentTarget.style.background = 'var(--accent-hover)')}
            onMouseLeave={(e) => canStart && (e.currentTarget.style.background = 'var(--accent)')}
          >
            {isPending ? (
              'Preparing session...'
            ) : (
              <>
                <Microphone size={16} />
                <span>Start interview</span>
                <ArrowRight size={16} />
              </>
            )}
          </button>
          <p style={{ marginTop: '0.65rem', fontSize: '0.8125rem', color: 'var(--text-subtle)' }}>
            {selectedTrack && selectedDifficulty
              ? `${DIFFICULTIES.find((d) => d.id === selectedDifficulty)?.questions} questions with audio interaction`
              : 'Choose a track and difficulty to begin.'}
          </p>
        </div>
      )}
    </div>
  );
}
