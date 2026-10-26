'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import AddCompany, { AddChoice } from '@/components/outreach/AddCompany'
import DueList from '@/components/outreach/DueList'
import MetPersonImport from '@/components/outreach/MetPersonImport'
import SuggestionList from '@/components/outreach/SuggestionList'
import ThreadDrawer from '@/components/outreach/ThreadDrawer'
import ThreadList from '@/components/outreach/ThreadList'
import TodayPack from '@/components/outreach/TodayPack'
import { track } from '@/lib/analytics'
import { api, DueItem, OutreachChannelName, OutreachHome, OutreachSource, Suggestion, Suggestions } from '@/lib/api'
import { NEXT_STEP_DONE, parseThreadTarget, threadHref, ThreadTarget } from '@/lib/outreach'
import { metRef } from '@/lib/outreach-met'

const TARGETS = Array.from({ length: 11 }, (_, i) => i) // 0 turns the daily target off; the server caps it at 10
const SNOOZE_DAYS = 3

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)

type ThreadDrawerSlotProps = {
  /** The thread named by ?thread= (with ?person=, and ?draft=1 or ?reply=1), or null when none is open. */
  target: ThreadTarget | null
  onClose: () => void
  /** Call after the drawer changed anything the lists show, so the page reloads them. */
  onChanged: () => void
}

// The drawer is keyed by thread, so opening another thread starts it afresh (focus, load, the first-open draft or reply).
function ThreadDrawerSlot({ target, onClose, onChanged }: ThreadDrawerSlotProps) {
  if (!target) return null
  return <ThreadDrawer
    key={target.threadId}
    threadId={target.threadId}
    focusPersonId={target.personId}
    openDraft={target.mode === 'draft'}
    openReply={target.mode === 'reply'}
    onClose={onClose}
    onChanged={onChanged}
    onDeleted={onChanged}
  />
}

