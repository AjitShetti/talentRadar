import type { AgentMemory, AgentNextAction } from '@/lib/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function authHeaders(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

export const agentApi = {
  nextAction: async (token: string): Promise<AgentNextAction> => {
    const res = await fetch(`${API_URL}/api/v1/agent/next-action`, { headers: authHeaders(token) });
    if (!res.ok) throw new Error(`Failed to load next action: ${res.status}`);
    return res.json();
  },
  listMemories: async (token: string, memoryType?: string): Promise<{ memories: AgentMemory[]; count: number }> => {
    const qs = memoryType ? `?memory_type=${encodeURIComponent(memoryType)}` : '';
    const res = await fetch(`${API_URL}/api/v1/agent/memories${qs}`, { headers: authHeaders(token) });
    if (!res.ok) throw new Error(`Failed to load memories: ${res.status}`);
    return res.json();
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
    if (!res.ok) throw new Error(`Failed to store memory: ${res.status}`);
    return res.json();
  },
};
