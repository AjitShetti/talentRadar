'use client'

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, ReactNode, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bookmark, CalendarDays, Check, Download, Info, List, Loader2, Map as MapIcon, Search, SlidersHorizontal, Trash2, Users, X } from 'lucide-react'
import AppShell from '@/components/AppShell'
import type { EventMapProps } from '@/components/EventMap'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import {
  api, EventCard, EventContact, EventContactIn, EventDetail, EventFormat, EventListParams, EventPage, EventSaveStatus,
  EventSource, ImportReason, ManualEventIn,
} from '@/lib/api'
import { clock, dayLabel, effectiveEndMs, ist, istDay, knownEndMs, MONTHS, pad, startMs, whenLabel } from '@/lib/event-time'
import { FEATURES } from '@/lib/features'
import { usePersistentState } from '@/lib/persistent-state'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'
import { useDialog } from '@/lib/use-dialog'

/**
 * The map is its own chunk: its stylesheet and code ship only when someone picks Map.
 * If even that chunk cannot be fetched the page falls back to the list like any other map failure.
 */
function MapChunkFailed({ onUnavailable }: EventMapProps) {
  useEffect(() => { onUnavailable('The map code could not be loaded.') }, [onUnavailable])
  return null
}
const EventMap = dynamic<EventMapProps>(
  () => import('@/components/EventMap').catch(() => ({ default: MapChunkFailed })),
  { ssr: false, loading: () => <div className="cm-plate" role="status"><Loader2 size={16} className="spin" />Loading map…</div> },
)

const PAGE_SIZE = 100
/** Workflow reads (`.github/workflows/events.yml`): 04:50 and 16:50 IST. */
const READ_TIMES_IST: ReadonlyArray<number> = [4 * 60 + 50, 16 * 60 + 50]

// ── Vocabularies ─────────────────────────────────────────────────────────────
// Mirrors domain/event_relevance.py (FORMATS, TOPICS, TOPIC_LABELS).
const SOURCE_LABEL: Record<EventSource, string> = { luma: 'Luma', gdg: 'GDG', eventbrite: 'Eventbrite', user: 'the organiser\'s page' }
/** How a source reads as a code beside the event, and in the ledger line. */
const SOURCE_NAME: Record<EventSource, string> = { luma: 'Luma', gdg: 'GDG', eventbrite: 'Eventbrite', user: 'Added by you' }
const FORMAT_LABELS: Record<EventFormat, string> = {
  hackathon: 'Hackathon', conference: 'Conference', demo: 'Demo', workshop: 'Workshop', talk: 'Talk',
  mixer: 'Mixer', meetup: 'Meetup', hiring: 'Hiring', other: 'Other',
}
const FORMATS = Object.keys(FORMAT_LABELS) as EventFormat[]
const TOPIC_LABELS: Record<string, string> = {
  ai: 'AI', data: 'Data', cloud: 'Cloud', devops: 'DevOps', web: 'Web', mobile: 'Mobile', security: 'Security',
  product: 'Product', design: 'Design', startups: 'Startups', opensource: 'Open source', hardware: 'Hardware',
  web3: 'Web3', quantum: 'Quantum', languages: 'Languages', career: 'Career',
}
const TOPICS = Object.keys(TOPIC_LABELS)
const topicLabel = (topic: string) => TOPIC_LABELS[topic] ?? topic

type When = 'today' | 'week' | 'weekend' | 'month'
const WHEN_OPTIONS: ReadonlyArray<readonly [When, string]> = [
  ['today', 'Today'], ['week', 'This week'], ['weekend', 'This weekend'], ['month', 'Next 30 days'],
]
type Rail = 'all' | 'saved' | 'going'
type View = 'list' | 'map'

type Filters = { q: string; when: When; topic: string; format: string; online: boolean; hidePaid: boolean; rail: Rail }
type SetFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => void

/** Why a pasted link did not resolve, in the words the manual form opens with (spec 4.4). */
const MANUAL_REASON: Record<ImportReason, string> = {
  unrecognised: 'We read links from Luma, GDG and Eventbrite, and this one is not a page we recognise.',
  luma_not_indexed: 'We read links from Luma, GDG and Eventbrite, and this one isn\'t in our Luma index yet.',
  eventbrite_off: 'Eventbrite links need an API key on our side, and it is not set up.',
  not_readable: 'We could not read that page.',
  daily_cap: 'You have added 10 links today.',
  not_in_bengaluru: 'That event does not look like it is in Bengaluru.',
}

