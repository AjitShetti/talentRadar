import type { CompanyIntelResponse, CompanySearchResponse } from '@/lib/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

export const companyApi = {
  getById: async (token: string, companyId: string): Promise<CompanyIntelResponse> => {
    const res = await fetch(`${API_URL}/api/v1/company-intel/${companyId}`, { headers: authHeaders(token) });
    if (!res.ok) throw new Error(`Failed to load company: ${res.status}`);
    return res.json();
  },
  search: async (token: string, name: string): Promise<CompanySearchResponse> => {
    const res = await fetch(`${API_URL}/api/v1/company-intel?name=${encodeURIComponent(name)}`, {
      headers: authHeaders(token),
    });
    if (!res.ok) throw new Error(`Company search failed: ${res.status}`);
    return res.json();
  },
};
