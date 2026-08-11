// ─────────────────────────────────────────────
// Core Job Schemas (match API responses)
// ─────────────────────────────────────────────

export interface Job {
  id: string;
  title: string;
  company_name?: string;
  company?: string;           // Populated from semantic search results
  company_id?: string;
  source?: string;
  source_url?: string;
  location_raw?: string;
  country?: string;
  city?: string;
  is_remote: boolean;
  seniority?: string;
  employment_type?: string;
  salary_raw?: string;
  salary_min?: number;
  salary_max?: number;
  salary_currency?: string;
  skills: string[];
  tags: string[];
  description_clean?: string;
  posted_at?: string;
  created_at?: string;
  match_score?: number;
  views?: number;
}

// ─────────────────────────────────────────────
// Search
// ─────────────────────────────────────────────

export interface JobFilterSchema {
  query?: string;
  skills?: string[];
  location?: string;
  is_remote?: boolean;
  seniority?: string;
  employment_type?: string;
  salary_min?: number;
  salary_max?: number;
  limit?: number;
  offset?: number;
}

export interface SearchStructuredResponse {
  jobs: Job[];
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
}

export interface SearchSemanticRequest {
  query: string;
  limit?: number;
  offset?: number;
}

export interface SearchSemanticResponse {
  results: Job[];
  total_found: number;
  summary?: string;
  filters_applied?: Record<string, unknown>;
}

export interface JobDetailResponse {
  job: Job;
  similar_jobs?: Job[];
}

// ─────────────────────────────────────────────
// Trends
// ─────────────────────────────────────────────

export interface TrendsRequest {
  query?: string;
  days?: number;
}

export interface TrendData {
  summary?: string;
  total_jobs: number;
  top_skills: Array<{ skill: string; count: number }>;
  salary_data?: {
    available: boolean;
    avg_min?: number;
    avg_max?: number;
    min?: number;
    max?: number;
    count?: number;
    currency?: string;
  };
  location_data: Array<{ location: string; count: number }>;
  seniority_data: Array<{ seniority: string; count: number }>;
  period_days?: number;
}

export interface SkillsTrendResponse {
  skills: Array<{ skill: string; count: number }>;
  period_days: number;
}

export interface LocationsTrendResponse {
  locations: Array<{ location: string; count: number }>;
  period_days: number;
}

export interface SalaryTrendResponse {
  salary_data: TrendData['salary_data'];
  period_days?: number;
}

// ─────────────────────────────────────────────
// Recommend / Match
// ─────────────────────────────────────────────

export interface CandidateProfile {
  name?: string;
  skills: string[];
  experience_years?: number;
  current_title?: string;
  desired_title?: string;
  location?: string;
  is_remote: boolean;
  seniority?: string;
  resume_text?: string;
}

export interface MatchRequest {
  candidate: CandidateProfile;
  limit?: number;
}

export interface MatchResult {
  job_id: string;
  title: string;
  company: string;
  location?: string;
  is_remote: boolean;
  skills: string[];
  score: number;
  match_reason?: string;
}

export interface MatchResponse {
  matches: MatchResult[];
  summary?: string;
  top_score?: number;
}

export interface SkillsAnalysisRequest {
  candidate_skills: string[];
  target_role: string;
}

// ─────────────────────────────────────────────
// Natural Language Query
// ─────────────────────────────────────────────

export interface QueryRequest {
  query: string;
  limit?: number;
  offset?: number;
}

export interface QueryResponse {
  intent: string;
  summary?: string;
  results: Job[];
  metadata?: Record<string, unknown>;
  error?: string;
}

// ─────────────────────────────────────────────
// Ingestion
// ─────────────────────────────────────────────

export interface IngestTriggerRequest {
  roles?: string[];
  locations?: string[];
  max_results_per_query?: number;
}

export interface IngestTriggerResponse {
  success: boolean;
  message: string;
  dag_run_id?: string;
  estimated_time?: string;
}

export interface IngestRun {
  dag_run_id: string;
  dag_id: string;
  state: 'running' | 'success' | 'failed' | 'queued' | 'up_for_retry' | string;
  start_date?: string;
  end_date?: string;
  duration?: number;
  roles?: string[];
  locations?: string[];
  jobs_collected?: number;
}

export interface IngestRunsResponse {
  runs: IngestRun[];
  total: number;
}

// ─────────────────────────────────────────────
// Health
// ─────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  database?: string;
  search_engine?: string;
  timestamp?: string;
}

// ─────────────────────────────────────────────
// Pagination Helper
// ─────────────────────────────────────────────

export interface PaginationState {
  limit: number;
  offset: number;
}

// ─── Applications ─────────────────────────────

export type ApplicationStatus =
  | 'saved' | 'applied' | 'screening' | 'interview'
  | 'offer' | 'rejected' | 'withdrawn';