function durationLabel(event: { starts_at: string; ends_at: string | null }): string | null {
  const endMs = knownEndMs(event)
  if (endMs === null) return null
  const minutes = Math.round((endMs - startMs(event)) / 60_000)
  if (minutes < 60) return `${minutes} min`
  if (minutes >= 24 * 60) return `${Math.round(minutes / (24 * 60))} d`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest}` : `${hours} h`
}

/** "Today" and "Tomorrow" beside a day header (given as an IST day number); nothing for any other day. */
function relativeDay(day: number, now: number): string | null {
  const diff = day - istDay(now)
  return diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : null
}

/** The next scheduled read, as "HH:MM", for the empty state. */
function nextReadTime(now: number): string {
  const p = ist(now)
  const minutes = p.hour * 60 + p.minute
  const next = READ_TIMES_IST.find(t => t > minutes) ?? READ_TIMES_IST[0]
  return `${pad(Math.floor(next / 60))}:${pad(next % 60)}`
}

/** When a source was last read: the time if it was today (IST), else the date and time. */
function readLabel(iso: string, now: number): string | null {
  const ms = Date.parse(iso)
  if (!Number.isFinite(ms)) return null
  const p = ist(ms)
  return istDay(ms) === istDay(now) ? clock(p) : `${p.day} ${MONTHS[p.month]} ${clock(p)}`
}

function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

const errorText = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)

function fileSlug(title: string): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 60).replace(/^-+|-+$/g, '')
  return slug || 'event'
}

// ── The page ─────────────────────────────────────────────────────────────────
export default function EventsPage() {
  // useSearchParams needs a Suspense boundary above it; the shell stays outside so the nav never blinks.
  return <RequireAuth><AppShell>
    <Suspense fallback={<div className="loading-state"><Loader2 className="spin" /> Loading events…</div>}>
      <EventsView />
    </Suspense>
  </AppShell></RequireAuth>
}

type Notice = { text: string; kind: 'error' | 'info' }
type ManualDraft = { reason: ImportReason | null; url: string }

function EventsView() {
  const router = useRouter()
  const search = useSearchParams()
  const selectedId = search.get('event')
  const now = useNow()

  const [q, setQ, hydrated] = usePersistentState('events.query', '')
  const [when, setWhen] = usePersistentState<When>('events.when', 'month')
  const [topic, setTopic] = usePersistentState('events.topic', '')
  const [format, setFormat] = usePersistentState('events.format', '')
  const [online, setOnline] = usePersistentState('events.online', false)
  const [hidePaid, setHidePaid] = usePersistentState('events.hidePaid', false)
  const [rail, setRail] = usePersistentState<Rail>('events.rail', 'all')
  const [storedView, setStoredView] = usePersistentState<View>('events.view', 'list')
  // Set when the map cannot be drawn (no WebGL, style or tiles down); the list stands in for it.
  const [mapDown, setMapDown] = useState('')

  const [events, setEvents] = useState<EventCard[]>([])
  const [meta, setMeta] = useState<Pick<EventPage, 'total' | 'counts' | 'sources'> | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [loadError, setLoadError] = useState('')
  const [sheetOpen, setSheetOpen] = useState(false)
  const [manual, setManual] = useState<ManualDraft | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)

  // The search box types faster than the list needs to reload.
  const [debouncedQuery, setDebouncedQuery] = useState(q)
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(q), 250)
    return () => clearTimeout(timer)
  }, [q])

  const filters = useMemo<Filters>(
    () => ({ q, when, topic, format, online, hidePaid, rail }),
    [q, when, topic, format, online, hidePaid, rail],
  )
  const set: SetFilter = useCallback((key, value) => {
    const setters: { [K in keyof Filters]: (next: Filters[K]) => void } = {
      q: setQ, when: setWhen, topic: setTopic, format: setFormat, online: setOnline, hidePaid: setHidePaid, rail: setRail,
    }
    ;(setters[key] as (next: typeof value) => void)(value)
  }, [setQ, setWhen, setTopic, setFormat, setOnline, setHidePaid, setRail])

  // Saved and Going are the user's own commitments, not the directory: an online event or one beyond
  // the window is still theirs, so "Online too" and When do not narrow those two views.
  const ownRail = rail !== 'all'
  const query = useMemo<EventListParams>(() => ({
    when: ownRail ? undefined : when,
    mode: ownRail || online ? 'any' : 'in_person',
    topic: topic || undefined,
    format: (format || undefined) as EventFormat | undefined,
    hidePaid: hidePaid || undefined,
    saved: rail === 'all' ? undefined : rail,
    q: debouncedQuery.trim() || undefined,
    limit: PAGE_SIZE,
  }), [ownRail, when, online, topic, format, hidePaid, rail, debouncedQuery])

  // Only the newest read may land: a slow answer to an old filter must not replace a newer one.
  const readSeq = useRef(0)
  const shownCount = useRef(0)
  shownCount.current = events.length
  const load = useCallback(async (options: { silent?: boolean; append?: boolean } = {}) => {
    const seq = ++readSeq.current
    if (!options.silent && !options.append) setLoadState('loading')
    try {
      const result = await api.events.list({ ...query, offset: options.append ? shownCount.current : undefined })
      if (seq !== readSeq.current) return
      setEvents(current => (options.append ? current.concat(result.events) : result.events))
      setMeta({ total: result.total, counts: result.counts, sources: result.sources })
      setLoadState('ready')
      setLoadError('')
    } catch (err) {
      if (seq !== readSeq.current) return
      if (options.silent || options.append) setNotice({ text: errorText(err, 'Could not refresh the events.'), kind: 'error' })
      else { setLoadError(errorText(err, 'Could not load events.')); setLoadState('error') }
    }
  }, [query])

  useEffect(() => { if (hydrated) void load() }, [hydrated, load])

  const openEvent = useCallback((id: string) => router.push(`/events?event=${encodeURIComponent(id)}`, { scroll: false }), [router])
  const closeDrawer = useCallback(() => router.replace('/events', { scroll: false }), [router])

  // A save made in the drawer or on a row shows at once; the read that follows corrects anything the server saw differently.
  const patchStatus = useCallback((id: string, status: EventSaveStatus | null) => {
    setEvents(current => current.map(e => (e.id === id ? { ...e, my_status: status } : e)))
  }, [])
  const statusChanged = useCallback((id: string, status: EventSaveStatus | null) => {
    patchStatus(id, status)
    void load({ silent: true })
  }, [patchStatus, load])

  const toggleSave = useCallback(async (event: EventCard) => {
    const next: EventSaveStatus | null = event.my_status ? null : 'saved'
    setNotice(null)
    patchStatus(event.id, next)
    try {
      if (next) await api.events.save(event.id, next)
      else await api.events.unsave(event.id)
    } catch (err) {
      setNotice({ text: errorText(err, 'Could not update that event.'), kind: 'error' })
    }
    void load({ silent: true })
  }, [patchStatus, load])

  const importLink = useCallback(async (url: string): Promise<boolean> => {
    setNotice(null)
    try {
      const result = await api.events.importLink(url)
      setSheetOpen(false)
      if ('needs_details' in result) { setManual({ reason: result.reason, url }); return true }
      await load({ silent: true })
      openEvent(result.event.id)
      return true
    } catch (err) {
      setNotice({ text: errorText(err, 'Could not add that link.'), kind: 'error' })
      return false
    }
  }, [load, openEvent])

  const manualAdded = useCallback(async (event: EventDetail) => {
    setManual(null)
    await load({ silent: true })
    openEvent(event.id)
  }, [load, openEvent])

  const deleted = useCallback(() => {
    closeDrawer()
    void load({ silent: true })
  }, [closeDrawer, load])

  const activeCount = (topic ? 1 : 0) + (format ? 1 : 0) + (hidePaid ? 1 : 0) + (ownRail ? 0 : (online ? 1 : 0) + (when !== 'month' ? 1 : 0))
  const anyFilter = activeCount > 0 || Boolean(q.trim())
  const clearFilters = useCallback(() => {
    setQ(''); setWhen('month'); setTopic(''); setFormat(''); setOnline(false); setHidePaid(false)
  }, [setQ, setWhen, setTopic, setFormat, setOnline, setHidePaid])

  const groups = useMemo(() => groupByDay(events), [events])
  const selectedCard = selectedId ? events.find(e => e.id === selectedId) : undefined

  // The user's choice, except that a map that cannot be drawn is always the list.
  const view: View = mapDown ? 'list' : storedView
  const chooseView = useCallback((next: View) => {
    if (next === 'map' && mapDown) return
    setStoredView(next)
  }, [mapDown, setStoredView])
  const showList = useCallback(() => setStoredView('list'), [setStoredView])
  // The map lights the events the user is going to (never a cancelled one) and the open one; a stable Set so the map is not redrawn for nothing.
  const goingIds = useMemo(() => new Set(events.filter(e => e.my_status === 'going' && !e.is_cancelled).map(e => e.id)), [events])
  const moreButton = meta && meta.total > events.length
    ? <button type="button" className="outline-button ev-more" onClick={() => void load({ append: true })}>Show more · {meta.total - events.length} left</button>
    : null

  const addLink = (variant: 'inline' | 'sheet') => <AddByLink variant={variant} onSubmit={importLink} />

  return <>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Events — Bengaluru</span>
        <h1>Where to meet people this week<span>.</span></h1>
        <p>Tech meetups, talks and demo nights from Luma, GDG and Eventbrite. We never register for you.</p>
      </div>
      <div className="tracker-total ev-total"><strong><FlapText value={meta ? meta.counts.upcoming : '—'} /></strong><span>upcoming</span></div>
    </section>
    {meta && <Ledger counts={meta.counts} sources={meta.sources} now={now} />}

    <Toolbar
      filters={filters}
      set={set}
      counts={meta?.counts ?? null}
      activeCount={activeCount}
      view={view}
      mapDown={Boolean(mapDown)}
      onView={chooseView}
      onOpenSheet={() => setSheetOpen(true)}
      addLink={addLink('inline')}
    />

    {manual && <ManualForm draft={manual} onCancel={() => setManual(null)} onAdded={manualAdded} />}
    {notice && <p className={notice.kind === 'error' ? 'form-error' : 'ci-empty-small'} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}
    {mapDown && <p className="cm-fallback" role="status"><Info size={15} aria-hidden="true" /><span>Map unavailable — showing the list.</span><small>{mapDown}</small></p>}

    {loadState === 'loading' && !events.length
      ? <div className="loading-state"><Loader2 className="spin" /> Loading events…</div>
      : loadState === 'error'
        ? <p className="form-error" role="alert">{loadError}</p>
        : !events.length
          ? <EmptyState
              rail={rail}
              anyFilter={anyFilter}
              online={online}
              upcoming={meta ? meta.counts.upcoming : null}
              going={meta ? meta.counts.going : null}
              nextRead={nextReadTime(now)}
              onClear={clearFilters}
              onOnline={() => setOnline(true)}
            />
          : view === 'map'
            ? <>
                <section className="ev-map" aria-label="Events map" data-testid="events-map-stage">
                  <EventMap
                    events={events}
                    selectedId={selectedId}
                    goingIds={goingIds}
                    onSelect={openEvent}
                    onUnavailable={setMapDown}
                    onShowList={showList}
                  />
                </section>
                {moreButton}
              </>
            : <div className="ev-list">
                {groups.map(group => <DayGroup
                  key={group.key}
                  group={group}
                  now={now}
                  selectedId={selectedId}
                  onToggleSave={toggleSave}
                />)}
                {moreButton}
              </div>}

    {selectedId && <>
      <button type="button" className="ci-backdrop cm-over" onClick={closeDrawer} aria-label="Close event details" />
      <EventDrawer
        eventId={selectedId}
        fallback={selectedCard}
        now={now}
        closeOnEscape={!sheetOpen}
        onClose={closeDrawer}
        onStatus={statusChanged}
        onDeleted={deleted}
      />
    </>}
    {sheetOpen && <FilterSheet
      filters={filters}
      set={set}
      count={meta ? meta.total : null}
      onClear={clearFilters}
      onClose={() => setSheetOpen(false)}
      addLink={addLink('sheet')}
    />}
  </>
}

// ── Heading and ledger ───────────────────────────────────────────────────────
/** What the system holds, and when each source was last read. A source that is down says so; the rest still list. */
function Ledger({ counts, sources, now }: { counts: EventPage['counts']; sources: EventPage['sources']; now: number }) {
  return <p className="ev-ledger" aria-label="What we hold">
    <span><b>{counts.this_week}</b> this week</span>
    <span data-live={counts.going > 0 ? '' : undefined}><b>{counts.going}</b> you&apos;re going to</span>
    {sources.map(source => {
      const name = SOURCE_NAME[source.source]
      if (source.status === 'off') return <span key={source.source} className="is-off">{name} off</span>
      if (source.status === 'failing') return <span key={source.source} className="is-down">{name} unavailable</span>
      const read = source.last_read_at ? readLabel(source.last_read_at, now) : null
      return <span key={source.source}>{read ? `${name} read ${read}` : `${name} not read yet`}</span>
    })}
  </p>
}

// ── Toolbar ──────────────────────────────────────────────────────────────────
const OWN_RAIL_NOTE = 'Saved and Going show everything you marked'

function WhenSelect({ value, bare, disabled, onChange }: { value: When; bare?: boolean; disabled?: boolean; onChange: (value: When) => void }) {
  return <label className={bare ? undefined : 'cm-field'}>
    <span>When</span>
    <select data-set={value !== 'month' && !disabled} disabled={disabled} title={disabled ? OWN_RAIL_NOTE : undefined} value={value} onChange={e => onChange(e.target.value as When)}>
      {WHEN_OPTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
    </select>
  </label>
}

function TopicSelect({ value, bare, onChange }: { value: string; bare?: boolean; onChange: (value: string) => void }) {
  return <label className={bare ? undefined : 'cm-field'}>
    <span>Topic</span>
    <select data-set={Boolean(value)} value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Any topic</option>
      {TOPICS.map(id => <option key={id} value={id}>{topicLabel(id)}</option>)}
    </select>
  </label>
}

function FormatSelect({ value, bare, onChange }: { value: string; bare?: boolean; onChange: (value: string) => void }) {
  return <label className={bare ? undefined : 'cm-field'}>
    <span>Format</span>
    <select data-set={Boolean(value)} value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Any format</option>
      {FORMATS.map(id => <option key={id} value={id}>{FORMAT_LABELS[id]}</option>)}
    </select>
  </label>
}

function ToggleChip({ pressed, disabled, onToggle, children }: { pressed: boolean; disabled?: boolean; onToggle: () => void; children: ReactNode }) {
  return <button type="button" className={`ci-chip ci-chip-toggle ${pressed ? 'active' : ''}`} aria-pressed={pressed} disabled={disabled} title={disabled ? OWN_RAIL_NOTE : undefined} onClick={onToggle}>{children}</button>
}

const RAIL: ReadonlyArray<readonly [Rail, string]> = [['all', 'All'], ['saved', 'Saved'], ['going', 'Going']]

function Toolbar({ filters, set, counts, activeCount, view, mapDown, onView, onOpenSheet, addLink }: {
  filters: Filters
  set: SetFilter
  counts: EventPage['counts'] | null
  activeCount: number
  view: View
  mapDown: boolean
  onView: (view: View) => void
  onOpenSheet: () => void
  addLink: ReactNode
}) {
  const railCount = (id: Rail) => (counts ? (id === 'all' ? counts.upcoming : id === 'going' ? counts.going : null) : null)
  return <div className="ev-toolbar">
    <div className="ci-searchbar ev-searchbar">
      <Search size={16} aria-hidden="true" />
      <input
        aria-label="Search events"
        placeholder="Search title or organiser — e.g. Swift, Qiskit, product"
        value={filters.q}
        onChange={e => set('q', e.target.value)}
      />
      {addLink}
    </div>
    <div className="ev-controls">
      <WhenSelect value={filters.when} disabled={filters.rail !== 'all'} onChange={value => set('when', value)} />
      <TopicSelect value={filters.topic} onChange={value => set('topic', value)} />
      <FormatSelect value={filters.format} onChange={value => set('format', value)} />
      <ToggleChip pressed={filters.online} disabled={filters.rail !== 'all'} onToggle={() => set('online', !filters.online)}>Online too</ToggleChip>
      <ToggleChip pressed={filters.hidePaid} onToggle={() => set('hidePaid', !filters.hidePaid)}>Hide paid</ToggleChip>
      <button type="button" className="ci-chip ev-filterbtn" aria-haspopup="dialog" onClick={onOpenSheet}>
        <SlidersHorizontal size={13} aria-hidden="true" />{activeCount > 0 ? `Filters (${activeCount})` : 'Filters'}
      </button>
      <div className="ev-viewswitch" role="group" aria-label="View" data-testid="view-switch">
        <button type="button" aria-pressed={view === 'list'} onClick={() => onView('list')}>
          <List size={13} aria-hidden="true" />List
        </button>
        <button type="button" aria-pressed={view === 'map'} aria-disabled={mapDown} title={mapDown ? 'Map unavailable' : undefined} onClick={() => onView('map')}>
          <MapIcon size={13} aria-hidden="true" />Map
        </button>
      </div>
    </div>
    <div className="status-tabs ev-rail" role="group" aria-label="Your events">
      {RAIL.map(([id, label]) => {
        const count = railCount(id)
        return <button key={id} type="button" className={filters.rail === id ? 'selected' : ''} aria-pressed={filters.rail === id} onClick={() => set('rail', id)}>
          {label}{count !== null && <b>{count}</b>}
        </button>
      })}
    </div>
  </div>
}

/** Paste a Luma, GDG or Eventbrite link. Inline in the search bar on desktop, a field in the filter sheet below 700 px. */
function AddByLink({ variant, onSubmit }: { variant: 'inline' | 'sheet'; onSubmit: (url: string) => Promise<boolean> }) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const url = value.trim()
    if (!url || busy) return
    setBusy(true)
    const ok = await onSubmit(url)
    setBusy(false)
    if (ok) setValue('')
  }
  const input = <input
    aria-label={variant === 'sheet' ? undefined : 'Add an event by link'}
    placeholder="Paste a Luma, GDG or Eventbrite link"
    value={value}
    onChange={e => setValue(e.target.value)}
    maxLength={2048}
  />
  if (variant === 'sheet') {
    return <form className="ev-sheet-add" onSubmit={submit}>
      <label><span>Add by link</span>{input}</label>
      <button type="submit" className="outline-button" disabled={busy || !value.trim()}>{busy ? 'Adding…' : 'Add event'}</button>
    </form>
  }
  return <form className="ev-addlink" onSubmit={submit}>
    {input}
    <button type="submit" disabled={busy || !value.trim()}>{busy ? 'Adding…' : 'Add'}</button>
  </form>
}

// ── Filter sheet (below 700px) ───────────────────────────────────────────────
function FilterSheet({ filters, set, count, onClear, onClose, addLink }: {
  filters: Filters
  set: SetFilter
  count: number | null
  onClear: () => void
  onClose: () => void
  addLink: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const onKeyDown = useDialog(ref, onClose, { modal: true, closeOnEscape: true })
  return <>
    <button type="button" className="cm-sheet-backdrop" onClick={onClose} aria-label="Close filters" />
    <div ref={ref} className="cm-sheet" role="dialog" aria-modal="true" aria-labelledby="ev-sheet-title" tabIndex={-1} onKeyDown={onKeyDown}>
      <span className="cm-sheet-grip" aria-hidden="true" />
      <div className="cm-sheet-head">
        <h2 id="ev-sheet-title">Filters</h2>
        <button type="button" className="ci-close" onClick={onClose} aria-label="Close filters" data-autofocus><X size={18} /></button>
      </div>
      <WhenSelect bare value={filters.when} disabled={filters.rail !== 'all'} onChange={value => set('when', value)} />
      <TopicSelect bare value={filters.topic} onChange={value => set('topic', value)} />
      <FormatSelect bare value={filters.format} onChange={value => set('format', value)} />
      <div className="cm-sheet-toggles">
        <ToggleChip pressed={filters.online} disabled={filters.rail !== 'all'} onToggle={() => set('online', !filters.online)}>Online too</ToggleChip>
        <ToggleChip pressed={filters.hidePaid} onToggle={() => set('hidePaid', !filters.hidePaid)}>Hide paid</ToggleChip>
      </div>
      {addLink}
      <div className="cm-sheet-foot">
        <button type="button" className="cm-clear" onClick={onClear}>Clear all</button>
        <button type="button" className="primary-button" onClick={onClose}>
          {count === null ? 'Show events' : `Show ${count} ${count === 1 ? 'event' : 'events'}`}
        </button>
      </div>
    </div>
  </>
}

// ── List ─────────────────────────────────────────────────────────────────────
type DayGroupData = { key: number; label: string; events: EventCard[] }

function groupByDay(events: EventCard[]): DayGroupData[] {
  const sorted = events.slice().sort((a, b) => startMs(a) - startMs(b))
  const groups: DayGroupData[] = []
  for (const event of sorted) {
    const key = istDay(startMs(event))
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.events.push(event)
    else groups.push({ key, label: dayLabel(ist(startMs(event))), events: [event] })
  }
  return groups
}

function DayGroup({ group, now, selectedId, onToggleSave }: {
  group: DayGroupData
  now: number
  selectedId: string | null
  onToggleSave: (event: EventCard) => void
}) {
  const relative = relativeDay(group.key, now)
  return <div className="ev-group" role="group" aria-label={group.label}>
    <div className="ev-day">{group.label}{relative && <> <small>{relative}</small></>}</div>
    {group.events.map(event => <EventRow
      key={event.id}
      event={event}
      now={now}
      selected={event.id === selectedId}
      onToggleSave={onToggleSave}
    />)}
  </div>
}

/** The lamp: amber for an event the user is going to that is today or tomorrow (it needs them), faint for any other saved one. */
function toneFor(event: EventCard, now: number): 'live' | 'saved' | undefined {
  if (!event.my_status) return undefined
  const soon = istDay(startMs(event)) - istDay(now) <= 1
  return event.my_status === 'going' && soon && !event.is_cancelled ? 'live' : 'saved'
}

function EventRow({ event, now, selected, onToggleSave }: {
  event: EventCard
  now: number
  selected: boolean
  onToggleSave: (event: EventCard) => void
}) {
  const start = ist(startMs(event))
  const duration = durationLabel(event)
  const place = event.is_online ? 'Online' : event.area || event.venue_name
  const meta = [event.organiser_name, place].filter(Boolean).join(' · ')
  const saved = event.my_status !== null
  const format = event.format && event.format !== 'other' ? FORMAT_LABELS[event.format] : null
  return <article
    className={`ev-row ${selected ? 'is-selected' : ''} ${event.is_cancelled ? 'is-cancelled' : ''}`}
    data-tone={toneFor(event, now)}
  >
    <span className="ev-lamp" aria-hidden="true" />
    <span className="ev-time">{clock(start)}{duration && <small>{duration}</small>}</span>
    <Link className="ev-main" href={`/events?event=${encodeURIComponent(event.id)}`} scroll={false} aria-current={selected ? 'true' : undefined}>
      <span className="ev-title">{event.title}</span>
      <span className="ev-meta">{meta}{event.my_status === 'going' && (meta ? " · you're going" : "You're going")}</span>
    </Link>
    <span className="ev-codes">
      {event.is_cancelled && <span className="ev-code is-cancelled">Cancelled</span>}
      {format && <span className="ev-code">{format}</span>}
      {event.topics[0] && <span className="ev-code">{topicLabel(event.topics[0])}</span>}
      {event.is_free && <span className="ev-code">Free</span>}
    </span>
    <button
      type="button"
      className="ev-save"
      aria-pressed={saved}
      aria-label={`Save ${event.title}`}
      title={event.my_status === 'going' ? 'Going — click to remove' : saved ? 'Saved — click to remove' : 'Save this event'}
      onClick={() => onToggleSave(event)}
    ><Bookmark size={16} aria-hidden="true" /></button>
  </article>
}

// ── Empty states ─────────────────────────────────────────────────────────────
function EmptyState({ rail, anyFilter, online, upcoming, going, nextRead, onClear, onOnline }: {
  rail: Rail
  anyFilter: boolean
  online: boolean
  /** The count of upcoming events on record, or null before it is known. */
  upcoming: number | null
  /** How many events the user is going to, or null before it is known. */
  going: number | null
  nextRead: string
  onClear: () => void
  onOnline: () => void
}) {
  if (anyFilter) {
    return <div className="empty-state">
      <CalendarDays size={18} aria-hidden="true" />
      <h2>Nothing matches these filters.</h2>
      <button type="button" className="outline-button" style={{ marginTop: 12 }} onClick={onClear}>Clear filters</button>
    </div>
  }
  // The ledger says they are going to something: never answer "you haven't" over that.
  if (rail !== 'all' && going) {
    return <div className="empty-state">
      <Check size={18} aria-hidden="true" />
      <h2>You&apos;re going to {going} {going === 1 ? 'event' : 'events'}.</h2>
      <p>{going === 1 ? 'It is' : 'They are'} not in this view yet. Reload, or look under All.</p>
    </div>
  }
  if (rail === 'saved') {
    return <div className="empty-state">
      <Bookmark size={18} aria-hidden="true" />
      <h2>You haven&apos;t saved an event.</h2>
      <p>Save one from the list and it shows up on your Overview the day it happens.</p>
    </div>
  }
  if (rail === 'going') {
    return <div className="empty-state">
      <Check size={18} aria-hidden="true" />
      <h2>You haven&apos;t marked an event as going.</h2>
      <p>Mark Going in an event&apos;s details and it shows up on your Overview the day it happens.</p>
    </div>
  }
  if (upcoming && !online) {
    return <div className="empty-state">
      <CalendarDays size={18} aria-hidden="true" />
      <h2>No in-person events in the next 30 days.</h2>
      <button type="button" className="outline-button" style={{ marginTop: 12 }} onClick={onOnline}>Include online events</button>
    </div>
  }
  return <div className="empty-state">
    <CalendarDays size={18} aria-hidden="true" />
    <h2>No events on record yet.</h2>
    <p>The next read is at {nextRead} IST.</p>
  </div>
}

// ── Detail drawer ────────────────────────────────────────────────────────────
type DrawerHeadData = EventCard | EventDetail
/** Changes the open event from the one it is now: a request that finishes late must not undo what happened meanwhile. */
type PatchDetail = (update: (current: EventDetail) => EventDetail) => void

function EventDrawer({ eventId, fallback, now, closeOnEscape, onClose, onStatus, onDeleted }: {
  eventId: string
  fallback: EventCard | undefined
  now: number
  closeOnEscape: boolean
  onClose: () => void
  onStatus: (id: string, status: EventSaveStatus | null) => void
  onDeleted: () => void
}) {
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [error, setError] = useState('')
  const rootRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const onKeyDown = useDialog(rootRef, onClose, { modal: true, closeOnEscape })

  useEffect(() => {
    let cancelled = false
    setDetail(null); setError('')
    bodyRef.current?.scrollTo({ top: 0 })
    api.events.detail(eventId)
      .then(result => { if (!cancelled) setDetail(result) })
      .catch(err => { if (!cancelled) setError(errorText(err, 'Could not load this event.')) })
    return () => { cancelled = true }
  }, [eventId])

  // `?event=<id>#people` lands on the people section once the detail is there.
  const patch = useCallback<PatchDetail>(update => setDetail(current => (current ? update(current) : current)), [])
  const loadedId = detail?.id
  useEffect(() => {
    if (loadedId && window.location.hash === '#people') document.getElementById('people')?.scrollIntoView({ block: 'start' })
  }, [loadedId])

  // The overlay owns the screen, so the page behind it must not scroll.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])

  const head: DrawerHeadData | null = detail ?? (fallback && fallback.id === eventId ? fallback : null)
  const started = head ? now >= startMs(head) : false

  return <aside
    ref={rootRef}
    className="ci-drawer cm-over"
    role="dialog"
    aria-modal="true"
    aria-labelledby="ev-drawer-title"
    tabIndex={-1}
    data-testid="event-drawer"
    onKeyDown={onKeyDown}
  >
    <header className="ci-drawer-head">
      <span className="ci-logo"><CalendarDays size={18} aria-hidden="true" /></span>
      <div className="ci-drawer-title">
        <h2 id="ev-drawer-title">{head ? head.title : 'Loading…'}</h2>
        {head && <Head event={head} now={now} />}
      </div>
      <button type="button" className="ci-close" onClick={onClose} aria-label="Close" data-autofocus><X size={18} /></button>
    </header>

    <div className="ci-drawer-body" ref={bodyRef}>
      {error && <p className="form-error" role="alert" style={{ marginTop: 18 }}>{error}</p>}
      {!detail && !error && <div className="cm-drawer-loading" aria-busy="true"><Loader2 size={16} className="spin" />Loading…</div>}
      {detail && <>
        <Actions detail={detail} started={started} onChange={patch} onStatus={onStatus} onDeleted={onDeleted} />
        {detail.source !== 'user' && <WhySection reasons={detail.reasons} />}
        <HostsSection detail={detail} />
        <AboutSection detail={detail} />
        <AlsoOnSection detail={detail} />
        {detail.my && <YourNote key={detail.id} detail={detail} onChange={patch} />}
        {started && <PeopleSection detail={detail} onChange={patch} onStatus={onStatus} />}
      </>}
    </div>
  </aside>
}

