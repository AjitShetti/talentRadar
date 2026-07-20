// frontend/lib/interview-types.ts
// ─────────────────────────────────────────────────────────────────
// TypeScript types that mirror the Phase 3 Pydantic schemas exactly.
// ─────────────────────────────────────────────────────────────────

export type InterviewTrack = 'python_dsa' | 'python_backend' | 'sql' | 'system_design';
export type InterviewDifficulty = 'beginner' | 'mid' | 'senior';

// Matches InterviewAgentState TypedDict in agents/interview/state.py
export interface AgentState {
  track: InterviewTrack;
  difficulty: InterviewDifficulty;
  user_id: string;
  conversation_history: Array<{ role: 'user' | 'assistant'; content: string }>;
  question_index: number;
  followup_count: number;
  scores: ScoreRecord[];
  session_complete: boolean;
  current_question?: string;
  current_answer?: string;
  next_action?: string;
  is_followup?: boolean;
  last_score?: AnswerScore;
  error?: string | null;
}

export interface ScoreRecord {
  correctness: number;
  clarity: number;
  depth: number;
  needs_followup?: boolean;
  feedback_note?: string;
  answer_summary?: string;
  question_index: number;
  question_text: string;
  was_followup: boolean;
}

export interface AnswerScore {
  correctness: number;
  clarity: number;
  depth: number;
  answer_summary?: string | null;
}

// ── Request bodies ─────────────────────────────────────────────
export interface StartSessionRequest {
  track: InterviewTrack;
  difficulty: InterviewDifficulty;
}

export interface SubmitAnswerRequest {
  session_id: string;
  answer: string;
  agent_state: AgentState;
}

export interface EndSessionRequest {
  session_id: string;
  agent_state: AgentState;
}

// ── Response bodies ────────────────────────────────────────────
export interface StartSessionResponse {
  session_id: string;
  question: string;
  question_index: number;
  is_followup: boolean;
  agent_state: AgentState;
}

export interface SubmitAnswerResponse {
  session_id: string;
  question: string;
  question_index: number;
  is_followup: boolean;
  score: AnswerScore;
  session_complete: boolean;
  agent_state: AgentState;
}

export interface FinalScore {
  total_score: number;
  correctness: number;
  clarity: number;
  depth: number;
  questions_answered: number;
}

export interface EndSessionResponse {
  session_id: string;
  completed: boolean;
  final_score: FinalScore;
  closing_message: string;
}

export interface SessionSummary {
  id: string;
  track: InterviewTrack;
  difficulty: InterviewDifficulty;
  total_score: number | null;
  completed: boolean;
  duration_seconds: number | null;
  created_at: string;
}

export interface SessionHistoryResponse {
  sessions: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface TranscribeResponse {
  transcript: string;
  confidence: number | null;
  provider: 'groq_whisper' | 'browser_fallback';
}

// ── Catalog metadata (frontend-only) ──────────────────────────
export interface TrackMeta {
  id: InterviewTrack;
  label: string;
  description: string;
  icon: string; // lucide icon name
  topics: string[];
}

export interface DifficultyMeta {
  id: InterviewDifficulty;
  label: string;
  questions: number;
}

// ── Session state held by the interview page ──────────────────
export type SessionPhase =
  | 'idle'
  | 'loading'
  | 'questioning'    // Waiting for user to answer
  | 'recording'      // Microphone active
  | 'submitting'     // Sending answer to backend
  | 'complete';      // Session ended

export interface LiveSessionState {
  phase: SessionPhase;
  sessionId: string | null;
  agentState: AgentState | null;
  currentQuestion: string;
  currentQuestionIndex: number;
  isFollowup: boolean;
  lastScore: AnswerScore | null;
  totalQuestionsAsked: number;  // for progress bar
  sessionComplete: boolean;
  finalScore: FinalScore | null;
  error: string | null;
}
