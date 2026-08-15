import type { ProfileResponse, ProfileUpsertRequest } from '@/lib/types';

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
      // ignore json parse error
    }
    if (res.status === 401) {
      throw new Error(detail || 'Session expired. Please log out and sign in again.');
    }
    throw new Error(detail || `${fallbackMsg} (${res.status})`);
  }
  return res.json();
}

export const profileApi = {
  get: async (token: string): Promise<ProfileResponse> => {
    const res = await fetch(`${API_URL}/api/v1/profile/`, { headers: authHeaders(token) });
    return handleResponse<ProfileResponse>(res, 'Failed to load profile');
  },
  upsert: async (token: string, data: ProfileUpsertRequest): Promise<ProfileResponse> => {
    const res = await fetch(`${API_URL}/api/v1/profile/`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    return handleResponse<ProfileResponse>(res, 'Failed to save profile');
  },
};
