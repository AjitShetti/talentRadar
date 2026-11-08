'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ScoreBars } from '@/components/overview/Charts'
import { api, SkillsFocus } from '@/lib/api'

type Dimension = { key: string; label: string; score: number; weakest: boolean }
type TrackStat = { track: string; label: string; score: number; sessions: number }
type TrendPoint = { label: string; score: number; date: string | null; completed: boolean }
/** What the dashboard reads of the mock interviews (services/interviews.py::interview_insights). */
export type InterviewInsights = { sessions_analyzed: number; questions_analyzed: number; average_score: number; delta: number; abandoned: number; trend: TrendPoint[]; dimensions: Dimension[]; weakest_dimension: { key: string; label: string; score: number; hint: string } | null; tracks: TrackStat[]; weakest_track: TrackStat | null; focus: string[] }

/** Where the mocks say the user is strong, where they are weak, and what to do about it. */
export function StrengthsAndGaps({ interviews, focus }: { interviews: InterviewInsights | null | undefined; focus: SkillsFocus | null | undefined }) {
  const dims = [...(interviews?.dimensions ?? [])].sort((a, b) => b.score - a.score)
  const strong = dims.slice(0, 2).filter(d => d.score >= 60)
  const weak = dims.length > 2 ? dims.slice(-2).reverse() : dims.filter(d => d.weakest)
  const tips = [
    ...(interviews?.weakest_dimension?.hint ? [interviews.weakest_dimension.hint] : []),
    ...(interviews?.focus ?? []),
  ].slice(0, 3)
  const gaps = focus?.items?.slice(0, 3) ?? []
  if (dims.length === 0 && gaps.length === 0) return null

  return <section className="ov2-section" aria-labelledby="ov2-strengths">
    <div className="ov2-head"><h2 id="ov2-strengths">Strengths and gaps</h2>{interviews && interviews.sessions_analyzed > 0 && <span>From {interviews.questions_analyzed} mock answers</span>}</div>
    <div className="ov2-split">
      {dims.length > 0 && <div className="ov2-card ov2-pad">
        <h3 className="ov2-sub">How your answers score</h3>
        <ScoreBars rows={dims.map(d => ({ key: d.key, label: d.label, score: d.score, tone: strong.some(s => s.key === d.key) ? 'good' : weak.some(w => w.key === d.key) ? 'weak' : 'plain' }))} />
        <div className="ov2-chips">
          {strong.map(d => <span key={d.key} data-tone="good">Strong: {d.label.toLowerCase()}</span>)}
          {weak.map(d => <span key={d.key} data-tone="weak">Work on: {d.label.toLowerCase()}</span>)}
        </div>
      </div>}
      <div className="ov2-card ov2-pad">
        <h3 className="ov2-sub">How to improve</h3>
        <ul className="ov2-tips">
          {tips.map(tip => <li key={tip}>{tip}</li>)}
          {gaps.map(gap => <li key={gap.title}><b>{focus?.kind === 'resume_improvements' ? gap.title : `Learn ${gap.title}`}.</b> {gap.detail}</li>)}
          {tips.length === 0 && gaps.length === 0 && <li>Run a mock interview and we&apos;ll tell you exactly what to work on.</li>}
        </ul>
        <div className="ov2-tip-actions">
          <Link className="outline-button" href="/interview">Practise an answer</Link>
          {gaps.length > 0 && focus?.kind === 'resume_improvements' && <Link className="text-button" href="/resume-studio">Fix it in Resume studio</Link>}
        </div>
      </div>
    </div>
  </section>
}

/**
 * Strengths and gaps on the copilot page, read from the dashboard. A failed read
 * shows nothing: the copilot below works without it.
 */
export default function CopilotStrengths() {
  const [data, setData] = useState<{ interviews: InterviewInsights | null; focus: SkillsFocus | null } | null>(null)
  useEffect(() => {
    api.dashboard()
      .then(d => setData({ interviews: (d.interviews as InterviewInsights | null) ?? null, focus: (d.skills_focus as SkillsFocus | null) ?? null }))
      .catch(() => setData(null))
  }, [])
  if (!data) return null
  return <StrengthsAndGaps interviews={data.interviews} focus={data.focus} />
}
