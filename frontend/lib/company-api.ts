import type { CompanyIntelResponse, CompanySearchResponse } from '@/lib/types';

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

export const companyApi = {
  getById: async (token: string, companyId: string): Promise<CompanyIntelResponse> => {
    const res = await fetch(`${API_URL}/api/v1/company-intel/${companyId}`, { headers: authHeaders(token) });
    return handleResponse<CompanyIntelResponse>(res, 'Failed to load company');
  },
  search: async (token: string, name: string): Promise<CompanySearchResponse> => {
    const res = await fetch(`${API_URL}/api/v1/company-intel?name=${encodeURIComponent(name)}`, {
      headers: authHeaders(token),
    });
    return handleResponse<CompanySearchResponse>(res, 'Company search failed');
  },
};
