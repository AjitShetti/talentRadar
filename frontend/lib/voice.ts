'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Browser voice primitives for the interview lab.
 *
 * The interview agent itself stays turn-based and stateless (see
 * agents/interview/graph.py) — this module is purely the I/O layer that turns
 * a turn into something you can hear and answer out loud:
 *
 *   speak(question)  →  listen()  →  blob → Whisper  →  submit answer  →  repeat
 *
 * Two transcription paths run at once. MediaRecorder audio goes to Groq
 * Whisper for the authoritative transcript; the browser's SpeechRecognition
 * runs alongside it purely for live captions, and doubles as the fallback
 * transcript when Whisper is rate-limited (provider="browser_fallback").
 */

/* ------------------------------------------------------------------ *
 * Text to speech — the interviewer's voice
 * ------------------------------------------------------------------ */

export function ttsSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

// Chrome stalls its utterance queue on long passages, so questions are spoken
// as sentence-sized chunks rather than one blob of text.
const MAX_CHUNK_CHARS = 170

function intoChunks(text: string): string[] {
  const sentences = text.replace(/\s+/g, ' ').trim().match(/[^.!?]+[.!?]*\s*/g) || [text]
  const chunks: string[] = []
  let buffer = ''
  for (const sentence of sentences) {
    if (buffer && (buffer + sentence).length > MAX_CHUNK_CHARS) { chunks.push(buffer.trim()); buffer = sentence }
    else buffer += sentence
  }
  if (buffer.trim()) chunks.push(buffer.trim())
  return chunks
}

// Ranked voice preferences. Indian English leads because TalentRadar is
// India-only; the rest are fallbacks for whatever the OS actually ships.
const VOICE_PREFERENCE: RegExp[] = [
  /en[-_]IN/i, /Natural/i, /Google UK English/i, /Google US English/i, /en[-_]GB/i, /en[-_]US/i, /^en/i,
]

function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const english = voices.filter(v => /^en/i.test(v.lang))
  const pool = english.length ? english : voices
  for (const preference of VOICE_PREFERENCE) {
    const match = pool.find(v => preference.test(v.lang) || preference.test(v.name))
    if (match) return match
  }
  return pool[0] || null
}

export type Speaker = {
  speak: (text: string) => Promise<void>
  stop: () => void
  speaking: boolean
  voiceName: string | null
  supported: boolean
}

