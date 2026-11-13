import RingMeter from '@/components/RingMeter'
import { PracticeHistory } from '@/lib/api'

const KINDS = [
  { key: 'easy', label: 'Easy', tone: 'easy' },
  { key: 'medium', label: 'Medium', tone: 'medium' },
  { key: 'hard', label: 'Hard', tone: 'hard' },
  { key: 'design', label: 'System design', tone: 'design' },
] as const

/** The ring of everything solved, with one arc per kind in proportion, and a tile for each. */
export default function SolvedSummary({ history }: { history: Pick<PracticeHistory, 'solved' | 'available'> }) {
  const { solved, available } = history
  return <RingMeter
    label="Solved"
    total={solved.total}
    unit={solved.total === 1 ? 'problem solved' : 'problems solved'}
    parts={KINDS.map(kind => ({ ...kind, count: solved[kind.key], of: available[kind.key] }))}
  />
}
