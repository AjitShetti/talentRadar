'use client'

import { clearPersistedState } from '@/lib/persistent-state'

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const TOKEN_KEY = 'talentradar_token'
const EMAIL_KEY = 'talentradar_email'

export type Job = { id: string; title: string; company?: string | null; company_name?: string | null; location_raw?: string | null; is_remote?: boolean; skills?: string[]; source_url?: string | null; salary_raw?: string | null; match_score?: number | null; description_clean?: string | null }
// One of the dashboard's daily top-3 picks for a Profile.target_roles entry —
// computed by the APScheduler job in api/main.py (services/job_matching.py).
export type JobMatch = { id: string; title: string; company: string; location: string; is_remote: boolean; skills: string[]; salary_raw?: string | null; source_url?: string | null; posted_at?: string | null; matched_role: string }
export type Application = { id: string; job_id?: string | null; status: string; notes?: string | null; applied_at?: string | null; created_at: string; job?: Job | null }
export type InterviewState = Record<string, unknown>
export type InterviewScore = { correctness: number; clarity: number; depth: number; answer_summary?: string; verbal_ack?: string | null }
export type AtsResult = { ats_score: number; missing_skills: string[]; matched_skills: string[]; suggestions: string[]; reasoning: string }
export type TailorResult = { candidate_name: string; latex_content: string; pdf_base64: string | null; filename: string | null }
export type SavedResume = { id: string; extracted_text: string; filename: string | null; updated_at: string | null }
export type ResumeSectionType = 'summary' | 'education' | 'experience' | 'projects' | 'skills' | 'certifications' | 'custom'
export type ResumeLink = { label: string; url: string }
export type ResumePersonal = { full_name: string; headline: string; email: string; phone: string; location: string; links: ResumeLink[] }
// Shape varies by the owning section's type — e.g. education uses school/degree,
// experience uses title/company, skills uses category/items. Kept as one loose
// type (rather than a discriminated union) since a section's items are only ever
// read/written alongside their own section, where the relevant fields are known.
export type ResumeItem = {
  text?: string
  school?: string; degree?: string
  title?: string; company?: string
  name?: string; tech?: string; link?: string
  category?: string; items?: string[]
  issuer?: string; date?: string
  heading?: string
  location?: string; dates?: string
  bullets?: string[]
}
export type ResumeSection = { id: string; type: ResumeSectionType; title: string; visible: boolean; order: number; items: ResumeItem[] }
export type ResumeDocument = { schema_version?: number; personal: ResumePersonal; sections: ResumeSection[] }
export type CompiledResume = { latex_content: string; pdf_base64: string | null; filename: string | null; compile_error?: string | null }
export type TargetJob = { id: string; title: string; company_name: string | null; description: string | null; location_raw: string | null }
export type SkillFocusItem = { title: string; detail: string }
// Resume vs target roles: `kind` decides what the dashboard panel is showing —
// the skills the target roles ask for and the resume doesn't show, or, when it
// already covers them, how the resume itself could read better.
export type SkillsFocus = { status: 'ok' | 'no_target_roles' | 'no_resume'; kind: 'missing_skills' | 'resume_improvements' | null; headline: string; items: SkillFocusItem[]; target_roles: string[]; resume_filename: string | null; resume_updated_at: string | null; analysis: string | null }
export type CompanyCard = { id: string; name: string; domain?: string | null; website_url?: string | null; logo_url?: string | null; tier?: string | null; tier_label?: string | null; industry?: string | null; description?: string | null; hq_city?: string | null; hq_country?: string | null; office_cities: string[]; employee_count_range?: string | null; founded_year?: number | null; github_org?: string | null; careers_url?: string | null; tech_stack: string[]; open_roles: number }
export type CompanyDirectory = { companies: CompanyCard[]; total: number; offset: number; limit: number; city?: string | null }
export type CompanyFacets = { city?: string | null; cities: string[]; tiers: Array<{ value: string; label: string; count: number }>; industries: Array<{ value: string; count: number }>; industries_total?: number; total: number }
export type GithubRepo = { name: string; full_name: string; description: string | null; html_url: string; language: string | null; stars: number; forks: number; topics: string[]; pushed_at: string | null }
export type GithubOrg = { org: string; name: string; html_url: string; avatar_url: string | null; description: string | null; blog: string | null; location: string | null; public_repos: number; followers: number; top_repos: GithubRepo[]; top_languages: string[] }
export type CompanyContact = { id: string; kind: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; notes?: string | null; source_url?: string | null; verified: boolean; is_curated: boolean; created_at?: string | null }
export type ContactCandidate = { kind: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; source_url?: string | null; source_title?: string | null; verified: boolean }
export type ContactDiscovery = { company?: string | null; careers_url?: string | null; candidates: ContactCandidate[]; queries: string[]; available: boolean; message?: string | null }
export type CompanyRole = { id: string; title: string; location?: string | null; is_remote?: boolean; seniority?: string | null; salary_raw?: string | null; skills: string[]; source_url?: string | null; posted_at?: string | null }
export type CompanyDetail = CompanyCard & { linkedin_url?: string | null; github?: GithubOrg | null; tech_stack_curated: string[]; tech_stack_from_postings: string[]; culture_summary?: string | null; contacts: CompanyContact[]; jobs: CompanyRole[]; has_profile: boolean }
export type NewContact = { kind?: string; name?: string | null; title?: string | null; email?: string | null; linkedin_url?: string | null; notes?: string | null; source_url?: string | null; verified?: boolean }
export type BriefingAction = { label: string; href: string; style: string }
export type BriefingCard = { id: string; kind: string; title: string; detail: string; tone: string; actions: BriefingAction[]; meta: Record<string, unknown>; dismissible: boolean }
export type Briefing = { generated_at: string; headline: string; cards: BriefingCard[]; hidden_count: number; stats: { total_applications?: number; saved?: number; applied?: number; interviews?: number; onboarding_completed?: boolean } }
export type ChatJob = { id: string; title: string; company?: string | null; location?: string | null; is_remote?: boolean; skills?: string[]; source_url?: string | null; score?: number | null }
export type ChatReply = { intent: string; reply: string; jobs: ChatJob[]; data?: unknown; error?: string | null }
export type AgentMemory = { id: string; memory_type: string; content: string; metadata?: Record<string, unknown> | null; created_at?: string | null }
export type LearningTask = { skill_name: string; title: string; description?: string | null; resources?: string[] | null; priority?: number | null }
export type LearningPlan = { tasks: LearningTask[]; total: number }

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
    document: {
      get: () => request<ResumeDocument>('/api/v1/resumes/document', {}, true),
      save: (document: ResumeDocument) => request<ResumeDocument>('/api/v1/resumes/document', { method: 'PUT', body: JSON.stringify({ document }) }, true),
      compile: (document: ResumeDocument) => request<CompiledResume>('/api/v1/resumes/document/compile', { method: 'POST', body: JSON.stringify({ document }) }, true),
    },
  },
  interview: {
    start: (track: string, difficulty: string, voice_mode = false) => request<{ session_id: string; question: string; question_index: number; agent_state: InterviewState }>('/api/v1/interview/sessions/start', { method: 'POST', body: JSON.stringify({ track, difficulty, voice_mode }) }, true),
    answer: (session_id: string, answer: string, agent_state: InterviewState) => request<{ question: string; question_index: number; is_followup: boolean; session_complete: boolean; agent_state: InterviewState; score: InterviewScore }>('/api/v1/interview/sessions/answer', { method: 'POST', body: JSON.stringify({ session_id, answer, agent_state }) }, true),
    end: (session_id: string, agent_state: InterviewState) => request<{ closing_message: string; final_score: { total_score: number; correctness: number; clarity: number; depth: number; questions_answered: number } }>('/api/v1/interview/sessions/end', { method: 'POST', body: JSON.stringify({ session_id, agent_state }) }, true),
    history: () => request<{ sessions: Array<{ id: string; track: string; difficulty: string; total_score?: number; completed: boolean; created_at: string }> }>('/api/v1/interview/sessions/history', {}, true),
    // Sent as multipart so the browser's MediaRecorder blob reaches Groq Whisper
    // untouched; provider='browser_fallback' means the caller should use its own
    // Web Speech transcript instead.
    transcribe: (blob: Blob, filename = 'answer.webm') => { const form = new FormData(); form.append('audio', new File([blob], filename, { type: blob.type || 'audio/webm' })); return request<{ transcript: string; confidence: number | null; provider: string }>('/api/v1/interview/voice/transcribe', { method: 'POST', body: form }, true) },
  },
  career: { weaknesses: () => request<Record<string, unknown>>('/api/v1/career/weaknesses', {}, true), recommend: () => request<LearningPlan>('/api/v1/career/recommend', { method: 'POST', body: JSON.stringify({ persist: true }) }, true) },
  company: {
    directory: (filters: { city?: string; tier?: string; industry?: string; q?: string; hasOpenRoles?: boolean } = {}) => {
      const params = new URLSearchParams()
      if (filters.city) params.set('city', filters.city)
      if (filters.tier) params.set('tier', filters.tier)
      if (filters.industry) params.set('industry', filters.industry)
      if (filters.q) params.set('q', filters.q)
      if (filters.hasOpenRoles) params.set('has_open_roles', 'true')
      return request<CompanyDirectory>(`/api/v1/company-intel/?${params.toString()}`, {}, true)
    },
    facets: (city?: string) => request<CompanyFacets>(`/api/v1/company-intel/facets${city ? `?city=${encodeURIComponent(city)}` : ''}`, {}, true),
    detail: (id: string) => request<CompanyDetail>(`/api/v1/company-intel/${encodeURIComponent(id)}`, {}, true),
    search: (name: string) => request<Record<string, unknown>>(`/api/v1/company-intel/resolve?name=${encodeURIComponent(name)}`, {}, true),
    discoverContacts: (id: string) => request<ContactDiscovery>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts/discover`, { method: 'POST' }, true),
    addContact: (id: string, payload: NewContact) => request<CompanyContact>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts`, { method: 'POST', body: JSON.stringify(payload) }, true),
    removeContact: (id: string, contactId: string) => request<void>(`/api/v1/company-intel/${encodeURIComponent(id)}/contacts/${encodeURIComponent(contactId)}`, { method: 'DELETE' }, true),
  },
  agent: {
    nextAction: () => request<Record<string, unknown>>('/api/v1/agent/next-action', {}, true),
    briefing: () => request<Briefing>('/api/v1/agent/briefing', {}, true),
    starters: () => request<{ starters: string[] }>('/api/v1/agent/starters', {}, true),
    chat: (message: string, history: Array<{ role: string; content: string }> = []) => request<ChatReply>('/api/v1/agent/chat', { method: 'POST', body: JSON.stringify({ message, history: history.slice(-8) }) }, true),
    dismissCard: (card_id: string, snooze_days?: number) => request<void>('/api/v1/agent/cards/dismiss', { method: 'POST', body: JSON.stringify({ card_id, snooze_days }) }, true),
    memories: () => request<{ memories: AgentMemory[]; count: number }>('/api/v1/agent/memories', {}, true),
    remember: (content: string, memory_type = 'preference') => request<AgentMemory>('/api/v1/agent/memories', { method: 'POST', body: JSON.stringify({ content, memory_type }) }, true),
    forget: (id: string) => request<void>(`/api/v1/agent/memories/${encodeURIComponent(id)}`, { method: 'DELETE' }, true),
  },
}
