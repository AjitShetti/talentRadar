'use client'

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, BarChart3, Clock3, Keyboard, Loader2, Mic, Play, RefreshCw,
  Send, SkipForward, StopCircle, Volume2,
} from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, InterviewScore, InterviewState } from '@/lib/api'
import { usePersistentState, useLatest } from '@/lib/persistent-state'
import {
  captionsSupported, filenameFor, micSupported, ttsSupported,
  useCaptions, useListener, useSpeaker,
} from '@/lib/voice'

type Turn = { role: 'interviewer' | 'you'; text: string }

type Active = {
  id: string
  question: string
  index: number
  state: InterviewState
  voice: boolean
  score?: InterviewScore
  done?: boolean
  closing?: string
}

type SessionSummary = {
  id: string; track: string; difficulty: string
  total_score?: number; completed: boolean; created_at: string
}

/**
 * Where the voice loop currently is.
 *
 * 'paused' is the resting state that always needs a click to leave — either
 * because a persisted session was restored (browsers refuse to speak or open
 * a mic without a fresh user gesture) or because a turn errored out.
 */
type Stage = 'paused' | 'speaking' | 'listening' | 'transcribing' | 'evaluating' | 'typing' | 'complete'

const STAGE_LABEL: Record<Stage, string> = {
  paused: 'Paused',
  speaking: 'Your interviewer is speaking',
  listening: 'Listening — just answer out loud',
  transcribing: 'Writing down what you said',
  evaluating: 'Your interviewer is thinking',
  typing: 'Type your answer instead',
  complete: 'Interview complete',
}

const RETRY_PROMPT = "Sorry, I didn't catch that. Could you say it again?"