/** When, where and the codes: everything under the title. */
function Head({ event, now }: { event: DrawerHeadData; now: number }) {
  const start = startMs(event)
  const state = event.is_cancelled ? 'cancelled' : now >= effectiveEndMs(event) ? 'ended' : now >= start ? 'started' : null
  return <>
    <p className="ev-when">{whenLabel(event)}{state && ` · ${state}`}</p>
    <Where event={event} />
    <div className="ev-head-codes">
      {event.is_cancelled && <span className="ev-code is-cancelled">Cancelled</span>}
      {event.format && event.format !== 'other' && <span className="ev-code">{FORMAT_LABELS[event.format]}</span>}
      {event.topics.slice(0, 3).map(topic => <span className="ev-code" key={topic}>{topicLabel(topic)}</span>)}
      {event.is_free && <span className="ev-code">Free</span>}
      <span className="ev-code">{SOURCE_NAME[event.source]}</span>
    </div>
  </>
}

/** Venue and area with an honest note on how exact it is, or "Online". */
function Where({ event }: { event: DrawerHeadData }) {
  if (event.is_online) return <div className="ev-where"><span>Online</span></div>
  const place = [event.venue_name, event.area].filter(Boolean).join(' · ')
  const note = event.geo_precision === 'area'
    ? `Area only${event.source === 'luma' ? ' — Luma shows the address after you register' : ''}`
    : null
  if (!place) return <div className="ev-where"><small>Location not stated</small></div>
  return <div className="ev-where"><span>{place}</span>{note && <small>{note}</small>}</div>
}

