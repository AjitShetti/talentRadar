'use client';

// frontend/app/interview/[sessionId]/InterviewSession.tsx
// ─────────────────────────────────────────────────────────────────
// The live interview session UI.
//
// Architecture:
//  - Agent state is round-tripped via sessionStorage + API (stateless backend).
//  - Voice recording uses MediaRecorder → sends blob to /voice/transcribe.
//  - Falls back to browser SpeechSynthesis for TTS (Supertonic 3 in Phase 5).
//  - Text answer is always available as a fallback to voice.
// ─────────────────────────────────────────────────────────────────

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  Mic, MicOff, Send, StopCircle, Volume2,
  ChevronRight, AlertTriangle, CheckCircle2, XCircle
} from 'lucide-react';
import { interviewApi } from '@/lib/interview-api';
import { getTrack, getDifficulty, scoreColour, scoreLabel } from '@/lib/interview-catalog';
import type {
  AgentState,
  AnswerScore,
  FinalScore,
  LiveSessionState,
  SessionPhase,
} from '@/lib/interview-types';

const MAX_QUESTIONS = 5;

interface Props {
  sessionId: string;
}

export default function InterviewSession({ sessionId }: Props) {
  const { data: session } = useSession();
  const router = useRouter();
  const token = session?.accessToken as string | undefined;

  // ── State ──────────────────────────────────────────────────────
  const [liveState, setLiveState] = useState<LiveSessionState>({
    phase: 'loading',
    sessionId,
    agentState: null,
    currentQuestion: '',
    currentQuestionIndex: 0,
    isFollowup: false,
    lastScore: null,
    totalQuestionsAsked: 0,
    sessionComplete: false,
    finalScore: null,
    error: null,
  });

  const [textAnswer, setTextAnswer] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [scoreVisible, setScoreVisible] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Helpers ────────────────────────────────────────────────────

  const updatePhase = useCallback((phase: SessionPhase) => {
    setLiveState(prev => ({ ...prev, phase, error: null }));
  }, []);

  const speakQuestion = useCallback((text: string) => {
    if (!window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.95;
    utterance.pitch = 1.0;
    utterance.onstart = () => setIsSpeaking(true);
    utterance.onend = () => setIsSpeaking(false);
    utterance.onerror = () => setIsSpeaking(false);
    window.speechSynthesis.speak(utterance);
  }, []);

  // ── Load initial state from sessionStorage ─────────────────────
  useEffect(() => {
    const stored = sessionStorage.getItem(`interview:${sessionId}`);
    if (!stored) {
      setLiveState(prev => ({
        ...prev,
        phase: 'idle',
        error: 'Session not found. Please start a new interview from the catalog.',
      }));
      return;
    }

    const agentState: AgentState = JSON.parse(stored);
    const question = agentState.current_question ?? '';

    setLiveState(prev => ({
      ...prev,
      phase: 'questioning',
      agentState,
      currentQuestion: question,
      currentQuestionIndex: agentState.question_index ?? 0,
      isFollowup: false,
      totalQuestionsAsked: 1,
    }));

    speakQuestion(question);
  }, [sessionId, speakQuestion]);

  // ── Recording helpers ──────────────────────────────────────────
  async function startRecording() {
    if (!navigator.mediaDevices) {
      setLiveState(prev => ({ ...prev, error: 'Microphone not available in this browser.' }));
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      recorder.start(250); // collect chunks every 250ms
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
      setRecordingSeconds(0);

      recordingTimerRef.current = setInterval(() => {
        setRecordingSeconds(s => s + 1);
      }, 1000);
    } catch {
      setLiveState(prev => ({ ...prev, error: 'Microphone permission denied.' }));
    }
  }

  async function stopRecordingAndTranscribe(): Promise<string> {
    return new Promise((resolve) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder) { resolve(''); return; }

      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
      setIsRecording(false);

      recorder.onstop = async () => {
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        recorder.stream.getTracks().forEach(t => t.stop());

        if (!token) { resolve(''); return; }

        try {
          const res = await interviewApi.transcribe(blob, 'answer.webm', token);
          if (res.provider === 'browser_fallback' || !res.transcript) {
            // Browser fallback: user types instead
            resolve('');
          } else {
            setTextAnswer(res.transcript);
            resolve(res.transcript);
          }
        } catch {
          resolve('');
        }
      };

      recorder.stop();
    });
  }

  // ── Submit answer ──────────────────────────────────────────────
  async function handleSubmit(overrideAnswer?: string) {
    const answer = (overrideAnswer ?? textAnswer).trim();
    if (!answer || !liveState.agentState || !token) return;

    updatePhase('submitting');
    setScoreVisible(false);
    setTextAnswer('');

    try {
      const res = await interviewApi.submitAnswer(
        { session_id: sessionId, answer, agent_state: liveState.agentState },
        token
      );

      // Update sessionStorage with new agent state
      sessionStorage.setItem(`interview:${sessionId}`, JSON.stringify(res.agent_state));

      const newTotalAsked = liveState.totalQuestionsAsked + 1;

      setLiveState(prev => ({
        ...prev,
        phase: res.session_complete ? 'complete' : 'questioning',
        agentState: res.agent_state,
        currentQuestion: res.question,
        currentQuestionIndex: res.question_index,
        isFollowup: res.is_followup,
        lastScore: res.score,
        totalQuestionsAsked: newTotalAsked,
        sessionComplete: res.session_complete,
        error: null,
      }));

      setScoreVisible(true);

      if (!res.session_complete) {
        setTimeout(() => {
          speakQuestion(res.question);
          setScoreVisible(false);
        }, 3000);
      }
    } catch (err) {
      setLiveState(prev => ({
        ...prev,
        phase: 'questioning',
        error: err instanceof Error ? err.message : 'Failed to submit answer.',
      }));
    }
  }

  async function handleVoiceSubmit() {
    const transcript = await stopRecordingAndTranscribe();
    if (transcript) {
      await handleSubmit(transcript);
    } else {
      updatePhase('questioning'); // fall back to text entry
    }
  }

  async function handleEndSession() {
    if (!liveState.agentState || !token) return;
    updatePhase('submitting');
    try {
      const res = await interviewApi.endSession(
        { session_id: sessionId, agent_state: liveState.agentState },
        token
      );
      setLiveState(prev => ({
        ...prev,
        phase: 'complete',
        sessionComplete: true,
        finalScore: res.final_score,
        currentQuestion: res.closing_message,
        error: null,
      }));
    } catch (err) {
      setLiveState(prev => ({
        ...prev,
        phase: 'questioning',
        error: err instanceof Error ? err.message : 'Failed to end session.',
      }));
    }
  }

  // ── Derived values ─────────────────────────────────────────────
  const track = getTrack(liveState.agentState?.track ?? '');
  const difficulty = getDifficulty(liveState.agentState?.difficulty ?? '');
  const progress = Math.min((liveState.totalQuestionsAsked / MAX_QUESTIONS) * 100, 100);
  const isSubmitting = liveState.phase === 'submitting';

  // ── Render: Loading ────────────────────────────────────────────
  if (liveState.phase === 'loading') {
    return (
      <div style={{ textAlign: 'center', padding: '4rem 0' }}>
        <div style={{ color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>
          LOADING INTERVIEW...
        </div>
      </div>
    );
  }

  // ── Render: Complete / Score ────────────────────────────────────
  if (liveState.phase === 'complete' || liveState.sessionComplete) {
    const score = liveState.finalScore ?? liveState.agentState?.scores?.reduce<FinalScore | null>((_, __, ___, arr) => {
      const avg = (k: keyof AnswerScore) => arr.reduce((s, x) => s + (x[k] as number ?? 0), 0) / arr.length;
      return {
        total_score: Math.round(((avg('correctness') + avg('clarity') + avg('depth')) / 30) * 100),
        correctness: Math.round(avg('correctness') * 10),
        clarity: Math.round(avg('clarity') * 10),
        depth: Math.round(avg('depth') * 10),
        questions_answered: arr.length,
      };
    }, null);

    const total = score?.total_score ?? 0;

    return (
      <div style={{ maxWidth: '640px', margin: '0 auto', padding: '2rem 0' }}>
        <div style={{ textAlign: 'center', marginBottom: '2.5rem' }}>
          <div style={{
            fontSize: 'clamp(3rem, 10vw, 5rem)', fontFamily: 'var(--font-display)', fontWeight: 800,
            color: scoreColour(total), lineHeight: 1
          }}>
            {total}<span style={{ fontSize: '40%', color: 'var(--color-fg-muted)' }}>/100</span>
          </div>
          <div style={{
            marginTop: '0.5rem', fontFamily: 'var(--font-display)', fontSize: '1.1rem',
            color: scoreColour(total), letterSpacing: '0.05em', textTransform: 'uppercase'
          }}>
            {scoreLabel(total)}
          </div>
        </div>

        {/* Closing message */}
        <div className="panel" style={{ marginBottom: '2rem', fontStyle: 'italic', color: 'var(--color-fg-muted)' }}>
          &ldquo;{liveState.currentQuestion}&rdquo;
        </div>

        {/* Breakdown */}
        {score && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem', marginBottom: '2rem' }}>
            {([
              { label: 'Correctness', value: score.correctness },
              { label: 'Clarity', value: score.clarity },
              { label: 'Depth', value: score.depth },
            ] as Array<{ label: string; value: number }>).map(({ label, value }) => (
              <div key={label} className="panel" style={{ textAlign: 'center' }}>
                <div style={{
                  fontFamily: 'var(--font-display)', fontSize: '1.75rem', fontWeight: 800,
                  color: scoreColour(value)
                }}>
                  {value}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  {label}
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: '1rem' }}>
          <button className="btn-primary" onClick={() => router.push('/interview')}
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            New Interview <ChevronRight size={16} />
          </button>
          <button className="btn" onClick={() => router.push('/interview/history')}>
            View History
          </button>
        </div>
      </div>
    );
  }

  // ── Render: Active session ─────────────────────────────────────
  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '2rem 0' }}>
      {/* Session header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: '2rem'
      }}>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span style={{
            fontFamily: 'var(--font-display)', fontSize: '0.75rem', textTransform: 'uppercase',
            letterSpacing: '0.1em', color: 'var(--color-accent)',
            padding: '0.2rem 0.6rem', border: '1px solid rgba(255,69,0,0.3)'
          }}>
            {track?.label ?? 'Interview'}
          </span>
          <span style={{ color: 'var(--color-fg-muted)', fontSize: '0.8rem' }}>
            {difficulty?.label}
          </span>
          {liveState.isFollowup && (
            <span style={{
              fontSize: '0.7rem', padding: '0.2rem 0.5rem',
              background: 'rgba(255,255,255,0.06)', border: '1px solid var(--color-border)',
              color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', letterSpacing: '0.05em'
            }}>
              FOLLOW-UP
            </span>
          )}
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>
          Q {Math.min(liveState.totalQuestionsAsked, MAX_QUESTIONS)} / {MAX_QUESTIONS}
        </div>
      </div>

      {/* Progress bar */}
      <div style={{
        height: '2px', background: 'var(--color-border)', marginBottom: '2rem', position: 'relative'
      }}>
        <div style={{
          position: 'absolute', left: 0, top: 0, height: '100%',
          width: `${progress}%`, background: 'var(--color-accent)',
          transition: 'width 0.4s ease'
        }} />
      </div>

      {/* Question panel */}
      <div className="panel" style={{ marginBottom: '2rem', position: 'relative' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          marginBottom: '1rem',
          fontFamily: 'var(--font-display)', fontSize: '0.7rem',
          textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--color-fg-muted)'
        }}>
          <Volume2 size={13} color={isSpeaking ? '#FF4500' : undefined} />
          {isSpeaking ? 'INTERVIEWER IS SPEAKING...' : 'QUESTION'}
        </div>
        <p style={{
          fontSize: 'clamp(1rem, 2.5vw, 1.2rem)', lineHeight: 1.7,
          fontFamily: 'var(--font-display)', fontWeight: 500
        }}>
          {liveState.currentQuestion}
        </p>
        {/* Replay button */}
        <button
          onClick={() => speakQuestion(liveState.currentQuestion)}
          style={{
            position: 'absolute', top: '1.25rem', right: '1.25rem',
            background: 'transparent', border: '1px solid var(--color-border)',
            padding: '0.35rem 0.6rem', cursor: 'pointer', opacity: 0.6
          }}
          title="Replay question"
        >
          <Volume2 size={14} />
        </button>
      </div>

      {/* Score flash after answer */}
      {scoreVisible && liveState.lastScore && (
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem',
          marginBottom: '1.5rem',
          animation: 'fadeIn 0.3s ease'
        }}>
          {([
            { label: 'Correctness', value: liveState.lastScore.correctness, max: 10 },
            { label: 'Clarity', value: liveState.lastScore.clarity, max: 10 },
            { label: 'Depth', value: liveState.lastScore.depth, max: 10 },
          ] as Array<{ label: string; value: number; max: number }>).map(({ label, value, max }) => (
            <div key={label} style={{
              padding: '0.875rem', background: 'rgba(255,255,255,0.03)',
              border: '1px solid var(--color-border)', textAlign: 'center'
            }}>
              <div style={{ fontFamily: 'var(--font-display)', fontSize: '1.4rem', fontWeight: 800, color: scoreColour((value / max) * 100) }}>
                {value.toFixed(1)}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {liveState.error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          padding: '0.875rem 1rem', marginBottom: '1rem',
          background: 'rgba(255,69,0,0.08)', border: '1px solid rgba(255,69,0,0.3)',
          color: 'var(--color-accent)', fontSize: '0.875rem'
        }}>
          <AlertTriangle size={15} />
          {liveState.error}
        </div>
      )}

      {/* Answer area */}
      <div className="panel" style={{ marginBottom: '1.5rem' }}>
        <textarea
          value={textAnswer}
          onChange={e => setTextAnswer(e.target.value)}
          disabled={isSubmitting || isRecording}
          placeholder={isRecording ? 'Recording... stop when done.' : 'Type your answer here, or use the microphone.'}
          rows={5}
          style={{
            width: '100%', background: 'transparent', border: 'none', outline: 'none',
            color: 'var(--color-fg)', fontFamily: 'var(--font-body)', fontSize: '0.95rem',
            lineHeight: 1.7, resize: 'vertical', minHeight: '120px',
            opacity: isSubmitting ? 0.5 : 1
          }}
        />
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' as const }}>
        {/* Mic button */}
        {!isRecording ? (
          <button
            onClick={startRecording}
            disabled={isSubmitting}
            className="btn"
            style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              opacity: isSubmitting ? 0.4 : 1
            }}
          >
            <Mic size={16} /> Record Answer
          </button>
        ) : (
          <button
            onClick={handleVoiceSubmit}
            className="btn-primary"
            style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              animation: 'pulse 1s infinite'
            }}
          >
            <StopCircle size={16} />
            Stop &amp; Submit ({recordingSeconds}s)
          </button>
        )}

        {/* Text submit */}
        <button
          onClick={() => handleSubmit()}
          disabled={!textAnswer.trim() || isSubmitting || isRecording}
          className="btn-primary"
          style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            opacity: (!textAnswer.trim() || isSubmitting) ? 0.4 : 1,
            cursor: (!textAnswer.trim() || isSubmitting) ? 'not-allowed' : 'pointer'
          }}
        >
          {isSubmitting ? (
            <>Evaluating...</>
          ) : (
            <><Send size={16} /> Submit Answer</>
          )}
        </button>

        {/* Spacer + End session */}
        <button
          onClick={handleEndSession}
          disabled={isSubmitting}
          style={{
            marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem',
            background: 'transparent', border: '1px solid var(--color-border)',
            color: 'var(--color-fg-muted)', cursor: 'pointer', padding: '0.6rem 1rem',
            fontFamily: 'var(--font-display)', fontSize: '0.75rem',
            letterSpacing: '0.05em', textTransform: 'uppercase' as const,
            opacity: isSubmitting ? 0.4 : 0.7
          }}
        >
          <XCircle size={13} /> End Interview
        </button>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
