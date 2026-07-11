import {
  type JobFilterSchema,
  type SearchStructuredResponse,
  type SearchSemanticRequest,
  type SearchSemanticResponse,
  type JobDetailResponse,
  type TrendData,
  type SkillsTrendResponse,
  type LocationsTrendResponse,
  type SalaryTrendResponse,
  type CandidateProfile,
  type MatchRequest,
  type MatchResponse,
  type SkillsAnalysisRequest,
  type QueryRequest,
  type QueryResponse,
  type IngestTriggerRequest,
  type IngestTriggerResponse,
  type IngestRunsResponse,
  type HealthResponse,
} from './types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ─────────────────────────────────────────────
// Core fetch with AbortController support
// ─────────────────────────────────────────────

interface FetchOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

async function fetchAPI<T>(endpoint: string, options: FetchOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, headers = {} } = options;

  const fetchOptions: RequestInit = {
    method,
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  };

  if (body !== undefined) {
    fetchOptions.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_URL}${endpoint}`, fetchOptions);

  if (!response.ok) {
    let errorMessage = `API error: ${response.status}`;
    try {
      const errorBody = (await response.json()) as { detail?: string; message?: string };
      errorMessage = errorBody.detail || errorBody.message || errorMessage;
    } catch {
      // If parsing fails, use status-based message
      if (response.status === 404) errorMessage = 'Resource not found';
      if (response.status === 429) errorMessage = 'Rate limited. Please try again later.';
      if (response.status >= 500) errorMessage = 'Server error. Please try again later.';
    }
    throw new Error(errorMessage);
  }

  return response.json() as Promise<T>;
}

// ─────────────────────────────────────────────
// API Client
// ─────────────────────────────────────────────

export const api = {
  // ── Search ─────────────────────────────────
  search: {
    structured: (filters: JobFilterSchema, signal?: AbortSignal) =>
      fetchAPI<SearchStructuredResponse>('/api/v1/search/structured', {
        method: 'POST',
        body: filters,
        signal,
      }),

    semantic: (
      params: SearchSemanticRequest,
      signal?: AbortSignal
    ) =>
      fetchAPI<SearchSemanticResponse>('/api/v1/search/semantic', {
        method: 'POST',
        body: params,
        signal,
      }),

    detail: (jobId: string, signal?: AbortSignal) =>
      fetchAPI<JobDetailResponse>(`/api/v1/search/${jobId}`, {
        method: 'GET',
        signal,
      }),

    trackView: (jobId: string) =>
      fetchAPI<{ success: boolean; views: number }>(`/api/v1/search/${jobId}/view`, {
        method: 'POST',
      }),
  },

  // ── Trends ─────────────────────────────────
  trends: {
    get: (query = 'Market trends', days = 30, signal?: AbortSignal) =>
      fetchAPI<TrendData>('/api/v1/trends', {
        method: 'POST',
        body: { query, days },
        signal,
      }),

    skills: (days = 30, signal?: AbortSignal) =>
      fetchAPI<SkillsTrendResponse>(`/api/v1/trends/skills?days=${days}`, {
        method: 'GET',
        signal,
      }),

    salaries: (days = 30, signal?: AbortSignal) =>
      fetchAPI<SalaryTrendResponse>(`/api/v1/trends/salaries?days=${days}`, {
        method: 'GET',
        signal,
      }),

    locations: (days = 30, signal?: AbortSignal) =>
      fetchAPI<LocationsTrendResponse>(`/api/v1/trends/locations?days=${days}`, {
        method: 'GET',
        signal,
      }),
  },

  // ── Recommend / Match ──────────────────────
  recommend: {
    match: (params: MatchRequest, signal?: AbortSignal) =>
      fetchAPI<MatchResponse>('/api/v1/recommend/match', {
        method: 'POST',
        body: params,
        signal,
      }),

    analyzeSkills: (params: SkillsAnalysisRequest, signal?: AbortSignal) =>
      fetchAPI<Record<string, unknown>>('/api/v1/recommend/analyze-skills', {
        method: 'POST',
        body: params,
        signal,
      }),
  },

  // ── Natural Language Query ─────────────────
  query: (params: QueryRequest, signal?: AbortSignal) =>
    fetchAPI<QueryResponse>('/api/v1/query', {
      method: 'POST',
      body: params,
      signal,
    }),

  // ── Ingest ─────────────────────────────────
  ingest: {
    trigger: (params: IngestTriggerRequest, signal?: AbortSignal) =>
      fetchAPI<IngestTriggerResponse>('/api/v1/ingest/trigger', {
        method: 'POST',
        body: params,
        signal,
      }),

    runs: (limit = 20, offset = 0, signal?: AbortSignal) =>
      fetchAPI<IngestRunsResponse>(
        `/api/v1/ingest/runs?limit=${limit}&offset=${offset}`,
        {
          method: 'GET',
          signal,
        }
      ),

    runDetail: (runId: string, signal?: AbortSignal) =>
      fetchAPI<IngestRunsResponse['runs'][number]>(`/api/v1/ingest/runs/${runId}`, {
        method: 'GET',
        signal,
      }),
  },

  // ── Health ─────────────────────────────────
  health: (signal?: AbortSignal) =>
    fetchAPI<HealthResponse>('/health', {
      method: 'GET',
      signal,
    }),
};
