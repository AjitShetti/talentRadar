// frontend/lib/applications-api.ts
import type {
  JobApplication,
  ApplicationListResponse,
  ApplicationCreateRequest,
  ApplicationUpdateRequest,
} from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string) {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || 'Request failed');
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export const applicationsApi = {
  list: (token: string, statusFilter?: string): Promise<ApplicationListResponse> =>
    fetch(
      `${API_BASE}/api/v1/applications/${statusFilter ? `?status=${statusFilter}` : ''}`,
      { headers: authHeaders(token) }
    ).then((r) => handleResponse<ApplicationListResponse>(r)),

  create: (token: string, body: ApplicationCreateRequest): Promise<JobApplication> =>
    fetch(`${API_BASE}/api/v1/applications/`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(body),
    }).then((r) => handleResponse<JobApplication>(r)),

  update: (token: string, id: string, body: ApplicationUpdateRequest): Promise<JobApplication> =>
    fetch(`${API_BASE}/api/v1/applications/${id}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify(body),
    }).then((r) => handleResponse<JobApplication>(r)),

  remove: (token: string, id: string): Promise<void> =>
    fetch(`${API_BASE}/api/v1/applications/${id}`, {
      method: 'DELETE',
      headers: authHeaders(token),
    }).then((r) => handleResponse<void>(r)),
};
