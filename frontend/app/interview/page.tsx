'use client'

import { FormEvent, Fragment, ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, BarChart3, Blocks, BookOpen, Clock3, Code2, CornerDownRight, Keyboard, Loader2, Mic, Play,
  RefreshCw, Send, SkipForward, StopCircle, Users, Volume2,
} from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, InterviewScore, InterviewState } from '@/lib/api'
import { runTask, useLatest, useMounted, usePersistentState, useTaskRunning } from '@/lib/persistent-state'
import {
  captionsSupported, filenameFor, isLikelyHallucination, ListenPhase, micSupported, ttsSupported,
  useCaptions, useListener, useSpeaker,
} from '@/lib/voice'

type Turn = { role: 'interviewer' | 'you'; text: string }

type Active = {
  id: string
  question: string
  index: number
  max?: number
  followup?: boolean
  state: InterviewState
  voice: boolean
  topic?: string
  label?: string
  score?: InterviewScore
  done?: boolean
  closing?: string
  finalScore?: number
}

/** One scored turn as the agent keeps it in ``agent_state.scores``. */
type ScoreRecord = {
  question_text?: string; correctness: number; clarity: number; depth: number
  tip?: string; was_followup?: boolean; unscored?: boolean
}

type SessionSummary = {
  id: string; track: string; topic?: string | null; difficulty: string
  total_score?: number; completed: boolean; created_at: string
}

type Suggestions = { for_you: string[]; recent: string[]; popular: string[] }

/** Round styles — the value is the interview_track_enum value the API stores. */
const STYLES = [
  { value: 'technical', label: 'Concepts', hint: 'How it works, trade-offs, pitfalls', Icon: BookOpen },
  { value: 'coding', label: 'Problem solving', hint: 'Talk through an approach and its cost', Icon: Code2 },
  { value: 'system_design', label: 'System design', hint: 'Architecture, scale, failure modes', Icon: Blocks },
  { value: 'behavioral', label: 'Behavioral', hint: 'Past experience, told as STAR stories', Icon: Users },
] as const

const STYLE_LABEL: Record<string, string> = Object.fromEntries(STYLES.map(s => [s.value, s.label]))

const DIFFICULTIES = [
  { value: 'beginner', label: 'Beginner' },
  { value: 'mid', label: 'Mid level' },
  { value: 'senior', label: 'Senior' },
] as const

const TOPIC_MAX = 80
const DEFAULT_MAX_QUESTIONS = 8
// Evaluation usually lands in ~2 s; past this the candidate hears something
// rather than wondering whether the interviewer froze.
const FILLER_AFTER_MS = 4000

function sessionLabel(session: { track: string; topic?: string | null }): string {
  return session.topic || session.track.replace(/_/g, ' ')
}

const average = (s: { correctness: number; clarity: number; depth: number }) => (s.correctness + s.clarity + s.depth) / 3

