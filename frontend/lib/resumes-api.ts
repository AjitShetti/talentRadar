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

export const resumesApi = {
  analyze: async (token: string, data: ResumeAnalyzeRequest): Promise<ResumeAnalyzeResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/analyze`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Resume analysis failed: ${res.status}`);
    return res.json();
  },
  tailor: async (token: string, data: ResumeTailorRequest): Promise<ResumeTailorResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/tailor`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Resume tailoring failed: ${res.status}`);
    return res.json();
  },
  coverLetter: async (token: string, data: CoverLetterRequest): Promise<CoverLetterResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/cover-letter`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Cover letter generation failed: ${res.status}`);
    return res.json();
  },
  gaps: async (token: string, data: ResumeGapsRequest): Promise<ResumeGapsResponse> => {
    const res = await fetch(`${API_URL}/api/v1/resumes/gaps`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Skill gap analysis failed: ${res.status}`);
    return res.json();
  },
};
