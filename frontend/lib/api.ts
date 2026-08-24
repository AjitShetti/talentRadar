'use client'

import { clearPersistedState } from '@/lib/persistent-state'

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const TOKEN_KEY = 'talentradar_token'
const EMAIL_KEY = 'talentradar_email'

export type Job = { id: string; title: string; company?: string | null; company_name?: string | null; location_raw?: string | null; is_remote?: boolean; skills?: string[]; source_url?: string | null; salary_raw?: string | null; match_score?: number | null; description_clean?: string | null }
export type Application = { id: string; job_id?: string | null; status: string; notes?: string | null; applied_at?: string | null; created_at: string; job?: Job | null }
export type InterviewState = Record<string, unknown>
export type AtsResult = { ats_score: number; missing_skills: string[]; matched_skills: string[]; suggestions: string[]; reasoning: string }
export type TailorResult = { candidate_name: string; latex_content: string; pdf_base64: string | null; filename: string | null }
export type SavedResume = { id: string; extracted_text: string; filename: string | null; updated_at: string | null }
export type TargetJob = { id: string; title: string; company_name: string | null; description: string | null; location_raw: string | null }

export function token() { return typeof window === 'undefined' ? null : localStorage.getItem(TOKEN_KEY) }
export function signedIn() { return Boolean(token()) }
export function currentEmail() { return typeof window === 'undefined' ? null : localStorage.getItem(EMAIL_KEY) }
export function signOut() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); clearPersistedState() }

async function request<T>(path: string, options: RequestInit = {}, authenticated = false): Promise<T> {
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (authenticated) {
    const accessToken = token()
    if (!accessToken) throw new Error('Please sign in to use this feature.')
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  const response = await fetch(`${API_URL}${path}`, { ...options, headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string; message?: string }
    if (response.status === 401) signOut()
    throw new Error(body.detail || body.message || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  auth: {
    async login(email: string, password: string) {
      const result = await request<{ access_token: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
      localStorage.setItem(TOKEN_KEY, result.access_token); localStorage.setItem(EMAIL_KEY, email)
      return result
    },
    signup: (email: string, password: string) => request('/api/auth/signup', { method: 'POST', body: JSON.stringify({ email, password }) }),
  },
  dashboard: () => request<Record<string, unknown>>('/api/v1/dashboard/overview', {}, true),
  search: {
    semantic: (query: string) => request<{ results: Job[]; total_found: number; summary?: string }>('/api/v1/search/semantic', { method: 'POST', body: JSON.stringify({ query, limit: 30 }) }),
    structured: (query: string, filters: { location?: string; remote?: boolean; experience?: string } = {}) => request<{ jobs: Job[]; total: number }>('/api/v1/search/structured', { method: 'POST', body: JSON.stringify({ query, location: filters.location || undefined, is_remote: filters.remote || undefined, experience: filters.experience || undefined, india_only: true, limit: 30 }) }),
  },
  applications: {
    list: () => request<{ applications: Application[]; total: number }>('/api/v1/applications/', {}, true),
    create: (job_id: string, status = 'saved', notes?: string) => request<Application>('/api/v1/applications/', { method: 'POST', body: JSON.stringify({ job_id, status, notes }) }, true),
    update: (id: string, payload: { status?: string; notes?: string }) => request<Application>(`/api/v1/applications/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, true),
    remove: (id: string) => request<void>(`/api/v1/applications/${id}`, { method: 'DELETE' }, true),
  },
  profile: {
    get: () => request<{ onboarding_completed: boolean; profile: Record<string, unknown> | null }>('/api/v1/profile/', {}, true),
    save: (payload: Record<string, unknown>) => request<{ profile: Record<string, unknown> }>('/api/v1/profile/', { method: 'POST', body: JSON.stringify(payload) }, true),
  },
  resumes: {
    me: () => request<SavedResume | null>('/api/v1/resumes/me', {}, true),
    targetJobs: () => request<TargetJob[]>('/api/v1/resumes/target-jobs', {}, true),
    extractText: (file: File) => { const form = new FormData(); form.append('resume_file', file); return request<SavedResume>('/api/v1/resumes/extract-text', { method: 'POST', body: form }, true) },
    analyze: (payload: { resume_text: string; job_description: string; job_title?: string }) => request<AtsResult>('/api/v1/resumes/analyze', { method: 'POST', body: JSON.stringify(payload) }, true),
    tailor: (payload: { resume_text: string; job_description: string; job_title?: string }) => request<TailorResult>('/api/v1/resumes/tailor', { method: 'POST', body: JSON.stringify(payload) }, true),
    coverLetter: (payload: { resume_text: string; job_description: string; job_title: string; company: string; tone?: string }) => request<{ content: string; tone: string }>('/api/v1/resumes/cover-letter', { method: 'POST', body: JSON.stringify(payload) }, true),
    gaps: (payload: Record<string, unknown>) => request<Record<string, unknown>>('/api/v1/resumes/gaps', { method: 'POST', body: JSON.stringify(payload) }, true),
  },
  interview: {
    start: (track: string, difficulty: string) => request<{ session_id: string; question: string; question_index: number; agent_state: InterviewState }>('/api/v1/interview/sessions/start', { method: 'POST', body: JSON.stringify({ track, difficulty }) }, true),
    answer: (session_id: string, answer: string, agent_state: InterviewState) => request<{ question: string; question_index: number; is_followup: boolean; session_complete: boolean; agent_state: InterviewState; score: { correctness: number; clarity: number; depth: number; answer_summary?: string } }>('/api/v1/interview/sessions/answer', { method: 'POST', body: JSON.stringify({ session_id, answer, agent_state }) }, true),
    end: (session_id: string, agent_state: InterviewState) => request<{ closing_message: string; final_score: { total_score: number; correctness: number; clarity: number; depth: number; questions_answered: number } }>('/api/v1/interview/sessions/end', { method: 'POST', body: JSON.stringify({ session_id, agent_state }) }, true),
    history: () => request<{ sessions: Array<{ id: string; track: string; difficulty: string; total_score?: number; completed: boolean; created_at: string }> }>('/api/v1/interview/sessions/history', {}, true),
  },
  career: { weaknesses: () => request<Record<string, unknown>>('/api/v1/career/weaknesses', {}, true), recommend: () => request<Record<string, unknown>>('/api/v1/career/recommend', { method: 'POST', body: JSON.stringify({ persist: true }) }, true) },
  company: { search: (name: string) => request<Record<string, unknown>>(`/api/v1/company-intel/?name=${encodeURIComponent(name)}`, {}, true) },
  agent: { nextAction: () => request<Record<string, unknown>>('/api/v1/agent/next-action', {}, true), memories: () => request<{ memories: Array<Record<string, unknown>> }>('/api/v1/agent/memories', {}, true) },
}
