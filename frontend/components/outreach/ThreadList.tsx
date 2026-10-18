'use client'

import type { ThreadSummary } from '@/lib/api'
import { initials, STAGE_LABEL, STAGE_TABS, stamp, threadTone } from '@/lib/outreach'
import { usePersistentState } from '@/lib/persistent-state'

type Tab = (typeof STAGE_TABS)[number]

const isTab = (value: string): value is Tab => (STAGE_TABS as readonly string[]).includes(value)
const tabLabel = (tab: Tab) => (tab === 'all' ? 'All' : STAGE_LABEL[tab])

// The mono date on a row: what is owed, else the last thing that happened.
function rowDate(t: ThreadSummary): string {
  if (t.due && t.next_due_at) return `Due ${stamp(t.next_due_at)}`
  if (t.last_touch_at) {
    const when = stamp(t.last_touch_at)
    if (t.stage === 'waiting') return `Wrote ${when}`
    if (t.stage === 'replied') return `Replied ${when}`
    return when
  }
  return `Added ${stamp(t.created_at)}`
}

type Props = { threads: ThreadSummary[]; onOpen: (thread: ThreadSummary) => void }

// Every thread the user has, behind a status rail. The chosen tab is remembered.
export default function ThreadList({ threads, onOpen }: Props) {
  const [stored, setTab] = usePersistentState<string>('outreach.tab', 'all')
  const tab: Tab = isTab(stored) ? stored : 'all'
  const shown = tab === 'all' ? threads : threads.filter(t => t.stage === tab)

  return <>
    <div className="status-tabs" role="group" aria-label="Filter threads by status">
      {STAGE_TABS.map(name =>
        <button type="button" key={name} className={tab === name ? 'selected' : ''} aria-pressed={tab === name} onClick={() => setTab(name)}>
          {tabLabel(name)}
        </button>)}
    </div>

    {!threads.length
      ? <div className="empty-state">
          <h2>No threads yet.</h2>
          <p>Pick a company on the right, or add one by name.</p>
        </div>
      : !shown.length
        ? <div className="empty-state"><h2>No {tabLabel(tab).toLowerCase()} threads.</h2></div>
        : <div>
            {shown.map(t =>
              <button type="button" className="or-row" data-tone={threadTone(t)} key={t.id} onClick={() => onOpen(t)}>
                <span className="or-lamp" aria-hidden />
                <span className="or-mark" aria-hidden>{initials(t.company_name)}</span>
                <span className="or-main">
                  <strong>{t.company_name}</strong>
                  <span>{t.lead ? t.lead.name : 'No one added yet'} · {STAGE_LABEL[t.stage]}</span>
                </span>
                <span className="or-date">{rowDate(t)}</span>
              </button>)}
          </div>}
  </>
}
