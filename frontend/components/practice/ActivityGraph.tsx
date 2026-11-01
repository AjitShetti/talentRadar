'use client'

import { useEffect, useRef } from 'react'
import FlapText from '@/components/FlapText'
import { PracticeDayCell, PracticeHistory } from '@/lib/api'
import { heatColumns, heatLevel, monthMarks } from '@/lib/practice-stats'

function cellLabel(cell: PracticeDayCell, today: boolean) {
  const what = cell.done ? (cell.kind === 'design' ? 'system design' : cell.difficulty ?? 'practised') : today ? 'today' : 'not practised'
  return `${cell.day}: ${what}${cell.contest ? ', contest entry' : ''}`
}

/** The year, as squares. It scrolls inside its card on a phone, opened at the newest week. */
export default function ActivityGraph({ history }: { history: PracticeHistory }) {
  const scroller = useRef<HTMLDivElement>(null)
  const columns = heatColumns(history.days)
  const marks = monthMarks(history.days)
  const practised = history.days.filter(day => day.done).length
  const newest = history.days[history.days.length - 1]?.day

  useEffect(() => {
    const element = scroller.current
    if (element) element.scrollLeft = element.scrollWidth
  }, [columns.length])

  return <section className="pr-card" aria-label="Activity">
    <div className="pr-heat-top">
      <p className="pr-heat-count"><b><FlapText value={practised} /></b> {practised === 1 ? 'day' : 'days'} practised in the past year</p>
      <div className="pr-heat-stats">
        <span>Longest streak: <b>{history.counts.longest_run}</b></span>
        <span>Current streak: <b>{history.counts.current_streak}</b></span>
      </div>
    </div>
    <div className="pr-heat-scroll" ref={scroller}>
      <div className="pr-heat-inner">
        <div className="pr-heat" role="grid" aria-label="Practice over the past year" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}>
          {columns.map((column, c) => <span role="row" style={{ display: 'contents' }} key={c}>
            {column.map((cell, r) => {
              if (!cell) return <i key={r} data-pad="" aria-hidden="true" />
              const today = cell.day === newest
              const label = cellLabel(cell, today)
              return <i key={cell.day} role="gridcell" data-l={heatLevel(cell)} data-today={today ? '' : undefined} aria-label={label} title={label} />
            })}
          </span>)}
        </div>
        <div className="pr-heat-months" aria-hidden="true">
          {marks.map(mark => <span key={`${mark.label}-${mark.leftPercent}`} style={{ left: `${mark.leftPercent}%` }}>{mark.label}</span>)}
        </div>
      </div>
    </div>
    <div className="pr-heat-legend" aria-hidden="true">
      Easier
      <i data-l="0" /><i data-l="1" /><i data-l="2" /><i data-l="3" />
      Harder
    </div>
  </section>
}
