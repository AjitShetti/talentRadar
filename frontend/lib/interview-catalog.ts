// frontend/lib/interview-catalog.ts
// ─────────────────────────────────────────────────────────────────
// Static catalog of tracks and difficulty levels.
// This is purely client-side data - no API call needed.
// ─────────────────────────────────────────────────────────────────

import type { TrackMeta, DifficultyMeta } from './interview-types';

export const TRACKS: TrackMeta[] = [
  {
    id: 'python_dsa',
    label: 'Python & DSA',
    description: 'Data structures, algorithms, complexity analysis, and Python-specific patterns.',
    icon: 'Code2',
    topics: ['Arrays & Hashing', 'Trees & Graphs', 'Dynamic Programming', 'Sorting & Searching', 'Recursion'],
  },
  {
    id: 'python_backend',
    label: 'Python Backend',
    description: 'REST APIs, async patterns, ORM, concurrency, and production backend design.',
    icon: 'Server',
    topics: ['FastAPI / Django', 'Async / Await', 'SQLAlchemy', 'Caching & Redis', 'Testing'],
  },
  {
    id: 'sql',
    label: 'SQL & Databases',
    description: 'Query design, indexing, transactions, normalization, and schema design.',
    icon: 'Database',
    topics: ['Joins & Aggregations', 'Indexes', 'Transactions', 'Schema Design', 'Query Optimization'],
  },
  {
    id: 'system_design',
    label: 'System Design',
    description: 'Scalable architectures, distributed systems, trade-offs, and component design.',
    icon: 'Network',
    topics: ['Scaling Strategies', 'CAP Theorem', 'Caching Layers', 'Message Queues', 'API Design'],
  },
];

export const DIFFICULTIES: DifficultyMeta[] = [
  {
    id: 'beginner',
    label: 'Beginner',
    questions: 5,
  },
  {
    id: 'mid',
    label: 'Mid-Level',
    questions: 5,
  },
  {
    id: 'senior',
    label: 'Senior',
    questions: 5,
  },
];

export function getTrack(id: string): TrackMeta | undefined {
  return TRACKS.find((t) => t.id === id);
}

export function getDifficulty(id: string): DifficultyMeta | undefined {
  return DIFFICULTIES.find((d) => d.id === id);
}

/** Score colour thresholds for the results page. */
export function scoreColour(score: number): string {
  if (score >= 80) return '#22c55e'; // green
  if (score >= 60) return '#f59e0b'; // amber
  return '#FF4500';                  // brand orange / fail
}

export function scoreLabel(score: number): string {
  if (score >= 80) return 'Excellent';
  if (score >= 60) return 'Good';
  if (score >= 40) return 'Needs Work';
  return 'Keep Practising';
}