function Actions({ detail, started, onChange, onStatus, onDeleted }: {
  detail: EventDetail
  started: boolean
  onChange: PatchDetail
  onStatus: (id: string, status: EventSaveStatus | null) => void
  onDeleted: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const status = detail.my?.status ?? null
  const link = externalHref(detail.url)
  const label = SOURCE_LABEL[detail.source]

  const run = async (work: () => Promise<void>, fallback: string) => {
    if (busy) return
    setBusy(true); setMessage('')
    try { await work() } catch (err) { setMessage(errorText(err, fallback)) } finally { setBusy(false) }
  }
  const setStatus = (next: EventSaveStatus | null) => run(async () => {
    if (next === null) {
      await api.events.unsave(detail.id)
      onChange(current => ({ ...current, my: null, my_status: null }))
    } else {
      const my = await api.events.save(detail.id, next)
      onChange(current => ({ ...current, my, my_status: my.status }))
    }
    onStatus(detail.id, next)
  }, 'Could not update this event.')
  const calendar = () => run(() => api.events.downloadIcs(detail.id, `${fileSlug(detail.title)}.ics`), 'Could not download the calendar file.')
  const remove = () => run(async () => {
    if (!window.confirm('Delete this event? This also removes the people you noted at it.')) return
    await api.events.deleteEvent(detail.id)
    onDeleted()
  }, 'Could not delete this event.')

  const register = !started && !detail.is_cancelled
  return <div className="ev-actions-wrap">
    <div className="ev-actions">
      {link && <a className="primary-button" href={link} {...EXTERNAL_LINK_PROPS}>
        {register ? `Register on ${label}` : `Open on ${label}`} <span aria-hidden="true">↗</span>
      </a>}
      <button type="button" className="ev-toggle" aria-pressed={status === 'going'} disabled={busy} onClick={() => setStatus(status === 'going' ? 'saved' : 'going')}>
        <Check size={13} aria-hidden="true" />Going
      </button>
      {status !== 'going' && <button type="button" className="ev-toggle" aria-pressed={status === 'saved'} disabled={busy} onClick={() => setStatus(status === 'saved' ? null : 'saved')}>
        <Bookmark size={13} aria-hidden="true" />Save
      </button>}
      <button type="button" className="ev-toggle" disabled={busy} onClick={calendar}>
        <Download size={13} aria-hidden="true" />Add to calendar (.ics)
      </button>
      {detail.source === 'user' && <button type="button" className="ev-toggle is-danger" disabled={busy} onClick={remove}>
        <Trash2 size={13} aria-hidden="true" />Delete event
      </button>}
    </div>
    {message && <p className="form-error" role="alert">{message}</p>}
  </div>
}

function WhySection({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null
  return <section className="ci-section">
    <h3>Why it&apos;s listed</h3>
    <p className="ev-why">{reasons.map(reason => <span key={reason}>{reason}</span>)}</p>
  </section>
}

/** The organiser, the hosts the source names, and the companies on the map they match. */
function HostsSection({ detail }: { detail: EventDetail }) {
  type Host = { name: string; organiser: boolean; companyId: string | null }
  const hosts: Host[] = []
  const add = (name: string | null | undefined, organiser: boolean, companyId: string | null) => {
    const clean = (name || '').trim()
    if (!clean) return
    const existing = hosts.find(h => h.name.toLowerCase() === clean.toLowerCase())
    if (existing) { existing.companyId = existing.companyId ?? companyId; existing.organiser = existing.organiser || organiser; return }
    hosts.push({ name: clean, organiser, companyId })
  }
  add(detail.organiser_name, true, null)
  detail.hosts.forEach(name => add(name, false, null))
  detail.companies.forEach(company => add(company.name, company.role === 'organiser', company.id))
  if (!hosts.length) return null
  const anyCompany = hosts.some(h => h.companyId)
  return <section className="ci-section ev-hosts">
    <h3><Users size={13} aria-hidden="true" />Hosted by</h3>
    <p>
      {hosts.map((host, index) => <span key={host.name}>
        {index > 0 && ' · '}
        {host.companyId ? <Link href={`/company-intel?company=${encodeURIComponent(host.companyId)}`}>{host.name}</Link> : host.name}
        {host.organiser && ' (organiser)'}
      </span>)}
      {anyCompany && <> <span className="ci-empty-small">— on the company map</span></>}
    </p>
  </section>
}

function AboutSection({ detail }: { detail: EventDetail }) {
  const snippet = (detail.snippet || '').trim()
  if (!snippet) return null
  const shown = snippet.length > 200 ? `${snippet.slice(0, 200).trimEnd()}…` : snippet
  const link = detail.source === 'user' ? null : externalHref(detail.url)
  return <section className="ci-section">
    <h3>About</h3>
    <p>{shown}{link && <> <a className="purple-link" href={link} {...EXTERNAL_LINK_PROPS}>more on {SOURCE_LABEL[detail.source]} ↗</a></>}</p>
  </section>
}

function AlsoOnSection({ detail }: { detail: EventDetail }) {
  const links = detail.also_on
    .map(other => ({ source: other.source, href: externalHref(other.url) }))
    .filter((other): other is { source: EventSource; href: string } => other.href !== null)
  if (!links.length) return null
  return <section className="ci-section">
    <h3>Also on</h3>
    <div className="ci-links">
      {links.map(other => <a key={other.href} className="ci-link" href={other.href} {...EXTERNAL_LINK_PROPS}>{SOURCE_NAME[other.source]} <span aria-hidden="true">↗</span></a>)}
    </div>
  </section>
}

/** The user's own note on an event they saved. It saves when the box loses focus. */
function YourNote({ detail, onChange }: { detail: EventDetail; onChange: PatchDetail }) {
  const saved = detail.my?.note ?? ''
  const [draft, setDraft] = useState(saved)
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const status = detail.my?.status
  if (!status) return null
  const save = async () => {
    if (draft === saved) return
    setState('saving')
    try {
      const my = await api.events.save(detail.id, status, draft)
      onChange(current => ({ ...current, my }))
      setState('saved')
    } catch {
      setState('error')
    }
  }
  return <section className="ci-section">
    <h3>Your note</h3>
    <textarea
      className="ev-note"
      aria-label="Your note"
      maxLength={2000}
      value={draft}
      onChange={e => { setDraft(e.target.value); setState('idle') }}
      onBlur={() => void save()}
    />
    <p className="ci-empty-small" role="status">
      {state === 'saving' ? 'Saving…' : state === 'saved' ? 'Saved' : state === 'error' ? 'Could not save your note.' : 'Only you can see this.'}
    </p>
  </section>
}

// ── People you met ───────────────────────────────────────────────────────────
function PeopleSection({ detail, onChange, onStatus }: {
  detail: EventDetail
  onChange: PatchDetail
  onStatus: (id: string, status: EventSaveStatus | null) => void
}) {
  const [error, setError] = useState('')
  const people = detail.people

  const added = async (person: EventContact) => {
    // Adding someone marks the event going when it was not saved, so read the event again for the user's side of it.
    onChange(current => ({ ...current, people: current.people.concat(person) }))
    if (detail.my) return
    try {
      const fresh = await api.events.detail(detail.id)
      onChange(() => fresh)
      onStatus(detail.id, fresh.my?.status ?? null)
    } catch { /* the person is saved; the status catches up on the next open */ }
  }
  const remove = async (person: EventContact) => {
    if (!window.confirm(`Remove ${person.name} from the people you met?`)) return
    setError('')
    try {
      await api.events.deletePerson(person.id)
      onChange(current => ({ ...current, people: current.people.filter(p => p.id !== person.id) }))
    } catch (err) {
      setError(errorText(err, 'Could not remove that person.'))
    }
  }

  return <section className="ci-section" id="people">
    <h3><Users size={13} aria-hidden="true" />People you met · {people.length}</h3>
    {!people.length && <p className="ci-empty-small">Who did you meet? Add them while names are fresh.</p>}
    {people.map(person => <PersonRow key={person.id} person={person} onRemove={() => void remove(person)} />)}
    {error && <p className="form-error" role="alert">{error}</p>}
    <PeopleForm eventId={detail.id} onAdded={added} />
  </section>
}

function PersonRow({ person, onRemove }: { person: EventContact; onRemove: () => void }) {
  const linkedin = externalHref(person.linkedin_url)
  const search = linkedin ? null : externalHref(person.linkedin_search_url)
  const x = externalHref(person.x_url)
  const github = externalHref(person.github_url)
  const line = [person.title, person.company_name, person.email].filter(Boolean).join(' · ')
  return <div className="ev-person">
    <strong>{person.name}</strong>
    {line && <span>{line}</span>}
    <div className="ev-person-links">
      {linkedin && <a href={linkedin} {...EXTERNAL_LINK_PROPS}>LinkedIn ↗</a>}
      {search && <a href={search} title="LinkedIn people search, built from the name — opens in your session" {...EXTERNAL_LINK_PROPS}>Find on LinkedIn ↗</a>}
      {x && <a href={x} {...EXTERNAL_LINK_PROPS}>X ↗</a>}
      {github && <a href={github} {...EXTERNAL_LINK_PROPS}>GitHub ↗</a>}
      {FEATURES.outreach && <Link href={`/outreach?add=event_contact:${person.id}`}>Start a thread</Link>}
      <button type="button" className="ev-person-remove" onClick={onRemove} aria-label={`Remove ${person.name}`}><X size={14} aria-hidden="true" /></button>
    </div>
    {person.note && <p>{person.note}</p>}
  </div>
}

const blank = (value: string) => (value.trim() ? value.trim() : undefined)

function PeopleForm({ eventId, onAdded }: { eventId: string; onAdded: (person: EventContact) => Promise<void> | void }) {
  const [name, setName] = useState('')
  const [company, setCompany] = useState('')
  const [title, setTitle] = useState('')
  const [linkedin, setLinkedin] = useState('')
  const [email, setEmail] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim() || busy) return
    const payload: EventContactIn = {
      name: name.trim(),
      company_name: blank(company),
      title: blank(title),
      linkedin_url: blank(linkedin),
      email: blank(email),
      note: blank(note),
    }
    setBusy(true); setError('')
    try {
      const person = await api.events.addPerson(eventId, payload)
      setName(''); setCompany(''); setTitle(''); setLinkedin(''); setEmail(''); setNote('')
      await onAdded(person)
    } catch (err) {
      setError(errorText(err, 'Could not add that person.'))
    } finally {
      setBusy(false)
    }
  }

  return <form className="ev-form" onSubmit={submit} aria-label="Add someone you met">
    <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Who did you meet?" maxLength={200} required /></label>
    <label>Company<input value={company} onChange={e => setCompany(e.target.value)} placeholder="Optional" maxLength={200} /></label>
    <label>Title<input value={title} onChange={e => setTitle(e.target.value)} placeholder="Optional" maxLength={200} /></label>
    <label>LinkedIn URL<input type="url" value={linkedin} onChange={e => setLinkedin(e.target.value)} placeholder="https://www.linkedin.com/in/…" /></label>
    <label className="full">Email <small>only if they gave it to you</small><input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Optional" /></label>
    <label className="full">Note<textarea rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="What you talked about, what you promised" maxLength={2000} /></label>
    {error && <p className="form-error full" role="alert">{error}</p>}
    <div className="ev-form-actions"><button type="submit" className="primary-button" disabled={busy || !name.trim()}>{busy ? 'Adding…' : 'Add person'}</button></div>
  </form>
}

