import type { CareerRecommendRequest, CareerRecommendResponse, WeaknessesResponse } from '@/lib/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

export const careerApi = {
  weaknesses: async (token: string): Promise<WeaknessesResponse> => {
    const res = await fetch(`${API_URL}/api/v1/career/weaknesses`, { headers: authHeaders(token) });
    if (!res.ok) throw new Error(`Failed to load weaknesses: ${res.status}`);
    return res.json();
  },
  recommend: async (token: string, data: CareerRecommendRequest): Promise<CareerRecommendResponse> => {
    const res = await fetch(`${API_URL}/api/v1/career/recommend`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Failed to generate recommendations: ${res.status}`);
    return res.json();
  },
};
