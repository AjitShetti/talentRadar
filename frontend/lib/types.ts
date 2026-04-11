// ─────────────────────────────────────────────
// Core Job Schemas (match API responses)
// ─────────────────────────────────────────────

export interface Job {
  id: string;
  title: string;
  company_name?: string;
  company_id: string;
  source: string;
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
  created_at: string;
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