export function useSpeaker(): Speaker {
  const [speaking, setSpeaking] = useState(false)
  const [voice, setVoice] = useState<SpeechSynthesisVoice | null>(null)
  const cancelled = useRef(false)
  const keepAlive = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!ttsSupported()) return
    // Voices load asynchronously in Chrome — the first getVoices() is often [].
    const load = () => setVoice(current => current || pickVoice(window.speechSynthesis.getVoices()))
    load()
    window.speechSynthesis.addEventListener('voiceschanged', load)
    return () => {
      window.speechSynthesis.removeEventListener('voiceschanged', load)
      window.speechSynthesis.cancel()
    }
  }, [])

  const clearKeepAlive = useCallback(() => {
    if (keepAlive.current) { clearInterval(keepAlive.current); keepAlive.current = null }
  }, [])

  const stop = useCallback(() => {
    cancelled.current = true
    clearKeepAlive()
    if (ttsSupported()) window.speechSynthesis.cancel()
    setSpeaking(false)
  }, [clearKeepAlive])

  const speak = useCallback((text: string) => new Promise<void>(resolve => {
    if (!ttsSupported() || !text.trim()) { resolve(); return }
    const synth = window.speechSynthesis
    synth.cancel()
    cancelled.current = false
    setSpeaking(true)

    // Chrome silently suspends a speaking queue after ~15s; a periodic
    // resume() keeps it draining.
    clearKeepAlive()
    keepAlive.current = setInterval(() => { if (synth.speaking) synth.resume() }, 5000)

    // Questions may carry `code` backticks for the on-screen card; spoken, they are noise.
    const chunks = intoChunks(text.replace(/`/g, ''))
    let index = 0
    const finish = () => { clearKeepAlive(); setSpeaking(false); resolve() }
    const next = () => {
      if (cancelled.current || index >= chunks.length) { finish(); return }
      const utterance = new SpeechSynthesisUtterance(chunks[index++])
      if (voice) { utterance.voice = voice; utterance.lang = voice.lang }
      utterance.rate = 0.98
      utterance.pitch = 1
      utterance.onend = next
      // A chunk that fails must not strand the interview mid-question.
      utterance.onerror = next
      synth.speak(utterance)
    }
    next()
  }), [voice, clearKeepAlive])

  return { speak, stop, speaking, voiceName: voice?.name ?? null, supported: ttsSupported() }
}

/* ------------------------------------------------------------------ *
 * Microphone capture with voice-activity detection
 * ------------------------------------------------------------------ */

/** Why a listening turn ended. */
export type TurnReason = 'silence' | 'manual' | 'timeout' | 'nospeech' | 'aborted' | 'error'

export type TurnResult = {
  blob: Blob | null
  mime: string
  hadSpeech: boolean
  speechMs: number
  durationMs: number
  reason: TurnReason
}

/** How the candidate currently sounds — drives the mic UI. */
export type ListenPhase = 'idle' | 'waiting' | 'speaking' | 'pausing'

// End-to-end runs with a synthetic candidate showed a 2.2 s hang cutting an
// answer at the first thinking pause: everything said after it was lost.
// A 3.5 s hang still cut the same answer at a ~3.5 s thinking pause. People
// pause for longer than that mid-answer; 5 s is the wait a human interviewer
// gives, the UI says it is waiting, and "I'm done" ends the turn sooner.
export const SILENCE_HANG_MS = 5000
const MIN_SPEECH_MS = 700          // ignore a cough before allowing auto-submit
// Senior questions deserve thinking time. At 15 s the turn ended on a silent
// candidate and Whisper transcribed the silence as "Thank you." — which was
// then scored as their answer.
const NO_SPEECH_TIMEOUT_MS = 30000
const MAX_TURN_MS = 180000         // hard cap on one spoken answer
const CALIBRATION_MS = 500         // room-noise sample taken before VAD arms
// Someone who starts talking inside the calibration window would otherwise set
// the "noise floor" at speech level, and nothing they said after would count.
const MAX_NOISE_FLOOR = 0.03

export function micSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof MediaRecorder !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia
  )
}

function pickMime(): string {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']
  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported?.(candidate)) return candidate
  }
  return ''
}

/** Whisper detects format from the extension, so the mime must map to one. */
export function filenameFor(mime: string): string {
  if (mime.includes('mp4')) return 'answer.mp4'
  if (mime.includes('ogg')) return 'answer.ogg'
  if (mime.includes('wav')) return 'answer.wav'
  return 'answer.webm'
}

export type Listener = {
  listen: () => Promise<TurnResult>
  submitNow: () => void
  abort: () => void
  level: number
  phase: ListenPhase
  supported: boolean
}

export function useListener(): Listener {
  const [level, setLevel] = useState(0)
  const [phase, setPhase] = useState<ListenPhase>('idle')
  const stopRef = useRef<((reason: TurnReason) => void) | null>(null)

  // A turn left running past unmount would hold the mic open indefinitely.
  useEffect(() => () => stopRef.current?.('aborted'), [])

  const listen = useCallback(async (): Promise<TurnResult> => {
    const empty = (reason: TurnReason): TurnResult => (
      { blob: null, mime: '', hadSpeech: false, speechMs: 0, durationMs: 0, reason }
    )
    if (!micSupported()) return empty('error')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
    } catch {
      return empty('error')
    }

    const mime = pickMime()
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
    const parts: BlobPart[] = []
    recorder.ondataavailable = event => { if (event.data.size) parts.push(event.data) }

    const audio = new AudioContext()
    const analyser = audio.createAnalyser()
    analyser.fftSize = 1024
    audio.createMediaStreamSource(stream).connect(analyser)
    const samples = new Float32Array(analyser.fftSize)

    const startedAt = Date.now()
    let speechMs = 0
    let lastLoudAt = startedAt
    let lastTickAt = startedAt
    let noiseFloor = 0
    let calibrating = true
    let frame = 0
    let frameCount = 0
    let settled = false

    return new Promise<TurnResult>(resolve => {
      const finalize = (reason: TurnReason) => {
        if (settled) return
        settled = true
        cancelAnimationFrame(frame)
        stopRef.current = null
        setLevel(0)
        setPhase('idle')
        const emit = () => {
          stream.getTracks().forEach(track => track.stop())
          audio.close().catch(() => { /* already closed */ })
          resolve({
            blob: parts.length ? new Blob(parts, { type: mime || 'audio/webm' }) : null,
            mime: mime || 'audio/webm',
            hadSpeech: speechMs >= MIN_SPEECH_MS,
            speechMs,
            durationMs: Date.now() - startedAt,
            reason,
          })
        }
        // stop() flushes the final chunk asynchronously — wait for it.
        if (recorder.state !== 'inactive') { recorder.onstop = emit; recorder.stop() } else emit()
      }
      stopRef.current = finalize

      const tick = () => {
        analyser.getFloatTimeDomainData(samples)
        let sum = 0
        for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
        const rms = Math.sqrt(sum / samples.length)

        const now = Date.now()
        const elapsed = now - startedAt
        const delta = now - lastTickAt
        lastTickAt = now

        // Throttle the meter — 60 state updates a second would thrash React.
        if (frameCount++ % 3 === 0) setLevel(Math.min(1, rms * 11))

        if (calibrating) {
          // Sample the room first so the threshold suits a noisy cafe as well
          // as a quiet bedroom.
          noiseFloor = Math.max(noiseFloor, rms)
          if (elapsed > CALIBRATION_MS) {
            calibrating = false
            noiseFloor = Math.min(MAX_NOISE_FLOOR, Math.max(0.008, noiseFloor * 2))
            lastLoudAt = now
          }
        } else if (rms > noiseFloor) {
          lastLoudAt = now
          speechMs += delta
          setPhase('speaking')
        } else if (speechMs >= MIN_SPEECH_MS) {
          setPhase('pausing')
          if (now - lastLoudAt > SILENCE_HANG_MS) { finalize('silence'); return }
        } else {
          setPhase('waiting')
          if (elapsed > NO_SPEECH_TIMEOUT_MS) { finalize('nospeech'); return }
        }

        if (elapsed > MAX_TURN_MS) { finalize('timeout'); return }
        frame = requestAnimationFrame(tick)
      }

      recorder.start(250)
      setPhase('waiting')
      frame = requestAnimationFrame(tick)
    })
  }, [])

  const submitNow = useCallback(() => stopRef.current?.('manual'), [])
  const abort = useCallback(() => stopRef.current?.('aborted'), [])

  return { listen, submitNow, abort, level, phase, supported: micSupported() }
}

// Whisper's well-known output for near-silent audio. Only discarded when the
// voice detector also heard very little, so a real "thank you" survives.
const WHISPER_SILENCE_HALLUCINATIONS = /^(thank you|thanks for watching|thank you for watching|you|bye)[.!]?$/i

/** True when a transcript is Whisper filling silence rather than the candidate speaking. */
export function isLikelyHallucination(transcript: string, speechMs: number): boolean {
  return speechMs < 1500 && WHISPER_SILENCE_HALLUCINATIONS.test(transcript.trim())
}

/* ------------------------------------------------------------------ *
 * Live captions — browser SpeechRecognition, also the STT fallback
 * ------------------------------------------------------------------ */

type RecognitionAlternative = { transcript: string }
type RecognitionResult = ArrayLike<RecognitionAlternative> & { isFinal: boolean }
type RecognitionEvent = { resultIndex: number; results: ArrayLike<RecognitionResult> }

type RecognitionLike = {
  continuous: boolean
  interimResults: boolean
  lang: string
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: (() => void) | null
  onend: (() => void) | null
}

declare global {
  interface Window {
    SpeechRecognition?: new () => RecognitionLike
    webkitSpeechRecognition?: new () => RecognitionLike
  }
}

export function captionsSupported(): boolean {
  return typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition)
}

export type Captions = {
  start: () => void
  stop: () => string
  reset: () => void
  text: string
  supported: boolean
}

export function useCaptions(): Captions {
  const [text, setText] = useState('')
  const recognition = useRef<RecognitionLike | null>(null)
  const settled = useRef('')

  const stop = useCallback(() => {
    try { recognition.current?.stop() } catch { /* already stopped */ }
    recognition.current = null
    return settled.current.trim()
  }, [])

  useEffect(() => () => { try { recognition.current?.abort() } catch { /* noop */ } }, [])

  const reset = useCallback(() => { settled.current = ''; setText('') }, [])

  const start = useCallback(() => {
    if (!captionsSupported()) return
    stop()
    reset()
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!Recognition) return
    const engine = new Recognition()
    engine.continuous = true
    engine.interimResults = true
    engine.lang = 'en-IN'
    engine.onresult = event => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        const phrase = result[0]?.transcript ?? ''
        if (result.isFinal) settled.current += phrase
        else interim += phrase
      }
      setText((settled.current + interim).trim())
    }
    // Recognition dying mid-answer is survivable — Whisper still has the audio.
    engine.onerror = () => { recognition.current = null }
    engine.onend = () => { recognition.current = null }
    try { engine.start(); recognition.current = engine } catch { recognition.current = null }
  }, [stop, reset])

  return { start, stop, reset, text, supported: captionsSupported() }
}