// ── Add by hand ──────────────────────────────────────────────────────────────
/** The manual form: opened when a pasted link did not resolve. The event it makes is private to the user. */
function ManualForm({ draft, onCancel, onAdded }: {
  draft: ManualDraft
  onCancel: () => void
  onAdded: (event: EventDetail) => void | Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [startsAt, setStartsAt] = useState('')
  const [venue, setVenue] = useState('')
  const [isOnline, setIsOnline] = useState(false)
  const [link, setLink] = useState(draft.url.startsWith('https://') ? draft.url : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const titleRef = useRef<HTMLInputElement>(null)
  useEffect(() => { titleRef.current?.focus({ preventScroll: false }) }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy || !title.trim() || !startsAt) return
    // The field is the user's wall-clock time in IST, whatever zone the browser is in.
    const payload: ManualEventIn = {
      title: title.trim(),
      starts_at: `${startsAt.slice(0, 16)}:00+05:30`,
      venue_name: isOnline ? undefined : blank(venue),
      is_online: isOnline,
      url: blank(link),
    }
    setBusy(true); setError('')
    try {
      const created = await api.events.manual(payload)
      await onAdded(created)
    } catch (err) {
      setError(errorText(err, 'Could not add this event.'))
      setBusy(false)
    }
  }

  const reason = draft.reason ? `${MANUAL_REASON[draft.reason]} ` : ''
  return <section className="ev-manual" aria-label="Add an event by hand">
    <p className="ci-empty-small">{reason}Add it by hand. It stays private to you.</p>
    <form className="ev-form" onSubmit={submit}>
      <label className="full">Title<input ref={titleRef} value={title} onChange={e => setTitle(e.target.value)} maxLength={300} required /></label>
      <label>Starts (IST)<input type="datetime-local" value={startsAt} onChange={e => setStartsAt(e.target.value)} required /></label>
      <label>Venue<input value={venue} onChange={e => setVenue(e.target.value)} disabled={isOnline} maxLength={200} /></label>
      <label className="ev-check full"><input type="checkbox" checked={isOnline} onChange={e => setIsOnline(e.target.checked)} />Online event</label>
      <label className="full">Link<input type="url" value={link} onChange={e => setLink(e.target.value)} placeholder="https://" maxLength={1024} /></label>
      {error && <p className="form-error full" role="alert">{error}</p>}
      <div className="ev-form-actions">
        <button type="button" className="cm-clear" onClick={onCancel}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy || !title.trim() || !startsAt}>{busy ? 'Adding…' : 'Add and mark going'}</button>
      </div>
    </form>
  </section>
}
