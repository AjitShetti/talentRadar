'use client'

import { FormEvent, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import PrepTabs from '@/components/PrepTabs'
import ActivityGraph from '@/components/practice/ActivityGraph'
import ContestSection from '@/components/practice/ContestSection'
import SolvedSummary from '@/components/practice/SolvedSummary'
import StandingCard from '@/components/practice/StandingCard'
import TodayCard from '@/components/practice/TodayCard'
import TopicBars from '@/components/practice/TopicBars'
import RequireAuth from '@/components/RequireAuth'
import { api, ChallengesOverview, OverallBoard, PracticeHistory, PracticeSettings, PracticeToday } from '@/lib/api'
import { isNewToPractice, scoreLine } from '@/lib/practice-stats'

const HISTORY_WEEKS = 53

export default function PracticePage() {
  const [today, setToday] = useState<PracticeToday | null>(null)
  const [history, setHistory] = useState<PracticeHistory | null>(null)
  const [overview, setOverview] = useState<ChallengesOverview | null>(null)
  const [board, setBoard] = useState<OverallBoard | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState({ today: '', history: '', overview: '', board: '' })
  const [busy, setBusy] = useState(false)

  const [settings, setSettings] = useState<PracticeSettings | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [showRepo, setShowRepo] = useState(false)
  const [contestReminder, setContestReminder] = useState(true)
  const [saving, setSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsSaved, setSettingsSaved] = useState(false)

  function adopt(next: PracticeSettings) {
    setSettings(next)
    setDisplayName(next.display_name ?? '')
    setShowRepo(next.show_repo)
    setContestReminder(next.contest_reminder)
  }

  async function loadHistory() {
    try {
      setHistory(await api.practice.history(HISTORY_WEEKS))
      setErrors(current => ({ ...current, history: '' }))
    } catch (err) {
      setErrors(current => ({ ...current, history: err instanceof Error ? err.message : 'Could not load your progress.' }))
    }
  }

  useEffect(() => {
    let live = true
    async function load() {
      const [todayResult, historyResult, overviewResult, settingsResult, boardResult] = await Promise.allSettled([
        api.practice.today(), api.practice.history(HISTORY_WEEKS), api.practice.challenges(), api.practice.settings(), api.practice.overall(),
      ])
      if (!live) return
      const reason = (result: PromiseRejectedResult, fallback: string) => result.reason instanceof Error ? result.reason.message : fallback
      if (todayResult.status === 'fulfilled') setToday(todayResult.value)
      if (historyResult.status === 'fulfilled') setHistory(historyResult.value)
      if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value)
      if (settingsResult.status === 'fulfilled') adopt(settingsResult.value)
      if (boardResult.status === 'fulfilled') setBoard(boardResult.value)
      setErrors({
        today: todayResult.status === 'rejected' ? reason(todayResult, 'Could not load today’s problem.') : '',
        history: historyResult.status === 'rejected' ? reason(historyResult, 'Could not load your progress.') : '',
        overview: overviewResult.status === 'rejected' ? reason(overviewResult, 'Could not load this week’s contest.') : '',
        board: boardResult.status === 'rejected' ? reason(boardResult, 'Could not load where you stand.') : '',
      })
      setLoading(false)
    }
    load()
    return () => { live = false }
  }, [])

  // The page loads section by section, so a link to one of them has to be followed afterwards.
  useEffect(() => {
    if (!loading && window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView()
  }, [loading])

  // `focusId` is where keyboard focus goes afterwards: the button that was pressed is often replaced
  // or disabled by the result, and focus would otherwise fall back to the top of the page.
  async function act(call: () => Promise<PracticeToday>, refreshHistory = false, focusId?: string) {
    setBusy(true)
    setErrors(current => ({ ...current, today: '' }))
    try {
      setToday(await call())
      if (refreshHistory) await loadHistory()
      if (focusId) window.setTimeout(() => document.getElementById(focusId)?.focus(), 0)
    } catch (err) {
      setErrors(current => ({ ...current, today: err instanceof Error ? err.message : 'Something went wrong.' }))
    } finally {
      setBusy(false)
    }
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setSettingsError('')
    setSettingsSaved(false)
    try {
      adopt(await api.practice.saveSettings({ display_name: displayName.trim() || null, show_repo: showRepo, contest_reminder: contestReminder }))
      setSettingsSaved(true)
      // LeetCode's contest line follows the reminder switch.
      api.practice.today().then(setToday).catch(() => undefined)
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Could not save your settings.')
    } finally {
      setSaving(false)
    }
  }

  const todayCard = <TodayCard today={today} error={errors.today} busy={busy} act={act} />
  const newcomer = !!history && isNewToPractice(history)
  const scores = scoreLine(overview?.past ?? [])

  const subhead = today?.kind === 'design'
    ? 'One problem a day and one contest a week. Today you draw a system.'
    : 'One problem a day and one contest a week. DSA is solved on leetcode.com.'

  return <RequireAuth><AppShell>
    <PrepTabs />
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Practice</span>
        <h1>Practise a little, every day<span>.</span></h1>
        <p>{subhead}</p>
      </div>
      <a className="pr-ghost" href="#today">Today&apos;s problem <span aria-hidden="true">↓</span></a>
    </section>

    {loading
      ? <div className="loading-state"><Loader2 className="spin" /> Loading Practice…</div>
      : <>
        {newcomer && todayCard}

        <section className="pr-section pr-stack" aria-label="Your progress">
          {errors.history && <p className="form-error" role="alert">{errors.history}</p>}
          {history && <>
            <ActivityGraph history={history} />
            <div className="pr-duo">
              <SolvedSummary history={history} />
              <TopicBars history={history} />
            </div>
          </>}
          <StandingCard board={board} scores={scores} error={errors.board} />
        </section>

        {!newcomer && todayCard}

        <ContestSection overview={overview} error={errors.overview} contest={today?.contest} />

        <section className="pr-section" id="settings">
          <details className="pr-settings">
            <summary>Leaderboard settings</summary>
            <form className="card-form" onSubmit={saveSettings}>
              <label>Leaderboard name <small>2–32 characters. Leave it empty to appear as {settings?.handle ?? 'builder-xxxx'}.</small>
                <input value={displayName} maxLength={32} onChange={e => { setDisplayName(e.target.value); setSettingsSaved(false) }} placeholder={settings?.handle ?? 'builder-xxxx'} />
              </label>
              <label className="toggle-row"><input type="checkbox" checked={showRepo} onChange={e => { setShowRepo(e.target.checked); setSettingsSaved(false) }} /> Show a link to my repo on the leaderboard</label>
              <label className="toggle-row"><input type="checkbox" checked={contestReminder} onChange={e => { setContestReminder(e.target.checked); setSettingsSaved(false) }} /> Show the weekly contest reminder</label>
              {settingsError && <p className="form-error" role="alert">{settingsError}</p>}
              {settingsSaved && <p className="success-message">Saved.</p>}
              <button type="submit" className="pr-ghost" disabled={saving || !settings}>{saving ? 'Saving…' : 'Save settings'}</button>
            </form>
          </details>
        </section>
      </>}
  </AppShell></RequireAuth>
}
