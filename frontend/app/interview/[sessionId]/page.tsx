// frontend/app/interview/[sessionId]/page.tsx
// ─────────────────────────────────────────────────────────────────
// Live interview session page: server shell with metadata.
// ─────────────────────────────────────────────────────────────────

import type { Metadata } from 'next';
import InterviewSession from './InterviewSession';

interface Props {
  params: Promise<{ sessionId: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { sessionId } = await params;
  return {
    title: `Interview Session | TalentRadar`,
    description: `Active mock interview session ${sessionId}`,
  };
}

export default async function SessionPage({ params }: Props) {
  const { sessionId } = await params;
  return <InterviewSession sessionId={sessionId} />;
}