export interface ApplicationJob {
  id: string;
  title: string;
  company_name?: string;
  location_raw?: string;
  is_remote: boolean;
  source_url?: string;
  salary_raw?: string;
  skills: string[];
}

export interface JobApplication {
  id: string;
  job_id: string | null;
  status: ApplicationStatus;
  notes: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
  job: ApplicationJob | null;
}

export interface ApplicationListResponse {
  applications: JobApplication[];
  total: number;
}

export interface ApplicationCreateRequest {
  job_id: string;
  status?: ApplicationStatus;
  notes?: string;
}

export interface ApplicationUpdateRequest {
  status?: ApplicationStatus;
  notes?: string;
  applied_at?: string;
}

// ─── Profile / Onboarding ───────────────────────

export interface ProfileSkill {
  name: string;
  proficiency?: number;
}

export interface ProfileUpsertRequest {
  full_name?: string;
  headline?: string;
  summary?: string;
  target_roles?: string[];
  target_locations?: string[];
  is_remote_preferred?: boolean;
  target_salary_min?: number;
  target_salary_max?: number;
  salary_currency?: string;
  years_experience?: number;
  current_role?: string;
  career_goals?: string;
  active_resume_id?: string;
  skills?: ProfileSkill[] | string[];
}

export interface ProfileResponse {
  onboarding_completed: boolean;
  profile: {
    id: string;
    user_id: string;
    full_name: string | null;
    headline: string | null;
    summary: string | null;
    target_roles: string[] | null;
    target_locations: string[] | null;
    is_remote_preferred: boolean;
    target_salary_min: number | null;
    target_salary_max: number | null;
    salary_currency: string | null;
    years_experience: number | null;
    current_role: string | null;
    career_goals: string | null;
    onboarding_completed: boolean;
    created_at: string;
    updated_at: string;
  } | null;
}

// ─── Resume Studio ──────────────────────────────

export interface ResumeAnalyzeRequest {
  resume_text: string;
  job_description: string;
  job_title?: string;
}

export interface ResumeAnalyzeResponse {
  ats_score: number;
  analysis: Record<string, unknown>;
  skills_match?: Record<string, unknown>;
  suggestions?: string[];
}

export interface ResumeTailorRequest {
  resume_text: string;
  job_description: string;
  job_title?: string;
}

export interface ResumeTailorResponse {
  latex?: string;
  pdf_url?: string;
  tailored_text?: string;
  changes?: string[];
}

export interface CoverLetterRequest {
  resume_text: string;
  job_description: string;
  job_title: string;
  company: string;
  tone?: string;
}

export interface CoverLetterResponse {
  content: string;
  tone: string;
}

export interface ResumeGapsRequest {
  resume_skills?: string[];
  resume_text?: string;
  job_skills?: string[];
  job_description?: string;
}

export interface ResumeGapsResponse {
  missing_skills: string[];
  matching_skills: string[];
  match_percentage: number;
  recommendations?: string[];
}

// ─── Company Intelligence ───────────────────────

export interface CompanyIntelResponse {
  company: {
    id: string;
    name: string;
    domain: string | null;
    industry: string | null;
    hq_city: string | null;
    hq_country: string | null;
    employee_count_range: string | null;
    website_url: string | null;
  };
  profile: {
    tech_stack: string[] | null;
    salary_ranges: Record<string, unknown> | null;
    interview_patterns: Record<string, unknown> | null;
    hiring_trends: Record<string, unknown> | null;
    culture_summary: string | null;
  } | null;
  open_jobs: Array<{
    id: string;
    title: string;
    location_raw: string | null;
    is_remote: boolean;
    seniority: string | null;
    skills: string[];
  }>;
}

export interface CompanySearchResponse {
  id: string;
  name: string;
  domain: string | null;
  industry: string | null;
  hq_city: string | null;
  hq_country: string | null;
  open_jobs_count?: number;
}

// ─── Career Coach ───────────────────────────────

export interface WeaknessResponse {
  skill: string;
  current_level?: number;
  target_level?: number;
  gap: number;
}

export interface WeaknessesResponse {
  weaknesses: WeaknessResponse[];
  target_role: string | null;
  summary?: string;
}

export interface CareerRecommendRequest {
  persist?: boolean;
}

export interface LearningTaskResponse {
  id: string;
  skill_name: string;
  title: string;
  description: string | null;
  resources: string[] | null;
  priority: number | null;
  status: string;
}

export interface CareerRecommendResponse {
  tasks: LearningTaskResponse[];
  generated: number;
  persisted: number;
}

// ─── Personal AI Agent ──────────────────────────

export interface AgentNextAction {
  action: string;
  reason: string;
  priority: 'high' | 'medium' | 'low';
  context?: Record<string, unknown>;
}

export interface AgentMemory {
  id: string;
  memory_type: string;
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
  expires_at: string | null;
}
