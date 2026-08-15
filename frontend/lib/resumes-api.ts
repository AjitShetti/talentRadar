import type {
  CoverLetterRequest,
  CoverLetterResponse,
  ResumeAnalyzeRequest,
  ResumeAnalyzeResponse,
  ResumeGapsRequest,
  ResumeGapsResponse,
  ResumeTailorRequest,
  ResumeTailorResponse,
} from '@/lib/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

async function handleResponse<T>(res: Response, fallbackMsg: string): Promise<T> {
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = await res.json();
      detail = body?.detail || null;
    } catch {
      // ignore
    }
    if (res.status === 401) {
      throw new Error(detail || 'Session expired. Please log out and sign in again.');
    }
    throw new Error(detail || `${fallbackMsg} (${res.status})`);
  }
  return res.json();
}

export const resumesApi = {
  analyze: async (token: string, data: ResumeAnalyzeRequest): Promise<ResumeAnalyzeResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/analyze`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<ResumeAnalyzeResponse>(res, 'Resume analysis failed');
  },
  tailor: async (token: string, data: ResumeTailorRequest): Promise<ResumeTailorResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/tailor`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<ResumeTailorResponse>(res, 'Resume tailoring failed');
  },
  coverLetter: async (token: string, data: CoverLetterRequest): Promise<CoverLetterResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/cover-letter`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<CoverLetterResponse>(res, 'Cover letter generation failed');
  },
  gaps: async (token: string, data: ResumeGapsRequest): Promise<ResumeGapsResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/gaps`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<ResumeGapsResponse>(res, 'Skill gap analysis failed');
  },
};
