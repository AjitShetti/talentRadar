// frontend/app/interview/page.tsx
// ─────────────────────────────────────────────────────────────────
// Interview Catalog — lets the user pick a track + difficulty
// then starts a session.  Server Component shell wrapping a
// Client Component for the interactive selection UI.
// ─────────────────────────────────────────────────────────────────

import type { Metadata } from 'next';
import InterviewCatalog from './InterviewCatalog';

export const metadata: Metadata = {
  title: 'Mock Interviews — TalentRadar',
  description:
    'Practice technical interviews with an AI voice interviewer. Choose your track and difficulty to start.',
};

export default function InterviewPage() {
  return <InterviewCatalog />;
}