/** Questions carry `backticks` around identifiers; show those as code, not as raw ticks. */
function withCode(text: string): ReactNode {
  return text.split(/(`[^`]+`)/g).map((part, i) => part.length > 2 && part.startsWith('`') && part.endsWith('`')
    ? <code key={i}>{part.slice(1, -1)}</code>
    : <Fragment key={i}>{part}</Fragment>)
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
  listening: 'Listening — take your time',
  transcribing: 'Writing down what you said',
  evaluating: 'Your interviewer is thinking',
  typing: 'Type your answer instead',
  complete: 'Interview complete',
}

const START_TASK = 'interview.start'
const ANSWER_TASK = 'interview.answer'
const END_TASK = 'interview.end'

const RETRY_PROMPT ="Sorry, I didn't catch that. Could you say it again?"
const SILENCE_PROMPT = "Take your time. Whenever you're ready."

export default function InterviewPage() {
  const [topic, setTopic] = usePersistentState('interview.topic', '')
  const [style, setStyle] = usePersistentState('interview.style', 'technical')
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)
  const [difficulty, setDifficulty] = usePersistentState('interview.difficulty', 'mid')
  const [mode, setMode] = usePersistentState<'voice' | 'text'>('interview.mode', 'voice')
  const [active, setActive] = usePersistentState<Active | null>('interview.active', null)
  const [log, setLog] = usePersistentState<Turn[]>('interview.log', [])
  const [answer, setAnswer] = usePersistentState('interview.answer', '')
  const [history, setHistory] = useState<SessionSummary[]>([])
  // Starting, answering and ending are server round-trips that can outlast a
  // visit to another page. As tasks they still update the stored session when
  // they land, and a remounted page shows them as in progress meanwhile.
  const starting = useTaskRunning(START_TASK)
  const answering = useTaskRunning(ANSWER_TASK)
  const ending = useTaskRunning(END_TASK)
  const loading = starting || answering
  const [error, setError] = usePersistentState('interview.error', '')
  const [notice, setNotice] = useState('')
  const [stage, setStage] = useState<Stage>('paused')
  const mounted = useMounted()

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
    api.interview.topics().then(setSuggestions).catch(() => setSuggestions({ for_you: [], recent: [], popular: [] }))
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
    api.interview.topics().then(setSuggestions).catch(() => {})
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
    const result = await runTask(ANSWER_TASK, () => api.interview.answer(session.id, text, session.state))
    sessionRef.current = { id: session.id, state: result.agent_state }
    setActive(previous => previous && {
      ...previous,
      question: result.question,
      index: result.question_index,
      max: result.max_questions,
      followup: result.is_followup,
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
    let silences = 0

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

      // Nothing was said. Never upload it: Whisper turns silence into
      // "Thank you." and that used to be scored as the answer.
      if (!turn.hadSpeech && turn.reason !== 'manual') {
        silences += 1
        if (silences >= 2) {
          setNotice('Paused while you think. Press “Answer by voice” when you are ready, or type your answer.')
          setStage('paused')
          return
        }
        toSpeak = SILENCE_PROMPT
        continue
      }
      silences = 0

      setStage('transcribing')
      let said = await resolveTranscript(turn.blob, turn.mime, live)
      if (!alive()) return
      if (isLikelyHallucination(said, turn.speechMs)) said = ''

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

      const filler: { speaking: Promise<void> | null } = { speaking: null }
      const fillerTimer = setTimeout(() => {
        if (alive()) filler.speaking = speakerRef.current.speak('Give me a second.')
      }, FILLER_AFTER_MS)

      let result
      try {
        result = await sendAnswer(said)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not submit your answer.')
        setStage('paused')
        return
      } finally {
        clearTimeout(fillerTimer)
      }
      // Let the filler finish rather than cutting it off mid-word.
      if (filler.speaking) await filler.speaking
      if (!alive() || !result) return

      if (result.session_complete) {
        setStage('complete')
        await speakerRef.current.speak([result.score?.verbal_ack, result.question].filter(Boolean).join(' '))
        return
      }

      // The reaction rides in on the evaluation call and the transition is
      // picked server-side once the follow-up decision is known.
      toSpeak = [result.score?.verbal_ack, result.question].filter(Boolean).join(' ')
    }
  }, [speakerRef, listenerRef, captionsRef, resolveTranscript, appendLog, sendAnswer])

  async function start(event?: FormEvent) {
    event?.preventDefault()
    const subject = topic.trim()
    if (subject.length < 2) { setError('Tell us what you want to be interviewed on.'); return }
    setError(''); setNotice('')
    const voice = mode === 'voice' && voiceReady
    let opening = ''
    try {
      const session = await runTask(START_TASK, () => api.interview.start(style, subject, difficulty, voice))
      sessionRef.current = { id: session.session_id, state: session.agent_state }
      setActive({
        id: session.session_id, question: session.question,
        index: session.question_index, max: session.max_questions, followup: false,
        state: session.agent_state, voice, topic: subject,
        label: `${subject} · ${STYLE_LABEL[style] ?? style}`,
      })
      setLog([{ role: 'interviewer', text: session.question }])
      setAnswer('')
      setStage(voice ? 'speaking' : 'typing')
      if (voice) {
        opening = `Hi, thanks for making the time. We'll do about ${session.max_questions} questions on ${subject}. `
          + `Pausing to think is completely fine. When you've finished an answer, just stop talking. First question. ${session.question}`
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the interview.')
      return
    }
    // Outside the try: a failure inside the interview is not a start failure,
    // and the loop reports its own errors. If the user left while the first
    // question was on its way, the session is stored and waits, paused, for
    // them to come back — never speak from a page that is not on screen.
    if (opening && mounted.current) await runVoiceLoop(opening)
  }

  /** Typed submit — used by text mode and by the voice loop's typing fallback. */
  async function submitTyped(event: FormEvent) {
    event.preventDefault()
    if (!active || !answer.trim() || loading) return
    setError(''); setNotice('')
    const text = answer.trim()
    let result
    try {
      appendLog('you', text)
      setAnswer('')
      result = await sendAnswer(text)
    } catch (err) {
      // Put the answer back so a failed submit never costs the user what they wrote.
      setAnswer(current => current || text)
      setError(err instanceof Error ? err.message : 'Could not submit your answer.')
      return
    }
    if (result?.session_complete) { setStage('complete'); return }
    if (active.voice && result && mounted.current) {
      await runVoiceLoop([result.score?.verbal_ack, result.question].filter(Boolean).join(' '))
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
    try {
      const result = await runTask(END_TASK, () => api.interview.end(active.id, active.state))
      setActive(previous => previous && previous.id === active.id
        ? { ...previous, done: true, closing: result.closing_message, finalScore: result.final_score.total_score }
        : previous)
      setStage('complete')
      refreshHistory()
      if (active.voice && mounted.current) speaker.speak(result.closing_message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not end session.')
    }
  }

  function reset(keepTopic: boolean) {
    stopEverything()
    if (!keepTopic) setTopic('')
    setActive(null); setAnswer(''); setLog([]); setNotice(''); setError('')
    setStage('paused')
    sessionRef.current = null
  }

  // The loop's stage is per visit; an answer still being evaluated is not.
  const shownStage: Stage = answering ? 'evaluating' : stage
  const busy = shownStage === 'transcribing' || shownStage === 'evaluating'
  const liveVoice = Boolean(active?.voice) && !active?.done
  const max = active?.max ?? DEFAULT_MAX_QUESTIONS
  const scores = ((active?.state?.scores as ScoreRecord[] | undefined) ?? [])

  return <RequireAuth><AppShell narrow>
    <section className="page-heading">
      <div>
        <span className="board-kicker">LangGraph interview lab</span>
        <h1>Practice the conversation before it counts<span>.</span></h1>
        <p>Pick any subject. Your interviewer asks, listens, probes the weak spots, and tells you what to sharpen.</p>
      </div>
      {history.length > 0 && <div className="tracker-total"><strong><FlapText value={history.length} /></strong><span>sessions logged</span></div>}
    </section>

    {error && <p className="form-error">{error}</p>}
    {notice && <p className="voice-notice">{notice}</p>}

    {!active ? <form className="interview-start" onSubmit={start}>
      <label className="topic-field">
        <span className="topic-label">What do you want to be interviewed on?</span>
        <input
          value={topic}
          maxLength={TOPIC_MAX}
          onChange={event => setTopic(event.target.value)}
          placeholder="Any skill, tool or role — React hooks, Kafka, product management…"
          autoComplete="off"
        />
      </label>
      <TopicChips suggestions={suggestions} current={topic} onPick={setTopic} />

      <p className="topic-label">Round style</p>
      <div className="style-picker" role="radiogroup" aria-label="Round style">
        {STYLES.map(({ value, label, hint, Icon }) => <button
          key={value}
          type="button"
          role="radio"
          aria-checked={style === value}
          className={style === value ? 'mode-card selected' : 'mode-card'}
          onClick={() => setStyle(value)}
        >
          <Icon size={17} />
          <strong>{label}</strong>
          <span>{hint}</span>
        </button>)}
      </div>

      <p className="topic-label">Difficulty</p>
      <div className="segmented" role="radiogroup" aria-label="Difficulty">
        {DIFFICULTIES.map(({ value, label }) => <button
          key={value}
          type="button"
          role="radio"
          aria-checked={difficulty === value}
          className={difficulty === value ? 'selected' : undefined}
          onClick={() => setDifficulty(value)}
        >{label}</button>)}
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

      <button type="submit" className="primary-button" disabled={loading || topic.trim().length < 2}>
        {loading ? 'Preparing your first question…' : <>Start interview <ArrowRight size={16} /></>}
      </button>
      {mode === 'voice' && voiceReady && <p className="voice-hint">
        Your browser will ask for microphone access. Headphones keep the interviewer&apos;s voice out of your answer.
      </p>}
    </form> : <section className="interview-session">
      <div className="interview-session-head">
        <div>
          <p className="eyebrow">{active.label ?? 'Mock interview'}{active.voice ? ' · VOICE' : ''}</p>
          <h2>{active.done
            ? 'Session complete'
            // question_index already points past the question a follow-up probes.
            : <>Question {Math.min(active.followup ? Math.max(active.index, 1) : active.index + 1, max)} <small>of {max}</small>{active.followup && <span className="followup-badge"><CornerDownRight size={12} />Follow-up</span>}</>}
          </h2>
        </div>
        {!active.done && <button className="outline-button danger-outline" onClick={end} disabled={ending}>
          <StopCircle size={15} />{ending ? 'Ending…' : 'End session'}
        </button>}
      </div>

      {!active.done && <div className="interview-progress" aria-hidden="true">
        {Array.from({ length: max }, (_, i) => <span key={i} className={i < active.index ? 'done' : i === active.index ? 'current' : undefined} />)}
      </div>}

      {active.done ? <Results
        closing={active.closing || active.question}
        scores={scores}
        finalScore={active.finalScore}
        topic={active.topic}
        onAgain={() => reset(true)}
        onNew={() => reset(false)}
      /> : <>
        {active.score && <LastAnswer score={active.score} />}

        {liveVoice && <VoiceStage
          stage={shownStage}
          phase={listener.phase}
          level={listener.level}
          caption={captions.text}
          onSkipSpeech={() => speaker.stop()}
          onDone={() => listener.submitNow()}
          onType={() => { listener.abort(); speaker.stop(); setStage('typing') }}
          onResume={resumeVoice}
          onRepeat={() => { stopEverything(); resumeVoice() }}
        />}

        <div className="question-card">
          <p>{withCode(active.question)}</p>
        </div>

        {(!liveVoice || shownStage === 'typing') && <form onSubmit={submitTyped} className="answer-form">
          <textarea
            value={answer}
            onChange={event => setAnswer(event.target.value)}
            placeholder="Think out loud. Explain your approach, decisions, and trade-offs…"
          />
          <button className="primary-button" disabled={loading || busy}>
            {loading || busy ? 'Evaluating…' : <>Submit answer <Send size={15} /></>}
          </button>
        </form>}
      </>}

      {log.length > 1 && <details className="voice-transcript" open={active.done}>
        <summary className="eyebrow">TRANSCRIPT · {log.length} turns</summary>
        {log.map((turn, index) => <div key={index} className={`vt-turn vt-${turn.role}`}>
          <span>{turn.role === 'you' ? 'You' : 'Interviewer'}</span>
          <p>{turn.text.replace(/`/g, '')}</p>
        </div>)}
      </details>}
    </section>}

    <section className="history-section">
      <div className="panel-heading">
        <div><p className="eyebrow">RECENT PRACTICE</p><h2>Interview history</h2></div>
        <Clock3 size={17} />
      </div>
      {history.length ? <div className="history-list">{history.map(session => <div key={session.id}>
        <span className={session.topic ? 'history-topic' : undefined}>{sessionLabel(session)}</span>
        <strong>{session.total_score != null ? `${Math.round(session.total_score)}/100` : 'In progress'}</strong>
        <small>{session.topic && STYLE_LABEL[session.track] ? `${STYLE_LABEL[session.track]} · ` : ''}{session.difficulty} · {new Date(session.created_at).toLocaleDateString()}</small>
      </div>)}</div> : <p className="muted-copy">Your completed sessions will appear here.</p>}
    </section>
  </AppShell></RequireAuth>
}

/** Feedback on the answer just given: the score, and the one thing to sharpen. */
function LastAnswer({ score }: { score: InterviewScore }) {
  if (score.scored === false) {
    return <div className="score-feedback score-unscored">
      <strong>That answer wasn&apos;t scored</strong>
      <span>The scoring service was busy. It won&apos;t count against you — carry on.</span>
    </div>
  }
  return <div className="score-feedback">
    <div className="score-line">
      <strong>Last answer {average(score).toFixed(1)}<small>/10</small></strong>
      <span>Correctness {score.correctness} · Clarity {score.clarity} · Depth {score.depth}</span>
    </div>
    {score.tip && <p className="score-tip">{withCode(score.tip)}</p>}
  </div>
}

/** End-of-session breakdown — what the closing message used to promise on a page that did not exist. */
function Results({ closing, scores, finalScore, topic, onAgain, onNew }: {
  closing: string
  scores: ScoreRecord[]
  finalScore?: number
  topic?: string
  onAgain: () => void
  onNew: () => void
}) {
  const scored = scores.filter(s => !s.unscored)
  const total = finalScore ?? (scored.length ? (scored.reduce((sum, s) => sum + average(s), 0) / scored.length) * 10 : 0)
  const dims = (['correctness', 'clarity', 'depth'] as const).map(key => ({
    key,
    value: scored.length ? (scored.reduce((sum, s) => sum + s[key], 0) / scored.length) * 10 : 0,
  }))
  const weakest = scored.length ? dims.reduce((low, d) => (d.value < low.value ? d : low)) : null

  return <div className="interview-results">
    <p className="results-closing">{closing.replace(/`/g, '')}</p>
    {scored.length > 0 && <>
      <div className="results-head">
        <div className="results-total"><strong>{Math.round(total)}</strong><span>/100 overall</span></div>
        <div className="results-dims">
          {dims.map(d => <div key={d.key} className={weakest?.key === d.key ? 'weakest' : undefined}>
            <span>{d.key}{weakest?.key === d.key ? ' · work on this' : ''}</span>
            <i><b style={{ width: `${Math.round(d.value)}%` }} /></i>
            <em>{Math.round(d.value)}</em>
          </div>)}
        </div>
      </div>
      <ol className="results-answers">
        {scores.map((s, i) => <li key={i}>
          <div>
            <p>{s.was_followup && <span className="followup-badge"><CornerDownRight size={11} />Follow-up</span>}{withCode(s.question_text || 'Question')}</p>
            {s.tip && <small>{withCode(s.tip)}</small>}
          </div>
          <strong>{s.unscored ? '—' : average(s).toFixed(1)}</strong>
        </li>)}
      </ol>
    </>}
    <div className="results-actions">
      {topic && <button className="primary-button" onClick={onAgain}><RefreshCw size={15} /> Practice {topic} again</button>}
      <button className="outline-button" onClick={onNew}>Choose a new topic</button>
    </div>
  </div>
}

/** Suggestion rows under the topic box. Picking one fills the box; typing is always allowed. */
function TopicChips({ suggestions, current, onPick }: {
  suggestions: Suggestions | null
  current: string
  onPick: (topic: string) => void
}) {
  if (!suggestions) return <div className="topic-chips" aria-hidden="true" />
  const rows: Array<[string, string[]]> = [
    ['For you', suggestions.for_you],
    ['Recent', suggestions.recent],
    ['Popular', suggestions.popular],
  ]
  const chosen = current.trim().toLowerCase()
  return <div className="topic-chips">
    {rows.filter(([, topics]) => topics.length).map(([title, topics]) => <div key={title} className="topic-chip-row">
      <span>{title}</span>
      <div>{topics.map(topic => <button
        key={topic}
        type="button"
        className={topic.toLowerCase() === chosen ? 'topic-chip selected' : 'topic-chip'}
        aria-pressed={topic.toLowerCase() === chosen}
        onClick={() => onPick(topic)}
      >{topic}</button>)}</div>
    </div>)}
  </div>
}

/** The live voice panel: who is talking, how loud, and how to take over. */
function VoiceStage({ stage, phase, level, caption, onSkipSpeech, onDone, onType, onResume, onRepeat }: {
  stage: Stage
  phase: ListenPhase
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
  // The detector flips to 'pausing' in every gap between words; only a pause
  // that lasts is worth telling the candidate about, or the status flickers.
  const [longPause, setLongPause] = useState(false)
  useEffect(() => {
    if (phase !== 'pausing') { setLongPause(false); return }
    const timer = setTimeout(() => setLongPause(true), 1200)
    return () => clearTimeout(timer)
  }, [phase])
  const status = stage === 'listening' && longPause
    ? 'Still listening — keep going, or stay quiet to finish'
    : STAGE_LABEL[stage]
  return <div className="voice-stage" data-stage={stage} data-phase={phase}>
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

    <p className="voice-status" aria-live="polite">{status}</p>
    {stage === 'listening' && <p className="voice-caption">{caption || 'Start whenever you are ready — pausing to think is fine.'}</p>}

    <div className="voice-controls">
      {stage === 'speaking' && <button type="button" className="outline-button" onClick={onSkipSpeech}>
        <SkipForward size={14} /> Skip to answering
      </button>}
      {stage === 'listening' && <>
        <button type="button" className="primary-button" onClick={onDone}>
          <Send size={14} /> I&apos;m done answering
        </button>
        <button type="button" className="outline-button" onClick={onRepeat}>
          <RefreshCw size={14} /> Repeat question
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
