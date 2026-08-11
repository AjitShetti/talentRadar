import type { ProfileResponse, ProfileUpsertRequest } from '@/lib/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

export const profileApi = {
  get: async (token: string): Promise<ProfileResponse> => {
    const res = await fetch(`${API_URL}/api/v1/profile/`, { headers: authHeaders(token) });
    if (!res.ok) throw new Error(`Failed to load profile: ${res.status}`);
    return res.json();
  },
  upsert: async (token: string, data: ProfileUpsertRequest): Promise<ProfileResponse> => {
    const res = await fetch(`${API_URL}/api/v1/profile/`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`Failed to save profile: ${res.status}`);
    return res.json();
  },
};
