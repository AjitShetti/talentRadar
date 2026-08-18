'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  Microphone, MicrophoneSlash, PaperPlaneRight, StopCircle, SpeakerHigh,
  CaretRight, WarningCircle, CheckCircle, XCircle
} from '@phosphor-icons/react';
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

      recorder.start(250);
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
      updatePhase('questioning');
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
      <div style={{ textAlign: 'center', padding: '4rem 0', color: 'var(--text-subtle)' }}>
        Loading interview...
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
      <div style={{ maxWidth: '640px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <div style={{ textAlign: 'center', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '2.5rem', background: 'var(--surface)' }}>
          <div style={{
            fontSize: '4rem', fontWeight: 800,
            color: scoreColour(total), lineHeight: 1
          }}>
            {total}<span style={{ fontSize: '1.25rem', color: 'var(--text-subtle)', fontWeight: 500 }}>/100</span>
          </div>
          <div style={{
            marginTop: '0.5rem', fontSize: '1rem',
            color: scoreColour(total), fontWeight: 600
          }}>
            {scoreLabel(total)}
          </div>
        </div>

        {/* Closing message */}
        <div style={{ padding: '1.25rem 1.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-subtle)', fontStyle: 'italic', color: 'var(--text-muted)' }}>
          &ldquo;{liveState.currentQuestion}&rdquo;
        </div>

        {/* Breakdown */}
        {score && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
            {([
              { label: 'Correctness', value: score.correctness },
              { label: 'Clarity', value: score.clarity },
              { label: 'Depth', value: score.depth },
            ] as Array<{ label: string; value: number }>).map(({ label, value }) => (
              <div key={label} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.25rem', textAlign: 'center', background: 'var(--surface)' }}>
                <div style={{
                  fontSize: '1.5rem', fontWeight: 700,
                  color: scoreColour(value), marginBottom: '0.25rem'
                }}>
                  {value}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {label}
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: '1rem' }}>
          <button
            onClick={() => router.push('/interview')}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff',
              border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem',
              fontWeight: 500, cursor: 'pointer'
            }}
          >
            New interview <CaretRight size={16} />
          </button>
          <button
            onClick={() => router.push('/interview/history')}
            style={{
              padding: '0.625rem 1.25rem', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', color: 'var(--text)',
              cursor: 'pointer'
            }}
          >
            View history
          </button>
        </div>
      </div>
    );
  }

  // ── Render: Active session ─────────────────────────────────────
  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Session header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between'
      }}>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <span style={{
            fontSize: '0.8125rem', fontWeight: 600, color: 'var(--accent)',
            padding: '0.2rem 0.6rem', background: 'var(--accent-subtle)', borderRadius: 'var(--radius-sm)'
          }}>
            {track?.label ?? 'Interview'}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
            {difficulty?.label}
          </span>
          {liveState.isFollowup && (
            <span style={{
              fontSize: '0.75rem', padding: '0.15rem 0.5rem',
              background: 'var(--bg-subtle)', border: '1px solid var(--border)',
              color: 'var(--text-muted)', borderRadius: 'var(--radius-sm)'
            }}>
              Follow-up
            </span>
          )}
        </div>
        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          Question {Math.min(liveState.totalQuestionsAsked, MAX_QUESTIONS)} of {MAX_QUESTIONS}
        </div>
      </div>

      {/* Progress bar */}
      <div style={{
        height: '3px', background: 'var(--border)', borderRadius: '99px', overflow: 'hidden'
      }}>
        <div style={{
          height: '100%',
          width: `${progress}%`, background: 'var(--accent)',
          transition: 'width 0.4s ease'
        }} />
      </div>

      {/* Question panel */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', background: 'var(--surface)', position: 'relative' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          marginBottom: '0.75rem', fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 500
        }}>
          <SpeakerHigh size={15} color={isSpeaking ? 'var(--accent)' : undefined} />
          {isSpeaking ? 'Interviewer is speaking...' : 'Question'}
        </div>
        <p style={{
          fontSize: '1.125rem', lineHeight: 1.6,
          fontWeight: 500, color: 'var(--text)'
        }}>
          {liveState.currentQuestion}
        </p>
        <button
          onClick={() => speakQuestion(liveState.currentQuestion)}
          style={{
            position: 'absolute', top: '1.25rem', right: '1.25rem',
            background: 'transparent', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)', padding: '0.35rem 0.5rem',
            cursor: 'pointer', color: 'var(--text-muted)'
          }}
          title="Replay audio"
        >
          <SpeakerHigh size={15} />
        </button>
      </div>

      {/* Score flash after answer */}
      {scoreVisible && liveState.lastScore && (
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem',
          animation: 'fadeIn 0.3s ease'
        }}>
          {([
            { label: 'Correctness', value: liveState.lastScore.correctness, max: 10 },
            { label: 'Clarity', value: liveState.lastScore.clarity, max: 10 },
            { label: 'Depth', value: liveState.lastScore.depth, max: 10 },
          ] as Array<{ label: string; value: number; max: number }>).map(({ label, value, max }) => (
            <div key={label} style={{
              padding: '0.875rem', background: 'var(--surface)',
              border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', textAlign: 'center'
            }}>
              <div style={{ fontSize: '1.25rem', fontWeight: 700, color: scoreColour((value / max) * 100) }}>
                {value.toFixed(1)}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
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
          padding: '0.75rem 1rem', background: 'var(--error-bg)',
          border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)',
          color: 'var(--error)', fontSize: '0.875rem'
        }}>
          <WarningCircle size={16} />
          {liveState.error}
        </div>
      )}

      {/* Answer area */}
      <div>
        <textarea
          value={textAnswer}
          onChange={e => setTextAnswer(e.target.value)}
          disabled={isSubmitting || isRecording}
          placeholder={isRecording ? 'Recording audio... click Stop & Submit when finished.' : 'Type your answer here, or click Record Answer to speak.'}
          rows={5}
          style={{ resize: 'vertical' }}
        />
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Mic button */}
        {!isRecording ? (
          <button
            onClick={startRecording}
            disabled={isSubmitting}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.5rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', color: 'var(--text)',
              cursor: isSubmitting ? 'not-allowed' : 'pointer', opacity: isSubmitting ? 0.5 : 1
            }}
          >
            <Microphone size={16} /> Record answer
          </button>
        ) : (
          <button
            onClick={handleVoiceSubmit}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.5rem 1rem', background: 'var(--error)', color: '#fff',
              border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem',
              fontWeight: 500, cursor: 'pointer'
            }}
          >
            <StopCircle size={16} />
            Stop and submit ({recordingSeconds}s)
          </button>
        )}

        {/* Text submit */}
        <button
          onClick={() => handleSubmit()}
          disabled={!textAnswer.trim() || isSubmitting || isRecording}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.5rem',
            padding: '0.5rem 1.25rem',
            background: (!textAnswer.trim() || isSubmitting) ? 'var(--border-hover)' : 'var(--accent)',
            color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)',
            fontSize: '0.875rem', fontWeight: 500,
            cursor: (!textAnswer.trim() || isSubmitting) ? 'not-allowed' : 'pointer'
          }}
        >
          {isSubmitting ? (
            'Evaluating...'
          ) : (
            <><PaperPlaneRight size={16} /> Submit answer</>
          )}
        </button>

        {/* End session */}
        <button
          onClick={handleEndSession}
          disabled={isSubmitting}
          style={{
            marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
            background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--text-muted)', cursor: 'pointer', padding: '0.5rem 0.85rem',
            borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem'
          }}
        >
          <XCircle size={15} /> End interview
        </button>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
