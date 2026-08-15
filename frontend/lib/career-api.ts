import type { CareerRecommendRequest, CareerRecommendResponse, WeaknessesResponse } from '@/lib/types';

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

export const careerApi = {
  weaknesses: async (token: string): Promise<WeaknessesResponse> => {
    const res = await fetch(`${API_URL}/api/v1/career/weaknesses`, { headers: authHeaders(token) });
    return handleResponse<WeaknessesResponse>(res, 'Failed to load weaknesses');
  },
  recommend: async (token: string, data: CareerRecommendRequest): Promise<CareerRecommendResponse> => {
    const res = await fetch(`${API_URL}/api/v1/career/recommend`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<CareerRecommendResponse>(res, 'Failed to generate recommendations');
  },
};
