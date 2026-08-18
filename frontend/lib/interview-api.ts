// frontend/lib/interview-api.ts
// ─────────────────────────────────────────────────────────────────
// API client for all /api/v1/interview/* endpoints.
// Follows the exact same fetchAPI pattern as lib/api.ts.
// ─────────────────────────────────────────────────────────────────

import type {
  StartSessionRequest,
  StartSessionResponse,
  SubmitAnswerRequest,
  SubmitAnswerResponse,
  EndSessionRequest,
  EndSessionResponse,
  SessionHistoryResponse,
  TranscribeResponse,
} from './interview-types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ─────────────────────────────────────────────
// Core authenticated fetch (mirrors lib/api.ts)
// ─────────────────────────────────────────────

async function fetchInterview<T>(
  endpoint: string,
  token: string,
  options: {
    method?: 'GET' | 'POST';
    body?: unknown;
    signal?: AbortSignal;
  } = {}
): Promise<T> {
  const { method = 'GET', body, signal } = options;

  const fetchOptions: RequestInit = {
    method,
    signal,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  };

  if (body !== undefined) {
    fetchOptions.body = JSON.stringify(body);
  }

  const res = await fetch(`${API_URL}${endpoint}`, fetchOptions);

  if (!res.ok) {
    let msg = `API error: ${res.status}`;
    try {
      const err = (await res.json()) as { detail?: string };
      msg = err.detail || msg;
    } catch {
      if (res.status === 401) msg = 'Unauthorised - please sign in again.';
      if (res.status === 429) msg = 'Rate limited. Please wait a moment.';
      if (res.status >= 500) msg = 'Server error. Please try again later.';
    }
    throw new Error(msg);
  }

  return res.json() as Promise<T>;
}

// ─────────────────────────────────────────────
// Multipart fetch for voice/transcribe
// ─────────────────────────────────────────────

async function fetchTranscribe(
  audioBlob: Blob,
  filename: string,
  token: string,
  signal?: AbortSignal
): Promise<TranscribeResponse> {
  const form = new FormData();
  form.append('audio', audioBlob, filename);

  const res = await fetch(`${API_URL}/api/v1/interview/voice/transcribe`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
    signal,
  });

  if (!res.ok) {
    let msg = `Transcription error: ${res.status}`;
    try {
      const err = (await res.json()) as { detail?: string };
      msg = err.detail || msg;
    } catch { /* ignore */ }
    throw new Error(msg);
  }

  return res.json() as Promise<TranscribeResponse>;
}

// ─────────────────────────────────────────────
// Exported interview API client
// ─────────────────────────────────────────────

export const interviewApi = {
  /** Create a new session and get the first question. */
  startSession: (req: StartSessionRequest, token: string, signal?: AbortSignal) =>
    fetchInterview<StartSessionResponse>(
      '/api/v1/interview/sessions/start',
      token,
      { method: 'POST', body: req, signal }
    ),

  /** Submit an answer and get the next question + score. */
  submitAnswer: (req: SubmitAnswerRequest, token: string, signal?: AbortSignal) =>
    fetchInterview<SubmitAnswerResponse>(
      '/api/v1/interview/sessions/answer',
      token,
      { method: 'POST', body: req, signal }
    ),

  /** Gracefully end a session early. */
  endSession: (req: EndSessionRequest, token: string, signal?: AbortSignal) =>
    fetchInterview<EndSessionResponse>(
      '/api/v1/interview/sessions/end',
      token,
      { method: 'POST', body: req, signal }
    ),

  /** Get paginated session history for the current user. */
  history: (
    token: string,
    limit = 20,
    offset = 0,
    signal?: AbortSignal
  ) =>
    fetchInterview<SessionHistoryResponse>(
      `/api/v1/interview/sessions/history?limit=${limit}&offset=${offset}`,
      token,
      { method: 'GET', signal }
    ),

  /** Transcribe an audio blob via Groq Whisper. */
  transcribe: (
    audioBlob: Blob,
    filename: string,
    token: string,
    signal?: AbortSignal
  ) => fetchTranscribe(audioBlob, filename, token, signal),
};