function OutreachBoard() {
  const router = useRouter()
  const params = useSearchParams()
  const target = parseThreadTarget(params)
  const focusCompany = params.get('company')
  // /outreach?add=event_contact:<uuid>: a person met at an event, from the Events drawer.
  const addMet = metRef(params.get('add'))

  const [home, setHome] = useState<OutreachHome | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)
  const [homeLoading, setHomeLoading] = useState(true)
  const [suggestionsLoading, setSuggestionsLoading] = useState(true)
  const [error, setError] = useState('')

  // A failed load keeps whatever had already loaded and says so; the other list is unaffected.
  const loadHome = useCallback(async () => {
    try {
      setHome(await api.outreach.home())
    } catch (err) {
      setError(messageOf(err, 'Could not load your outreach.'))
    } finally {
      setHomeLoading(false)
    }
  }, [])

  const loadSuggestions = useCallback(async () => {
    try {
      setSuggestions(await api.outreach.suggestions())
    } catch (err) {
      setError(messageOf(err, 'Could not load who to reach.'))
    } finally {
      setSuggestionsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadHome()
    void loadSuggestions()
  }, [loadHome, loadSuggestions])

  const openThread = (next: ThreadTarget) => router.push(threadHref(next), { scroll: false })
  const closeThread = () => router.replace('/outreach', { scroll: false })
  // The link is replaced, not pushed, so Back does not start the import again.
  const finishAddMet = useCallback((threadId: string, personId: string) => {
    void loadHome()
    router.replace(threadHref({ threadId, personId, mode: null }), { scroll: false })
  }, [loadHome, router])

  async function saveTarget(daily: number) {
    if (!home) return
    const before = home.target.daily
    const withTarget = (n: number) => (h: OutreachHome | null) => (h ? { ...h, target: { ...h.target, daily: n } } : h)
    setError('')
    setHome(withTarget(daily))
    try {
      const saved = await api.outreach.settings.save(daily)
      setHome(withTarget(saved.daily_target))
    } catch (err) {
      setHome(withTarget(before))
      setError(messageOf(err, 'Could not save your daily target.'))
    }
  }

  // The thread is created (or found, when it already exists) and then opened.
  async function addCompany(payload: { company_id?: string; company_name?: string }, source: OutreachSource): Promise<boolean> {
    setError('')
    try {
      const { thread, created } = await api.outreach.create({ ...payload, source })
      if (created) track('outreach_added', { source })
      const { company_id } = payload
      if (company_id) setSuggestions(cur => cur && { ...cur, suggestions: cur.suggestions.filter(s => s.company_id !== company_id) })
      void loadHome()
      openThread({ threadId: thread.id, personId: null, mode: null })
      return true
    } catch (err) {
      setError(messageOf(err, 'Could not add this company to your outreach.'))
      return false
    }
  }

  const addSuggestion = async (s: Suggestion) => { await addCompany({ company_id: s.company_id }, 'suggestion') }

  const addTyped = (choice: AddChoice) =>
    addCompany('companyId' in choice ? { company_id: choice.companyId } : { company_name: choice.companyName }, 'typed')

  // The row goes at once; if the server refuses, the list is read again from it.
  function dismissSuggestion(s: Suggestion) {
    setSuggestions(cur => cur && { ...cur, suggestions: cur.suggestions.filter(x => x.company_id !== s.company_id) })
    api.outreach.dismissSuggestion(s.company_id).catch(err => {
      setError(messageOf(err, 'Could not hide that suggestion.'))
      void loadSuggestions()
    })
  }

  // "I sent it" records an outbound touch on the channel the person can be reached on.
  // The DueItem does not carry channels, so the thread is read for them.
  async function markSent(item: DueItem) {
    if (!item.person_id) return
    setError('')
    try {
      const detail = await api.outreach.thread(item.thread_id)
      const person = detail.people.find(p => p.id === item.person_id)
      const channel: OutreachChannelName = person?.channels[0]?.channel ?? 'other'
      await api.outreach.touch(item.thread_id, { person_id: item.person_id, channel, direction: 'out' })
      track('outreach_sent', { channel })
      await loadHome()
    } catch (err) {
      setError(messageOf(err, 'Could not record that.'))
    }
  }

  async function snooze(item: DueItem) {
    if (!item.person_id) return
    setError('')
    try {
      await api.outreach.updatePerson(item.thread_id, item.person_id, { snooze_days: SNOOZE_DAYS })
      await loadHome()
    } catch (err) {
      setError(messageOf(err, 'Could not snooze that.'))
    }
  }

  // A next step the user has done: its text and date both go, so it stops being owed.
  async function finishStep(item: DueItem) {
    setError('')
    try {
      await api.outreach.update(item.thread_id, NEXT_STEP_DONE)
      await loadHome()
    } catch (err) {
      setError(messageOf(err, 'Could not mark that done.'))
    }
  }

  const due = home?.due ?? []
  const threads = home?.threads ?? []
  const daily = home?.target.daily ?? 0
  const reached = home?.target.reached_today ?? 0

  return <>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Outreach &middot; Bengaluru</span>
        <h1>Start conversations<span>.</span></h1>
        <p>Who to reach, how, and when to follow up. Nothing is ever sent for you.</p>
        {home && <div className="or-target">
          <span className="or-ticks" aria-hidden>
            {Array.from({ length: Math.min(daily, 10) }, (_, i) => <i key={i} data-on={i < reached ? '' : undefined} />)}
          </span>
          <span>{daily === 0 ? 'Daily target off' : `${reached} of ${daily} reached today`}</span>
          <label>
            Daily target
            <select value={daily} onChange={e => saveTarget(Number(e.target.value))}>
              {TARGETS.map(n => <option key={n} value={n}>{n === 0 ? 'Off' : n}</option>)}
            </select>
          </label>
        </div>}
      </div>
      <div className="tracker-total"><strong><FlapText value={home?.conversations_started ?? 0} /></strong><span>conversations started</span></div>
    </section>

    {error && <p className="form-error" role="alert">{error}</p>}

    {addMet && <MetPersonImport key={addMet} reference={addMet} onDone={finishAddMet} onCancel={closeThread} />}

    <TodayPack onChanged={() => { void loadHome() }} />

    <div className="or-layout">
      <div>
        {due.length > 0 && <section className="or-section">
          <div className="or-head"><h2>Due today</h2><span className="or-rule" /><span className="or-note">{due.length}</span></div>
          <DueList
            items={due}
            onDraft={item => openThread({ threadId: item.thread_id, personId: item.person_id, mode: 'draft' })}
            onSent={markSent}
            onReplied={item => openThread({ threadId: item.thread_id, personId: item.person_id, mode: 'reply' })}
            onSnooze={snooze}
            onStepDone={finishStep}
            onOpen={item => openThread({ threadId: item.thread_id, personId: null, mode: null })}
          />
        </section>}

        <section className="or-section">
          <div className="or-head"><h2>Your threads</h2><span className="or-rule" />{home && <span className="or-note">{threads.length}</span>}</div>
          {home
            ? <ThreadList threads={threads} onOpen={t => openThread({ threadId: t.id, personId: null, mode: null })} />
            : homeLoading && <div className="loading-state"><Loader2 className="spin" /> Loading your threads&hellip;</div>}
        </section>
      </div>

      <aside className="or-side">
        <section className="or-section">
          <div className="or-head"><h2>Who to reach</h2><span className="or-rule" /><span className="or-note">Ranked &middot; no AI</span></div>
          <AddCompany onAdd={addTyped} />
          {suggestionsLoading
            ? <div className="loading-state"><Loader2 className="spin" /> Ranking companies&hellip;</div>
            : <SuggestionList data={suggestions} focusId={focusCompany} onAdd={addSuggestion} onDismiss={dismissSuggestion} />}
        </section>
      </aside>
    </div>

    <ThreadDrawerSlot target={target} onClose={closeThread} onChanged={() => { void loadHome() }} />
  </>
}

export default function OutreachPage() {
  return <RequireAuth><AppShell>
    <Suspense fallback={<div className="loading-state"><Loader2 className="spin" /> Loading outreach&hellip;</div>}>
      <OutreachBoard />
    </Suspense>
  </AppShell></RequireAuth>
}
