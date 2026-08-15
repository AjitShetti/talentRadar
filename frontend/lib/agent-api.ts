import type { AgentMemory, AgentNextAction } from '@/lib/types';

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

export const agentApi = {
  nextAction: async (token: string): Promise<AgentNextAction> => {
    const res = await fetch(`${API_URL}/api/v1/agent/next-action`, { headers: authHeaders(token) });
    return handleResponse<AgentNextAction>(res, 'Failed to load next action');
  },
  listMemories: async (token: string, memoryType?: string): Promise<{ memories: AgentMemory[]; count: number }> => {
    const qs = memoryType ? `?memory_type=${encodeURIComponent(memoryType)}` : '';
    const res = await fetch(`${API_URL}/api/v1/agent/memories${qs}`, { headers: authHeaders(token) });
    return handleResponse<{ memories: AgentMemory[]; count: number }>(res, 'Failed to load memories');
  },
  remember: async (
    token: string,
    memoryType: string,
    content: string,
    metadata?: Record<string, unknown>,
  ): Promise<AgentMemory> => {
    const res = await fetch(`${API_URL}/api/v1/agent/memories`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({ memory_type: memoryType, content, metadata }),
    });
    return handleResponse<AgentMemory>(res, 'Failed to store memory');
  },
};
