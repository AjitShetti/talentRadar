'use client'

import { useEffect, useRef, useState } from 'react'
import FlapText from '@/components/FlapText'
import SolvedSummary from '@/components/practice/SolvedSummary'
import RingMeter from '@/components/RingMeter'
import { ActivityDay, ActivityKind, api, YearActivity } from '@/lib/api'
import { heatColumns, monthMarks } from '@/lib/practice-stats'

type Lens = 'all' | ActivityKind

const LENSES: Array<{ key: ActivityKind; label: string; one: string; many: string }> = [
  { key: 'problems', label: 'Problems', one: 'problem', many: 'problems' },
  { key: 'applications', label: 'Applications', one: 'application', many: 'applications' },
  { key: 'interviews', label: 'Mock interviews', one: 'mock interview', many: 'mock interviews' },
]

const count = (day: ActivityDay, lens: Lens) =>
  lens === 'all' ? day.problems + day.applications + day.interviews : day[lens]

/** All three together shade by how much was done; one kind alone shows any of it clearly. */
function level(day: ActivityDay, lens: Lens): 0 | 1 | 2 | 3 {
  const n = count(day, lens)
  if (n === 0) return 0
  if (lens === 'all') return Math.min(n, 3) as 1 | 2 | 3
  return n === 1 ? 2 : 3
}

function dayName(iso: string, withYear = false) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: withYear ? 'numeric' : undefined, timeZone: 'UTC' })
}

function dayLine(day: ActivityDay) {
  return LENSES.map(lens => `${day[lens.key]} ${day[lens.key] === 1 ? lens.one : lens.many}`).join(', ')
}

/**
 * The past year on the Overview: problems solved, applications sent and mock interviews finished,
 * as one graph. Pointing at a kind shows only that kind; pointing at a day reads all three for it.
 * A press keeps a kind on. The meters under it are Practice's Solved ring and the mock interviews.
 */
export default function YourYear() {
  const [year, setYear] = useState<YearActivity | null>(null)
  const [pinned, setPinned] = useState<Lens>('all')
  const [peek, setPeek] = useState<Lens | null>(null)
  const [focus, setFocus] = useState<ActivityDay | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    api.activity().then(next => { if (live) setYear(next) }).catch(() => undefined)
    return () => { live = false }
  }, [])

  useEffect(() => {
    const element = scroller.current
    if (element) element.scrollLeft = element.scrollWidth
  }, [year])

  if (!year) return null

  const lens = peek ?? pinned
  const columns = heatColumns(year.days)
  const marks = monthMarks(year.days)
  const newest = year.days[year.days.length - 1]
  const shown = focus ?? newest
  const activeDays = year.days.filter(day => count(day, 'all') > 0).length
  const { interviews } = year

  const lensButton = (key: Lens, label: string, value: number) => <button
    key={key}
    type="button"
    className="ya-lens"
    data-kind={key}
    aria-pressed={pinned === key}
    data-peek={peek === key ? 'true' : undefined}
    onPointerEnter={() => setPeek(key)}
    onPointerLeave={() => setPeek(null)}
    onFocus={() => setPeek(key)}
    onBlur={() => setPeek(null)}
    onClick={() => setPinned(current => current === key && key !== 'all' ? 'all' : key)}
  ><b><FlapText value={value} /></b><span>{label}</span></button>

  return <section className="ov2-section" aria-labelledby="ov2-year">
    <div className="ov2-head">
      <h2 id="ov2-year">Your year</h2>
      <span>Current streak {year.streak.current} · longest {year.streak.longest}</span>
    </div>

    <div className="pr-card ya">
      <div className="ya-lenses" role="group" aria-label="What the graph shows">
        {lensButton('all', activeDays === 1 ? 'day active' : 'days active', activeDays)}
        {LENSES.map(item => lensButton(item.key, item.label, year.totals[item.key]))}
      </div>

      <div className="pr-heat-scroll" ref={scroller} tabIndex={0} role="region" aria-label="The past year, scrolling sideways">
        <div className="pr-heat-inner">
          <div
            className="pr-heat"
            role="grid"
            aria-label="Problems, applications and mock interviews over the past year"
            style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}
            onPointerLeave={() => setFocus(null)}
          >
            {columns.map((column, c) => <span role="row" style={{ display: 'contents' }} key={c}>
              {column.map((cell, r) => {
                if (!cell) return <i key={r} data-pad="" aria-hidden="true" />
                const label = `${dayName(cell.day, true)}: ${dayLine(cell)}`
                return <i
                  key={cell.day}
                  role="gridcell"
                  data-l={level(cell, lens)}
                  data-today={cell.day === newest.day ? '' : undefined}
                  data-on={focus?.day === cell.day ? '' : undefined}
                  aria-label={label}
                  onPointerEnter={() => setFocus(cell)}
                  onClick={() => setFocus(cell)}
                />
              })}
            </span>)}
          </div>
          <div className="pr-heat-months" aria-hidden="true">
            {marks.map(mark => <span key={`${mark.label}-${mark.leftPercent}`} style={{ left: `${mark.leftPercent}%` }}>{mark.label}</span>)}
          </div>
        </div>
      </div>

      <div className="ya-foot">
        <p className="ya-day" aria-live="polite">
          <b>{shown.day === newest.day ? 'Today' : dayName(shown.day, shown.day.slice(0, 4) !== newest.day.slice(0, 4))}</b>
          {LENSES.map(item => <span key={item.key} data-kind={item.key} data-dim={lens !== 'all' && lens !== item.key ? 'true' : undefined}>
            {shown[item.key]} {shown[item.key] === 1 ? item.one : item.many}
          </span>)}
        </p>
        <div className="pr-heat-legend" aria-hidden="true">
          Less
          <i data-l="0" /><i data-l="1" /><i data-l="2" /><i data-l="3" />
          More
        </div>
      </div>
    </div>

    <div className="pr-duo ya-meters">
      <div onPointerEnter={() => setPeek('problems')} onPointerLeave={() => setPeek(null)}>
        <SolvedSummary history={year} />
      </div>
      <div onPointerEnter={() => setPeek('interviews')} onPointerLeave={() => setPeek(null)}>
        <RingMeter
          label="Mock interviews"
          total={interviews.completed}
          unit={interviews.completed === 1 ? 'interview done' : 'interviews done'}
          parts={[
            { key: 'strong', label: 'Strong, 75+', count: interviews.strong, tone: 'strong' },
            { key: 'fair', label: 'Fair, 50–74', count: interviews.fair, tone: 'fair' },
            { key: 'weak', label: 'Needs work', count: interviews.weak, tone: 'weak' },
          ]}
          extra={{ label: 'Average score', value: interviews.average == null ? '–' : String(interviews.average) }}
        />
      </div>
    </div>
  </section>
}