export default function InterviewPage() {
  const [track, setTrack] = usePersistentState('interview.track', 'python_backend')
  const [difficulty, setDifficulty] = usePersistentState('interview.difficulty', 'mid')
  const [mode, setMode] = usePersistentState<'voice' | 'text'>('interview.mode', 'voice')
  const [active, setActive] = usePersistentState<Active | null>('interview.active', null)
  const [log, setLog] = usePersistentState<Turn[]>('interview.log', [])
  const [answer, setAnswer] = usePersistentState('interview.answer', '')
  const [history, setHistory] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [ending, setEnding] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [stage, setStage] = useState<Stage>('paused')

  const speaker = useSpeaker()
  const listener = useListener()
  const captions = useCaptions()
  const speakerRef = useLatest(speaker)
  const listenerRef = useLatest(listener)
  const captionsRef = useLatest(captions)

  // The voice loop is a long-running async function, so everything it reads
  // lives in refs — state captured in its closure would be a turn stale.
  const sessionRef = useRef<{ id: string; state: InterviewState } | null>(null)
  const runRef = useRef(0)

  const voiceReady = micSupported() && ttsSupported()

  useEffect(() => {
    api.interview.history().then(result => setHistory(result.sessions)).catch(() => {})
  }, [])

  // A restored session cannot resume on its own: audio needs a user gesture.
  useEffect(() => {
    if (active && !active.done) sessionRef.current = { id: active.id, state: active.state }
  }, [active])

  const stopEverything = useCallback(() => {
    runRef.current += 1
    speakerRef.current.stop()
    listenerRef.current.abort()
    captionsRef.current.stop()
  }, [speakerRef, listenerRef, captionsRef])

  useEffect(() => () => stopEverything(), [stopEverything])

  const appendLog = useCallback((role: Turn['role'], text: string) => {
    if (text.trim()) setLog(previous => [...previous, { role, text: text.trim() }])
  }, [setLog])

  const refreshHistory = useCallback(() => {
    api.interview.history().then(result => setHistory(result.sessions)).catch(() => {})
  }, [])

  /**
   * Whisper owns the transcript; the browser's live captions are the fallback
   * for when it is rate-limited (provider='browser_fallback') or the clip is
   * too short to be worth uploading.
   */
  const resolveTranscript = useCallback(async (
    blob: Blob | null, mime: string, live: string,
  ): Promise<string> => {
    if (!blob || blob.size < 1200) return live
    try {
      const result = await api.interview.transcribe(blob, filenameFor(mime))
      if (result.provider !== 'browser_fallback' && result.transcript.trim()) {
        return result.transcript.trim()
      }
    } catch { /* captions below */ }
    return live
  }, [])

  /** Send one answer to the LangGraph agent and fold the response into state. */
  const sendAnswer = useCallback(async (text: string) => {
    const session = sessionRef.current
    if (!session) return null
    const result = await api.interview.answer(session.id, text, session.state)
    sessionRef.current = { id: session.id, state: result.agent_state }
    setActive(previous => previous && {
      ...previous,
      question: result.question,
      index: result.question_index,
      state: result.agent_state,
      score: result.score,
      done: result.session_complete,
      closing: result.session_complete ? result.question : undefined,
    })
    appendLog('interviewer', result.question)
    if (result.session_complete) refreshHistory()
    return result
  }, [setActive, appendLog, refreshHistory])

  /**
   * The spoken interview itself: speak, listen, transcribe, evaluate, repeat.
   * The agent graph decides *what* is said — this only carries it in and out.
   */
  const runVoiceLoop = useCallback(async (opening: string) => {
    const token = ++runRef.current
    const alive = () => token === runRef.current
    let toSpeak = opening
    let misses = 0

    while (alive()) {
      setStage('speaking')
      await speakerRef.current.speak(toSpeak)
      if (!alive()) return

      setStage('listening')
      captionsRef.current.start()
      const turn = await listenerRef.current.listen()
      const live = captionsRef.current.stop()
      if (!alive() || turn.reason === 'aborted') return

      if (turn.reason === 'error') {
        setNotice('I could not reach your microphone. You can allow access and resume, or type your answer.')
        setStage('typing')
        return
      }

      setStage('transcribing')
      const said = await resolveTranscript(turn.blob, turn.mime, live)
      if (!alive()) return

      if (!said) {
        misses += 1
        if (misses >= 2) {
          setNotice('I still could not hear you — type this answer and we will carry on by voice afterwards.')
          setStage('typing')
          return
        }
        toSpeak = RETRY_PROMPT
        continue
      }

      misses = 0
      appendLog('you', said)
      setStage('evaluating')

      let result
      try {
        result = await sendAnswer(said)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not submit your answer.')
        setStage('paused')
        return
      }
      if (!alive() || !result) return

      if (result.session_complete) {
        setStage('complete')
        await speakerRef.current.speak(result.question)
        return
      }

      // The acknowledgement rides in on the same evaluation call, so the
      // interviewer reacts before asking — no extra round-trip, no dead air.
      toSpeak = [result.score?.verbal_ack, result.question].filter(Boolean).join(' ')
    }
  }, [speakerRef, listenerRef, captionsRef, resolveTranscript, appendLog, sendAnswer])

  async function start() {
    setLoading(true); setError(''); setNotice('')
    const voice = mode === 'voice' && voiceReady
    let opening = ''
    try {
      const session = await api.interview.start(track, difficulty, voice)
      sessionRef.current = { id: session.session_id, state: session.agent_state }
      setActive({
        id: session.session_id, question: session.question,
        index: session.question_index, state: session.agent_state, voice,
      })
      setLog([{ role: 'interviewer', text: session.question }])
      setAnswer('')
      setStage(voice ? 'speaking' : 'typing')
      if (voice) opening = `Hi, thanks for making the time. Let's get started. ${session.question}`
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the interview.')
      return
    } finally {
      setLoading(false)
    }
    // Outside the try: a failure inside the interview is not a start failure,
    // and the loop reports its own errors.
    if (opening) await runVoiceLoop(opening)
  }

  /** Typed submit — used by text mode and by the voice loop's typing fallback. */
  async function submitTyped(event: FormEvent) {
    event.preventDefault()
    if (!active || !answer.trim()) return
    setLoading(true); setError(''); setNotice('')
    const text = answer.trim()
    try {
      appendLog('you', text)
      setAnswer('')
      const result = await sendAnswer(text)
      if (active.voice && result && !result.session_complete) {
        setLoading(false)
        await runVoiceLoop([result.score?.verbal_ack, result.question].filter(Boolean).join(' '))
        return
      }
      if (result?.session_complete) setStage('complete')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not submit your answer.')
    } finally {
      setLoading(false)
    }
  }

  /** Resume a restored or errored voice session from the current question. */
  async function resumeVoice() {
    if (!active) return
    setNotice('')
    await runVoiceLoop(active.question)
  }

  async function end() {
    if (!active) return
    stopEverything()
    setEnding(true)
    try {
      const result = await api.interview.end(active.id, active.state)
      const closing = `${result.closing_message} Final score: ${Math.round(result.final_score.total_score)}/100.`
      setActive({ ...active, done: true, closing })
      setStage('complete')
      refreshHistory()
      if (active.voice) speaker.speak(result.closing_message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not end session.')
    } finally {
      setEnding(false)
    }
  }

  function reset() {
    stopEverything()
    setActive(null); setAnswer(''); setLog([]); setNotice(''); setError('')
    setStage('paused')
    sessionRef.current = null
  }

  const busy = stage === 'transcribing' || stage === 'evaluating'
  const liveVoice = Boolean(active?.voice) && !active?.done

  return <RequireAuth><AppShell narrow>
    <section className="page-heading">
      <div>
        <span className="board-kicker">LangGraph interview lab</span>
        <h1>Practice the conversation before it counts<span>.</span></h1>
        <p>Answer out loud and your interviewer listens, reacts, and probes — the same agent that scores every response.</p>
      </div>
      {history.length > 0 && <div className="tracker-total"><strong><FlapText value={history.length} /></strong><span>sessions logged</span></div>}
    </section>

    {error && <p className="form-error">{error}</p>}
    {notice && <p className="voice-notice">{notice}</p>}

    {!active ? <div className="interview-start">
      <div className="interview-orb"><BarChart3 size={30} /></div>
      <h2>Build a focused mock interview</h2>
      <div className="select-grid">
        <label>Track
          <select value={track} onChange={event => setTrack(event.target.value)}>
            <option value="python_dsa">Python &amp; DSA</option>
            <option value="python_backend">Python backend</option>
            <option value="sql">SQL</option>
            <option value="system_design">System design</option>
          </select>
        </label>
        <label>Difficulty
          <select value={difficulty} onChange={event => setDifficulty(event.target.value)}>
            <option value="beginner">Beginner</option>
            <option value="mid">Mid level</option>
            <option value="senior">Senior</option>
          </select>
        </label>
      </div>

      <div className="mode-picker">
        <button
          type="button"
          className={mode === 'voice' ? 'mode-card selected' : 'mode-card'}
          onClick={() => setMode('voice')}
          disabled={!voiceReady}
        >
          <Mic size={17} />
          <strong>Voice interview</strong>
          <span>{voiceReady
            ? 'Hands-free. It asks, you answer out loud, it follows up.'
            : 'Needs a browser with microphone and speech support.'}</span>
        </button>
        <button
          type="button"
          className={mode === 'text' || !voiceReady ? 'mode-card selected' : 'mode-card'}
          onClick={() => setMode('text')}
        >
          <Keyboard size={17} />
          <strong>Typed interview</strong>
          <span>Write your answers at your own pace.</span>
        </button>
      </div>

      <button className="primary-button" onClick={start} disabled={loading}>
        {loading ? 'Preparing your first question…' : <>Start interview <ArrowRight size={16} /></>}
      </button>
      {mode === 'voice' && voiceReady && <p className="voice-hint">
        Your browser will ask for microphone access. Headphones keep the interviewer&apos;s voice out of your answer.
      </p>}
    </div> : <section className="interview-session">
      <div className="interview-session-head">
        <div>
          <p className="eyebrow">QUESTION {active.index + 1} · {track.replace('_', ' ')}{active.voice ? ' · VOICE' : ''}</p>
          <h2>{active.done ? 'Session complete' : 'Your interviewer asks'}</h2>
        </div>
        {!active.done && <button className="outline-button danger-outline" onClick={end} disabled={ending}>
          <StopCircle size={15} />{ending ? 'Ending…' : 'End session'}
        </button>}
      </div>

      {active.score && <div className="score-feedback">
        <strong>Your last answer: {((active.score.correctness + active.score.clarity + active.score.depth) / 3).toFixed(1)}/10</strong>
        <span>{active.score.answer_summary || `Correctness ${active.score.correctness} · Clarity ${active.score.clarity} · Depth ${active.score.depth}`}</span>
      </div>}

      {liveVoice && <VoiceStage
        stage={stage}
        level={listener.level}
        caption={captions.text}
        onSkipSpeech={() => speaker.stop()}
        onDone={() => listener.submitNow()}
        onType={() => { listener.abort(); speaker.stop(); setStage('typing') }}
        onResume={resumeVoice}
        onRepeat={() => { stopEverything(); resumeVoice() }}
      />}

      <div className="question-card">
        <p>{active.done ? active.closing || active.question : active.question}</p>
      </div>

      {!active.done && (!liveVoice || stage === 'typing') && <form onSubmit={submitTyped} className="answer-form">
        <textarea
          value={answer}
          onChange={event => setAnswer(event.target.value)}
          placeholder="Think out loud. Explain your approach, decisions, and trade-offs…"
        />
        <button className="primary-button" disabled={loading || busy}>
          {loading || busy ? 'Evaluating…' : <>Submit answer <Send size={15} /></>}
        </button>
      </form>}

      {active.done && <button className="primary-button" onClick={reset}>Start another session</button>}

      {log.length > 1 && <div className="voice-transcript">
        <p className="eyebrow">TRANSCRIPT</p>
        {log.map((turn, index) => <div key={index} className={`vt-turn vt-${turn.role}`}>
          <span>{turn.role === 'you' ? 'You' : 'Interviewer'}</span>
          <p>{turn.text}</p>
        </div>)}
      </div>}
    </section>}

    <section className="history-section">
      <div className="panel-heading">
        <div><p className="eyebrow">RECENT PRACTICE</p><h2>Interview history</h2></div>
        <Clock3 size={17} />
      </div>
      {history.length ? <div className="history-list">{history.map(session => <div key={session.id}>
        <span>{session.track.replace('_', ' ')}</span>
        <strong>{session.total_score != null ? `${Math.round(session.total_score)}/100` : 'In progress'}</strong>
        <small>{session.difficulty} · {new Date(session.created_at).toLocaleDateString()}</small>
      </div>)}</div> : <p className="muted-copy">Your completed sessions will appear here.</p>}
    </section>
  </AppShell></RequireAuth>
}

/** The live voice panel: who is talking, how loud, and how to take over. */
function VoiceStage({ stage, level, caption, onSkipSpeech, onDone, onType, onResume, onRepeat }: {
  stage: Stage
  level: number
  caption: string
  onSkipSpeech: () => void
  onDone: () => void
  onType: () => void
  onResume: () => void
  onRepeat: () => void
}) {
  // The ring tracks mic level while listening and simply breathes otherwise.
  const scale = stage === 'listening' ? 1 + Math.min(level, 1) * 0.55 : 1
  return <div className="voice-stage" data-stage={stage}>
    <div className="voice-ring">
      <span className="voice-pulse" style={{ transform: `scale(${scale})` }} />
      <span className="voice-core">
        {stage === 'speaking' && <Volume2 size={22} />}
        {stage === 'listening' && <Mic size={22} />}
        {(stage === 'transcribing' || stage === 'evaluating') && <Loader2 size={22} className="spin" />}
        {(stage === 'paused' || stage === 'typing') && <Play size={22} />}
        {stage === 'complete' && <BarChart3 size={22} />}
      </span>
    </div>

    <p className="voice-status">{STAGE_LABEL[stage]}</p>
    {stage === 'listening' && <p className="voice-caption">{caption || 'Take a breath and start whenever you are ready…'}</p>}

    <div className="voice-controls">
      {stage === 'speaking' && <button type="button" className="outline-button" onClick={onSkipSpeech}>
        <SkipForward size={14} /> Skip to answering
      </button>}
      {stage === 'listening' && <>
        <button type="button" className="primary-button" onClick={onDone}>
          <Send size={14} /> I&apos;m done answering
        </button>
        <button type="button" className="outline-button" onClick={onType}>
          <Keyboard size={14} /> Type instead
        </button>
      </>}
      {(stage === 'paused' || stage === 'typing') && <>
        <button type="button" className="outline-button" onClick={onResume}>
          <Mic size={14} /> Answer by voice
        </button>
        <button type="button" className="outline-button" onClick={onRepeat}>
          <RefreshCw size={14} /> Repeat the question
        </button>
      </>}
    </div>

    {!captionsSupported() && stage === 'listening' && <p className="voice-hint">
      Live captions are not available in this browser — your answer is still recorded and transcribed.
    </p>}
  </div>
}
