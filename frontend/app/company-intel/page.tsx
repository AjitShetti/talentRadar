'use client'

import Link from 'next/link'
import { Fragment, FormEvent, ReactNode, RefObject, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  Briefcase, Building2, Calendar, ChevronDown, ChevronUp, ExternalLink, Github, Globe, Info, Linkedin, List, Loader2,
  Mail, Map as MapIcon, Newspaper, Plus, Search, SlidersHorizontal, Sparkles, Star, Trash2, Users, X,
} from 'lucide-react'
import AppShell from '@/components/AppShell'
import CompanyMap from '@/components/CompanyMap'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import {
  api, CompanyCard, CompanyContact, CompanyDetail, CompanyListFilters, CompanyNewsItem, CompanyPerson,
  ContactCandidate, decodeMapRows, GeoPrecision, isPinned, MapCompany, NewContact, SummarySource,
} from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'
import { useDialog } from '@/lib/use-dialog'

const CITY = 'Bengaluru'
/** Width of the docked drawer, as in `.ci-drawer`. */
const DRAWER_WIDTH = 540
/** At least this wide and the drawer docks beside the map; narrower and it overlays it. */
const DOCK_QUERY = '(min-width: 1000px)'
const NARROW_QUERY = '(max-width: 699px)'
const RAISED_DAYS = 90

// ── Vocabularies ─────────────────────────────────────────────────────────────
// Mirrors domain/company_taxonomy.py and services/companies.py::TIER_LABELS.
// The map rows carry codes only, so the labels live here; the list and the
// drawer use the API's own labels.
const TIERS: Record<string, string> = {
  big_tech: 'Big Tech', unicorn: 'Unicorn', gcc: 'Global Capability Centre', scaleup: 'Scale-up',
  startup: 'Startup', agency: 'Agency & Studio', services: 'IT Services', vc: 'Venture Capital',
}
const STAGES: Record<string, string> = {
  bootstrapped: 'Bootstrapped', pre_seed: 'Pre-seed', seed: 'Seed', series_a: 'Series A', series_b: 'Series B',
  series_c_plus: 'Series C+', public: 'Public', acquired: 'Acquired',
}
const SECTORS: Record<string, string> = {
  ai: 'AI', saas: 'SaaS', fintech: 'Fintech', consumer: 'Consumer', d2c: 'D2C', deeptech: 'Deeptech', edtech: 'Edtech',
  healthtech: 'Healthtech', logistics: 'Logistics & mobility', gaming: 'Gaming', devtools: 'Developer tools',
  it_services: 'IT services', other: 'Other',
}
const SIZES: Record<string, string> = { '1-50': '1–50', '51-200': '51–200', '201-1000': '201–1,000', '1000+': '1,000+' }
const SUMMARY_SOURCE: Record<SummarySource, string> = {
  curated: 'curated', site: 'from their website', wikidata: 'from Wikidata', yc: 'from Y Combinator', github_org: 'from GitHub', llm: 'AI summary',
}
const PEOPLE_KINDS = new Set(['founder', 'leader', 'engineer'])

type SelectKey = 'type' | 'area' | 'stage' | 'sector' | 'size'
const SELECTS: ReadonlyArray<{ key: SelectKey; label: string; all: string; field: 'tier' | 'area' | 'stage' | 'sector' | 'size'; names: Record<string, string> | null }> = [
  { key: 'type', label: 'Type', all: 'All types', field: 'tier', names: TIERS },
  { key: 'area', label: 'Area', all: 'All areas', field: 'area', names: null },
  { key: 'stage', label: 'Stage', all: 'Any stage', field: 'stage', names: STAGES },
  { key: 'sector', label: 'Sector', all: 'All sectors', field: 'sector', names: SECTORS },
  { key: 'size', label: 'Size', all: 'Any size', field: 'size', names: SIZES },
]

type Filters = { q: string; type: string; area: string; stage: string; sector: string; size: string; hiring: boolean; raised: boolean; unpinned: boolean }
type Option = { value: string; label: string; count: number }
type View = 'map' | 'list'

// ── Small helpers ────────────────────────────────────────────────────────────
function initials(name: string) {
  const words = name.replace(/[^\w\s]/g, ' ').split(/\s+/).filter(Boolean)
  return ((words[0]?.[0] || '') + (words[1]?.[0] || '')).toUpperCase() || name.slice(0, 2).toUpperCase()
}

const fmt = (n: number) => n.toLocaleString('en-IN')

function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

function ago(iso: string | null | undefined): string | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const hours = Math.max(0, Math.round((Date.now() - then) / 3_600_000))
  if (hours < 1) return 'just now'
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return days < 60 ? `${days}d ago` : fmtDate(iso)
}

function hostOf(href: string): string {
  try { return new URL(href).hostname.replace(/^www\./, '') } catch { return href }
}

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(false)
  useEffect(() => {
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

/** The name of a company's tier, for a map row (which carries the code only). */
const tierName = (tier: string | null) => (tier ? TIERS[tier] || tier : 'Unclassified')

/** A link to a page outside the app. Anything that is not http(s) renders as plain text. */
function ExtLink({ href, className, label, children }: { href: string | null | undefined; className?: string; label?: string; children: ReactNode }) {
  const safe = externalHref(href)
  if (!safe) return <span className={className}>{children}</span>
  return <a href={safe} className={className} aria-label={label} {...EXTERNAL_LINK_PROPS}>{children}</a>
}

function Logo({ company }: { company: { name: string; logo_url?: string | null } }) {
  const src = externalHref(company.logo_url)
  return <span className="ci-logo">{src
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={src} alt="" />
    : initials(company.name)}</span>
}

function TierBadge({ tier, label }: { tier?: string | null; label?: string | null }) {
  if (!tier) return null
  return <span className="ci-tier" data-tier={tier}>{label || TIERS[tier] || tier.replace('_', ' ')}</span>
}

/** Dot for a precise location, ring for an area, a faint small ring for none on record (the map's own marks). */
function PrecisionGlyph({ precision }: { precision: GeoPrecision | null | undefined }) {
  if (!precision || precision === 'city') {
    return <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="2.8" className="cm-glyph-none" /></svg>
  }
  return precision === 'area'
    ? <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.4" className="cm-glyph-ring" /></svg>
    : <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.6" className="cm-glyph-dot" /></svg>
}

const PRECISION_SHORT: Record<GeoPrecision, string> = { exact: 'Exact', street: 'Street', area: 'Approximate', city: 'Location unknown' }

function precisionNote(precision: GeoPrecision | null | undefined, area: string | null | undefined): string {
  switch (precision) {
    case 'exact': return 'Exact location'
    case 'street': return 'Street-level — geocoded from their address'
    case 'area': return `Approximate — area only. The pin is offset within ${area || 'the area'}`
    default: return 'Location not on record — shown near the city centre'
  }
}

// ── Filtering ────────────────────────────────────────────────────────────────
function rowMatches(row: MapCompany, f: Filters, skip?: SelectKey): boolean {
  if (skip !== 'type' && f.type && row.tier !== f.type) return false
  if (skip !== 'area' && f.area && row.area !== f.area) return false
  if (skip !== 'stage' && f.stage && row.stage !== f.stage) return false
  if (skip !== 'sector' && f.sector && row.sector !== f.sector) return false
  if (skip !== 'size' && f.size && row.size !== f.size) return false
  if (f.hiring && row.openRoles <= 0) return false
  if (f.raised && !row.raised90d) return false
  if (f.unpinned && isPinned(row)) return false
  const q = f.q.trim().toLowerCase()
  if (q) {
    const hay = [row.name, row.area, row.sector && SECTORS[row.sector], row.stage && STAGES[row.stage], tierName(row.tier)]
      .filter(Boolean).join(' ').toLowerCase()
    if (!q.split(/\s+/).every(token => hay.includes(token))) return false
  }
  return true
}

/** The options of one select, counted with every other filter applied. */
function optionsFor(rows: MapCompany[], filters: Filters, spec: (typeof SELECTS)[number]): Option[] {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const value = row[spec.field]
    if (value && rowMatches(row, filters, spec.key)) counts.set(value, (counts.get(value) || 0) + 1)
  }
  const selected = filters[spec.key]
  if (selected && !counts.has(selected)) counts.set(selected, 0)
  const names = spec.names
  if (names) {
    return Object.keys(names).filter(value => counts.has(value)).map(value => ({ value, label: names[value], count: counts.get(value) || 0 }))
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([value, count]) => ({ value, label: value, count }))
}

// ── The page ─────────────────────────────────────────────────────────────────
export default function CompanyIntelPage() {
  const [q, setQ] = usePersistentState('companyIntel.query', '')
  const [type, setType] = usePersistentState('companyIntel.tier', '')
  const [area, setArea] = usePersistentState('companyIntel.area', '')
  const [stage, setStage] = usePersistentState('companyIntel.stage', '')
  const [sector, setSector] = usePersistentState('companyIntel.sector', '')
  const [size, setSize] = usePersistentState('companyIntel.size', '')
  const [hiring, setHiring] = usePersistentState('companyIntel.openRolesOnly', false)
  const [raised, setRaised] = usePersistentState('companyIntel.raised', false)
  const [storedView, setStoredView, viewHydrated] = usePersistentState<View | null>('companyIntel.view', null, 'local')
  const [newsOpenStored, setNewsOpen] = usePersistentState<boolean | null>('companyIntel.newsOpen', null)
  const [unpinned, setUnpinned] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)

  const [rows, setRows] = useState<MapCompany[]>([])
  const [counts, setCounts] = useState({ total: 0, pinned: 0, approximate: 0 })
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [hiringIds, setHiringIds] = useState<ReadonlySet<string>>(() => new Set())
  const [news, setNews] = useState<CompanyNewsItem[]>([])
  const [mapDown, setMapDown] = useState('')
  const [listTotal, setListTotal] = useState<number | null>(null)

  const narrow = useMedia(NARROW_QUERY)
  const canDock = useMedia(DOCK_QUERY)

  // The search box types faster than the filters need to run.
  const [debouncedQuery, setDebouncedQuery] = useState(q)
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(q), 250)
    return () => clearTimeout(timer)
  }, [q])

  // Three reads and nothing else: the map rows, the user's hiring set, the news.
  // The second and third are extras; the page works without either.
  useEffect(() => {
    let cancelled = false
    api.company.map(CITY)
      .then(payload => {
        if (cancelled) return
        setRows(decodeMapRows(payload.rows))
        // `approximate` is absent on an API that predates approximate pins.
        setCounts({ total: payload.total, pinned: payload.pinned, approximate: payload.approximate ?? 0 })
        setLoadState('ready')
      })
      .catch(err => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not load the company map.')
        setLoadState('error')
      })
    api.company.mapForMe().then(r => { if (!cancelled) setHiringIds(new Set(r.hiring_for_you)) }).catch(() => undefined)
    api.company.news(5).then(items => { if (!cancelled) setNews(items) }).catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  const filters = useMemo<Filters>(
    () => ({ q: debouncedQuery, type, area, stage, sector, size, hiring, raised, unpinned }),
    [debouncedQuery, type, area, stage, sector, size, hiring, raised, unpinned],
  )
  const setters: Record<SelectKey, (value: string) => void> = { type: setType, area: setArea, stage: setStage, sector: setSector, size: setSize }

  const rowsById = useMemo(() => new Map(rows.map(r => [r.id, r])), [rows])
  const visible = useMemo(() => rows.filter(r => rowMatches(r, filters)), [rows, filters])
  const options = useMemo(
    () => Object.fromEntries(SELECTS.map(spec => [spec.key, optionsFor(rows, filters, spec)])) as Record<SelectKey, Option[]>,
    [rows, filters],
  )
  // Only rows with no coordinates at all. An approximate row is on the map.
  const visibleUnpinned = useMemo(() => visible.filter(r => r.lat == null).length, [visible])
  const raisedCount = useMemo(() => rows.filter(r => r.raised90d).length, [rows])

  // Which view: the user's choice if they made one, else the map; always the
  // list when the map cannot be drawn. Unlocated employers are one chip away.
  const view: View = mapDown || loadState === 'error' ? 'list' : (storedView ?? 'map')
  const mapActive = view === 'map' && viewHydrated && loadState === 'ready'
  const docked = view === 'map' && canDock && !mapDown
  const newsOpen = newsOpenStored ?? !narrow

  const chooseView = (next: View) => {
    if (next === 'map' && mapDown) return
    setStoredView(next)
    if (next === 'map') setUnpinned(false)
  }
  const seeUnpinned = () => { setUnpinned(true); setStoredView('list') }

  const clearAll = () => {
    setQ(''); setType(''); setArea(''); setStage(''); setSector(''); setSize(''); setHiring(false); setRaised(false); setUnpinned(false)
  }
  const activeCount = (['type', 'area', 'stage', 'sector', 'size'] as const).filter(k => filters[k]).length
    + (hiring ? 1 : 0) + (raised ? 1 : 0) + (unpinned ? 1 : 0)
  const anyFilter = activeCount > 0 || Boolean(filters.q)
  const shown = view === 'list' ? listTotal : visible.length

  const openNews = (item: CompanyNewsItem) => { if (item.company_id) setSelectedId(item.company_id) }
  const closeDrawer = useCallback(() => setSelectedId(null), [])

  const selectedRow = selectedId ? rowsById.get(selectedId) ?? null : null
  const drawerOpen = docked && Boolean(selectedId)

  const ready = loadState === 'ready'
  const stats = (strip: boolean) => <StatsPlate
    strip={strip}
    total={ready ? counts.total : null}
    pinned={ready ? counts.pinned : null}
    raised={ready ? raisedCount : null}
    approximate={ready ? counts.approximate : 0}
  />

  const drawer = selectedId && <CompanyDrawer
    companyId={selectedId}
    fallbackName={selectedRow?.name}
    hiringForYou={hiringIds.has(selectedId)}
    docked={docked}
    closeOnEscape={!sheetOpen}
    onClose={closeDrawer}
  />

  return <RequireAuth><AppShell bleed>
    <div className="cm-root">
      <div className={`cm-page ${view === 'map' ? 'is-map' : 'is-list'}`} data-view={view} data-drawer={drawerOpen ? 'open' : 'closed'}>
        <h1 className="cm-sr">Every employer in {CITY}, on one map.</h1>

        {/* One control panel in both views: it floats over the map, and sits in
            the page flow above the list. It is never remounted, so a keyboard
            user who switches views keeps their place. */}
        <div className={`cm-band ${view === 'list' ? 'cm-inner' : ''}`}>
          <ControlPanel
            view={view}
            narrow={narrow}
            mapDown={Boolean(mapDown)}
            q={q}
            onQuery={setQ}
            filters={filters}
            options={options}
            setters={setters}
            onHiring={setHiring}
            onRaised={setRaised}
            onUnpinned={setUnpinned}
            activeCount={activeCount}
            anyFilter={anyFilter}
            shown={shown}
            onClear={clearAll}
            onView={chooseView}
            sheetOpen={sheetOpen}
            onOpenSheet={() => setSheetOpen(true)}
          />
          {(narrow || view === 'list') && stats(narrow)}
        </div>

        {mapDown && <div className="cm-inner"><p className="cm-fallback" role="status"><Info size={15} /><span>Map unavailable — showing the list.</span><small>{mapDown}</small></p></div>}
        {error && <div className="cm-inner"><p className="form-error cm-error">{error}</p></div>}

        {!mapDown && <section className="cm-stage" hidden={view !== 'map'} data-testid="company-map-stage" data-drawer={drawerOpen ? 'open' : 'closed'}>
          <div className={`cm-stage-inner ${drawerOpen ? 'has-drawer' : ''}`}>
            <CompanyMap
              rows={visible}
              litIds={hiringIds}
              selectedId={selectedId}
              selected={selectedRow}
              active={mapActive}
              rightInset={drawerOpen ? DRAWER_WIDTH : 0}
              onSelect={setSelectedId}
              railTop={!narrow && stats(false)}
              railBottom={visibleUnpinned > 0 && <button type="button" className="ci-chip cm-float cm-unpinned" data-testid="unpinned-chip" onClick={seeUnpinned}>
                <strong>{fmt(visibleUnpinned)}</strong> not pinned — see List
              </button>}
              onUnavailable={setMapDown}
            />
            {news.length > 0 && <NewsStrip items={news} open={newsOpen} onToggle={() => setNewsOpen(!newsOpen)} onOpen={openNews} />}
            {docked && drawer}
          </div>
        </section>}

        {view === 'list' && <div className="cm-inner cm-list">
          <DirectoryList
            filters={filters}
            unpinnedCount={Math.max(0, counts.total - counts.pinned)}
            selectedId={selectedId}
            onPick={setSelectedId}
            onToggleUnpinned={() => setUnpinned(!unpinned)}
            onTotal={setListTotal}
            anyFilter={anyFilter}
            loadingRows={loadState === 'loading'}
          />
        </div>}
      </div>

      {!docked && selectedId && <>
        <button type="button" className="ci-backdrop cm-over" onClick={closeDrawer} aria-label="Close company details" />
        {drawer}
      </>}
      {sheetOpen && narrow && <FilterSheet
        filters={filters}
        options={options}
        count={view === 'list' ? listTotal : visible.length}
        setters={setters}
        onHiring={setHiring}
        onRaised={setRaised}
        onClear={clearAll}
        onClose={() => setSheetOpen(false)}
      />}
    </div>
  </AppShell></RequireAuth>
}

// ── Stats plate ──────────────────────────────────────────────────────────────
/** What the system holds: employers, how many are on the map, how many raised lately. Counts of the whole city, not of the filters. */
function StatsPlate({ strip, total, pinned, raised, approximate }: {
  strip: boolean
  total: number | null
  pinned: number | null
  raised: number | null
  approximate: number
}) {
  const items: Array<[string, number | null]> = [
    ['employers', total],
    ['on the map', pinned],
    [`raised in ${RAISED_DAYS} days`, raised],
  ]
  if (approximate > 0) items.push(['location unknown', approximate])
  return <section className={`cm-stats ${strip ? 'is-strip' : ''}`} aria-label={`${CITY} employers`} data-testid="stats-plate">
    {!strip && <span className="cm-stats-label">Company Intel — {CITY}</span>}
    <p className="cm-stats-line">
      {items.map(([label, value], i) =>
        <Fragment key={label}>
          {i > 0 && <i aria-hidden="true">·</i>}
          <span className="cm-stat"><b>{value === null ? <span className="cm-dash">—</span> : <FlapText value={fmt(value)} />}</b> {label}</span>
        </Fragment>)}
    </p>
  </section>
}

// ── Control panel ────────────────────────────────────────────────────────────
type PanelProps = {
  view: View
  narrow: boolean
  mapDown: boolean
  q: string
  onQuery: (value: string) => void
  filters: Filters
  options: Record<SelectKey, Option[]>
  setters: Record<SelectKey, (value: string) => void>
  onHiring: (value: boolean) => void
  onRaised: (value: boolean) => void
  onUnpinned: (value: boolean) => void
  activeCount: number
  anyFilter: boolean
  shown: number | null
  onClear: () => void
  onView: (view: View) => void
  sheetOpen: boolean
  onOpenSheet: () => void
}

/**
 * Search, the five filters behind one button, the two toggles, and the Map | List
 * switch. Two rows tall however many filters are on: the selects live in a
 * popover (below 700px, in the sheet).
 */
function ControlPanel(p: PanelProps) {
  const { view, narrow, filters, setters } = p
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const popId = useId()
  const popOpen = open && !narrow

  // The popover is a disclosure, not a modal: focus stays on the button and Tab
  // walks straight into it (it follows the button in the DOM). Esc closes it and
  // returns to the button; so does a click anywhere else, unless that click put
  // focus somewhere real. Escape is taken in the capture phase so the same
  // keypress does not also close the company drawer behind it.
  useEffect(() => {
    if (!popOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
      buttonRef.current?.focus()
    }
    const onPointer = (e: PointerEvent) => {
      const target = e.target
      if (!(target instanceof Node)) return
      if (popRef.current?.contains(target) || buttonRef.current?.contains(target)) return
      setOpen(false)
      // A click on another control takes focus there; a click on the map or on
      // bare page (the map canvas is focusable, but it is not a control) hands
      // focus back to the Filters button rather than losing it.
      const element = target instanceof Element ? target : target.parentElement
      const control = element?.closest('input,select,textarea,button,a[href],summary')
      if (!control) setTimeout(() => buttonRef.current?.focus({ preventScroll: true }), 0)
    }
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('pointerdown', onPointer, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('pointerdown', onPointer, true)
    }
  }, [popOpen])

  const placeholder = narrow
    ? 'Search companies'
    : view === 'map' ? 'Search name, area, sector or type' : 'Search name, industry or what they do'
  const showSub = !narrow || p.activeCount > 0
  const toggleFilters = () => { if (narrow) p.onOpenSheet(); else setOpen(o => !o) }

  return <div className="cm-panel" role="search" aria-label="Find companies" data-testid="control-panel">
    <div className="cm-row cm-row-main">
      <div className="cm-search">
        <Search size={16} aria-hidden="true" />
        <input
          type="search"
          value={p.q}
          onChange={e => p.onQuery(e.target.value)}
          placeholder={placeholder}
          aria-label="Search companies"
        />
      </div>
      <div className="cm-filters">
        <button
          ref={buttonRef}
          type="button"
          className={`cm-filter-btn ${p.activeCount > 0 ? 'has-active' : ''}`}
          onClick={toggleFilters}
          aria-haspopup="dialog"
          aria-expanded={narrow ? p.sheetOpen : popOpen}
          aria-controls={popOpen ? popId : undefined}
          aria-label={p.activeCount > 0 ? `Filters, ${p.activeCount} active` : 'Filters'}
        >
          <SlidersHorizontal size={13} aria-hidden="true" />Filters{p.activeCount > 0 && <b aria-hidden="true">{p.activeCount}</b>}
          {!narrow && <ChevronDown size={12} className="cm-chev" aria-hidden="true" />}
        </button>
        {popOpen && <FiltersPopover
          id={popId}
          popRef={popRef}
          filters={filters}
          options={p.options}
          setters={setters}
          shown={p.shown}
          activeCount={p.activeCount}
          onClear={p.onClear}
          onClose={() => { setOpen(false); buttonRef.current?.focus() }}
        />}
      </div>
      <div className="cm-view" role="group" aria-label="View" data-testid="view-switch">
        <button type="button" aria-pressed={view === 'map'} aria-disabled={p.mapDown} title={p.mapDown ? 'Map unavailable' : undefined} onClick={() => p.onView('map')}>
          <MapIcon size={13} aria-hidden="true" /><span className="cm-view-label">Map</span>
        </button>
        <i aria-hidden="true" />
        <button type="button" aria-pressed={view === 'list'} onClick={() => p.onView('list')}>
          <List size={13} aria-hidden="true" /><span className="cm-view-label">List</span>
        </button>
      </div>
    </div>

    {showSub && <div className="cm-row cm-row-sub">
      <button type="button" className={`ci-chip ci-chip-toggle cm-toggle ${filters.hiring ? 'active' : ''}`} aria-pressed={filters.hiring} onClick={() => p.onHiring(!filters.hiring)}>
        <Sparkles size={12} />Hiring now
      </button>
      <button type="button" className={`ci-chip ci-chip-toggle cm-toggle ${filters.raised ? 'active' : ''}`} aria-pressed={filters.raised} onClick={() => p.onRaised(!filters.raised)}>
        Raised ≤ {RAISED_DAYS} days
      </button>
      {SELECTS.filter(spec => filters[spec.key]).map(spec =>
        <button type="button" key={spec.key} className="ci-chip cm-applied" onClick={() => setters[spec.key]('')} aria-label={`Clear ${spec.label}: ${spec.names?.[filters[spec.key]] || filters[spec.key]}`}>
          {spec.names?.[filters[spec.key]] || filters[spec.key]}<X size={11} />
        </button>)}
      {filters.unpinned && <button type="button" className="ci-chip cm-applied" onClick={() => p.onUnpinned(false)} aria-label="Clear Location unknown">Location unknown<X size={11} /></button>}
      {narrow && filters.hiring && <button type="button" className="ci-chip cm-applied" onClick={() => p.onHiring(false)} aria-label="Clear Hiring now">Hiring now<X size={11} /></button>}
      {narrow && filters.raised && <button type="button" className="ci-chip cm-applied" onClick={() => p.onRaised(false)} aria-label="Clear Raised">Raised ≤ {RAISED_DAYS} days<X size={11} /></button>}
      {p.activeCount > 0 && <button type="button" className="cm-clear" onClick={p.onClear}>Clear all</button>}
      {p.anyFilter && <span className="ci-count cm-count" role="status">
        {p.shown === null ? <span className="cm-dash">—</span> : <FlapText value={fmt(p.shown)} />} shown
      </span>}
    </div>}
  </div>
}

function FiltersPopover({ id, popRef, filters, options, setters, shown, activeCount, onClear, onClose }: {
  id: string
  popRef: RefObject<HTMLDivElement>
  filters: Filters
  options: Record<SelectKey, Option[]>
  setters: Record<SelectKey, (value: string) => void>
  shown: number | null
  activeCount: number
  onClear: () => void
  onClose: () => void
}) {
  return <div ref={popRef} id={id} className="cm-pop" role="dialog" aria-label="Filters" data-testid="filters-popover">
    <div className="cm-pop-grid">
      {SELECTS.map(spec =>
        <label key={spec.key} className="cm-field">
          <span>{spec.label}</span>
          <select value={filters[spec.key]} data-set={filters[spec.key] ? 'true' : 'false'} onChange={e => setters[spec.key](e.target.value)}>
            <option value="">{spec.all}</option>
            {options[spec.key].map(o => <option key={o.value} value={o.value}>{o.label} ({o.count})</option>)}
          </select>
        </label>)}
    </div>
    <div className="cm-pop-foot">
      <span className="ci-count">{shown === null ? <span className="cm-dash">—</span> : <FlapText value={fmt(shown)} />} shown</span>
      {activeCount > 0 && <button type="button" className="cm-clear" onClick={onClear}>Clear all</button>}
      <button type="button" className="cm-done" onClick={onClose}>Done</button>
    </div>
  </div>
}

// ── News strip ───────────────────────────────────────────────────────────────
function NewsStrip({ items, open, onToggle, onOpen }: {
  items: CompanyNewsItem[]
  open: boolean
  onToggle: () => void
  onOpen: (item: CompanyNewsItem) => void
}) {
  const meta = (item: CompanyNewsItem) => <span className="cm-meta">
    {item.category === 'funding' && <span className="cm-tag">Funding</span>}
    <span>{[item.source, ago(item.published_at)].filter(Boolean).join(' · ')}</span>
  </span>
  return <div className={`cm-float cm-news ${open ? 'is-open' : ''}`} data-testid="news-strip">
    <button type="button" className="cm-news-head" onClick={onToggle} aria-expanded={open} aria-controls="cm-news-list">
      <Newspaper size={14} /><strong>Latest</strong><em>{items.length}</em><ChevronUp size={14} className="cm-chev" />
    </button>
    <ol id="cm-news-list" hidden={!open}>
      {items.map(item =>
        <li key={item.id}>
          {item.company_id
            ? <button type="button" className="cm-news-item" onClick={() => onOpen(item)}>
                <b>{item.title}</b>{meta(item)}
              </button>
            : <ExtLink href={item.url} className="cm-news-item">
                <b>{item.title}<ExternalLink size={10} className="cm-ext" /></b>{meta(item)}
              </ExtLink>}
        </li>)}
    </ol>
  </div>
}

// ── Filter sheet (below 700px) ───────────────────────────────────────────────
function FilterSheet({ filters, options, count, setters, onHiring, onRaised, onClear, onClose }: {
  filters: Filters
  options: Record<SelectKey, Option[]>
  count: number | null
  setters: Record<SelectKey, (value: string) => void>
  onHiring: (value: boolean) => void
  onRaised: (value: boolean) => void
  onClear: () => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const onKeyDown = useDialog(ref, onClose, { modal: true, closeOnEscape: true })
  return <>
    <button type="button" className="cm-sheet-backdrop" onClick={onClose} aria-label="Close filters" />
    <div ref={ref} className="cm-sheet" role="dialog" aria-modal="true" aria-labelledby="cm-sheet-title" tabIndex={-1} onKeyDown={onKeyDown}>
      <span className="cm-sheet-grip" aria-hidden="true" />
      <div className="cm-sheet-head">
        <h2 id="cm-sheet-title">Filters</h2>
        <button type="button" className="ci-close" onClick={onClose} aria-label="Close filters" data-autofocus><X size={18} /></button>
      </div>
      {SELECTS.map(spec =>
        <label key={spec.key}>
          <span>{spec.label}</span>
          <select value={filters[spec.key]} onChange={e => setters[spec.key](e.target.value)}>
            <option value="">{spec.all}</option>
            {options[spec.key].map(o => <option key={o.value} value={o.value}>{o.label} ({o.count})</option>)}
          </select>
        </label>)}
      <div className="cm-sheet-toggles">
        <button type="button" className={`ci-chip ci-chip-toggle cm-toggle ${filters.hiring ? 'active' : ''}`} aria-pressed={filters.hiring} onClick={() => onHiring(!filters.hiring)}>
          <Sparkles size={12} />Hiring now
        </button>
        <button type="button" className={`ci-chip ci-chip-toggle cm-toggle ${filters.raised ? 'active' : ''}`} aria-pressed={filters.raised} onClick={() => onRaised(!filters.raised)}>
          Raised ≤ {RAISED_DAYS} days
        </button>
      </div>
      <div className="cm-sheet-foot">
        <button type="button" className="cm-clear" onClick={onClear}>Clear all</button>
        <button type="button" className="primary-button" onClick={onClose}>
          {count === null ? 'Show companies' : `Show ${fmt(count)} ${count === 1 ? 'company' : 'companies'}`}
        </button>
      </div>
    </div>
  </>
}

// ── List view ────────────────────────────────────────────────────────────────
function DirectoryList({ filters, unpinnedCount, selectedId, onPick, onToggleUnpinned, onTotal, anyFilter, loadingRows }: {
  filters: Filters
  unpinnedCount: number
  selectedId: string | null
  onPick: (id: string) => void
  onToggleUnpinned: () => void
  onTotal: (total: number | null) => void
  anyFilter: boolean
  loadingRows: boolean
}) {
  const [cards, setCards] = useState<CompanyCard[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')

  // The server runs every one of the list's filters, "not pinned" included
  // (`pinned=false`), so the list pages the same way whatever is switched on.
  const query = useMemo<CompanyListFilters>(() => ({
    city: CITY,
    tier: filters.type || undefined,
    q: filters.q.trim() || undefined,
    stage: filters.stage || undefined,
    sector: filters.sector || undefined,
    area: filters.area || undefined,
    size: filters.size || undefined,
    hasOpenRoles: filters.hiring,
    raisedWithinDays: filters.raised ? RAISED_DAYS : undefined,
    pinned: filters.unpinned ? false : undefined,
  }), [filters.type, filters.q, filters.stage, filters.sector, filters.area, filters.size, filters.hiring, filters.raised, filters.unpinned])
  const onlyUnpinned = filters.unpinned

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.company.directory(query)
      .then(result => { if (!cancelled) { setCards(result.companies); setTotal(result.total); setError('') } })
      .catch(err => { if (!cancelled) { setCards([]); setTotal(0); setError(err instanceof Error ? err.message : 'Could not load the company directory.') } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [query])

  useEffect(() => { onTotal(loading ? null : total) }, [loading, total, onTotal])
  useEffect(() => () => onTotal(null), [onTotal])

  // The directory arrives a page at a time; the rest is fetched on request so
  // the first paint does not wait on every card.
  const loadMore = () => {
    setLoadingMore(true)
    api.company.directory({ ...query, offset: cards.length })
      .then(result => { setCards(current => [...current, ...result.companies.filter(c => !current.some(have => have.id === c.id))]); setTotal(result.total) })
      .catch(err => setError(err instanceof Error ? err.message : 'Could not load more companies.'))
      .finally(() => setLoadingMore(false))
  }

  return <>
    {unpinnedCount > 0 && !loadingRows && <div className="cm-unpinned-note">
      <span className="cm-note-text">
        <PrecisionGlyph precision="city" />
        <span><strong><FlapText value={fmt(unpinnedCount)} /></strong> with no address or area on record yet — the map shows them near the city centre.</span>
      </span>
      <button type="button" className={`ci-chip ci-chip-toggle cm-toggle ${onlyUnpinned ? 'active' : ''}`} aria-pressed={onlyUnpinned} onClick={onToggleUnpinned}>
        {onlyUnpinned ? 'Showing only these' : 'Show only these'}
      </button>
    </div>}

    {error && <p className="form-error cm-error">{error}</p>}

    {loading && cards.length === 0
      ? <div className="ci-grid cm-skeleton" aria-busy="true" aria-label="Loading the directory">
          {Array.from({ length: 8 }, (_, i) =>
            <div key={i} className="cm-skel-row"><span className="cm-skel cm-skel-logo" /><span className="cm-skel cm-skel-name" /><span className="cm-skel cm-skel-desc" /><span className="cm-skel cm-skel-meta" /></div>)}
        </div>
      : cards.length === 0
        ? <div className="empty-state">
            <Building2 size={24} />
            <h2>No companies match those filters.</h2>
            <p>{anyFilter ? `Clear a filter to see more of ${CITY}.` : <>Seed the catalogue with <code>python scripts/seed_companies.py</code> if this is a fresh database.</>}</p>
          </div>
        : <>
            <div className="cm-list-head" aria-hidden="true">
              <span>Company</span><span>What they do</span><span>Area</span><span>Stage · sector</span><span>Type</span><span>Roles</span>
            </div>
            <div className="ci-grid">
              {cards.map(company => <CompanyRow key={company.id} company={company} selected={selectedId === company.id} onPick={onPick} />)}
            </div>
          </>}

    {!loading && !error && cards.length < total &&
      <div className="ci-more">
        <button type="button" className="ci-chip" onClick={loadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : `Show more — ${fmt(total - cards.length)} of ${fmt(total)} still to come`}
        </button>
      </div>}
  </>
}

function CompanyRow({ company, selected, onPick }: { company: CompanyCard; selected: boolean; onPick: (id: string) => void }) {
  const precision: GeoPrecision = company.geo_precision || 'city'
  const stageName = company.stage ? STAGES[company.stage] || company.stage : null
  const sectorName = company.sector ? SECTORS[company.sector] || company.sector : null
  return <button type="button" className={`ci-card ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={() => onPick(company.id)}>
    <div className="ci-card-head">
      <Logo company={company} />
      <div className="ci-card-title">
        <strong>{company.name}</strong>
        <span>{[sectorName || company.industry || company.hq_city, stageName].filter(Boolean).join(' · ') || '—'}</span>
      </div>
      <TierBadge tier={company.tier} label={company.tier_label} />
    </div>
    <p className="ci-card-desc">{company.summary || company.description || 'No summary yet.'}</p>
    <div className="cm-cell-area">
      <span><PrecisionGlyph precision={precision} />{company.area || (precision === 'city' ? CITY : company.hq_city) || CITY}</span>
      <small>{PRECISION_SHORT[precision]}</small>
    </div>
    <div className="cm-cell-codes"><span>{stageName || '—'}</span><span>{sectorName || '—'}</span></div>
    <div className="ci-card-foot">
      <div className="ci-mini-chips">
        {company.tech_stack.slice(0, 2).map(tech => <span key={tech}>{tech}</span>)}
      </div>
      {company.open_roles > 0 && <span className="ci-roles">{company.open_roles} open</span>}
    </div>
  </button>
}

// ── Drawer ───────────────────────────────────────────────────────────────────
function CompanyDrawer({ companyId, fallbackName, hiringForYou, docked, closeOnEscape, onClose }: {
  companyId: string
  fallbackName?: string
  hiringForYou: boolean
  docked: boolean
  closeOnEscape: boolean
  onClose: () => void
}) {
  const [detail, setDetail] = useState<CompanyDetail | null>(null)
  const [error, setError] = useState('')
  const rootRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const onKeyDown = useDialog(rootRef, onClose, { modal: !docked, closeOnEscape })

  useEffect(() => {
    let cancelled = false
    setDetail(null); setError('')
    bodyRef.current?.scrollTo({ top: 0 })
    api.company.detail(companyId)
      .then(result => { if (!cancelled) setDetail(result) })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load this company.') })
    return () => { cancelled = true }
  }, [companyId])

  // An overlay drawer owns the screen, so the page behind it must not scroll.
  useEffect(() => {
    if (docked) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [docked])

  const setContacts = useCallback((contacts: CompanyContact[]) => {
    setDetail(current => (current ? { ...current, contacts } : current))
  }, [])
  const reload = useCallback(() => {
    api.company.detail(companyId)
      .then(result => setDetail(current => (current && current.id === result.id ? result : current)))
      .catch(() => undefined)
  }, [companyId])

  const name = detail?.name || fallbackName || 'Loading…'
  return <aside
    ref={rootRef}
    className={`ci-drawer ${docked ? 'is-docked' : 'cm-over'}`}
    role="dialog"
    aria-modal={!docked}
    aria-labelledby="cm-drawer-title"
    tabIndex={-1}
    data-testid="company-drawer"
    onKeyDown={onKeyDown}
  >
    <header className="ci-drawer-head">
      {detail ? <Logo company={detail} /> : <span className="ci-logo">{fallbackName ? initials(fallbackName) : <Building2 size={18} />}</span>}
      <div className="ci-drawer-title">
        <h2 id="cm-drawer-title">{name}</h2>
        {detail && <DrawerSummary detail={detail} hiringForYou={hiringForYou} />}
      </div>
      <button type="button" className="ci-close" onClick={onClose} aria-label="Close" data-autofocus><X size={18} /></button>
    </header>

    <div className="ci-drawer-body" ref={bodyRef}>
      {error && <p className="form-error" style={{ marginTop: 18 }}>{error}</p>}
      {!detail && !error && <div className="cm-drawer-loading" aria-busy="true"><Loader2 size={16} className="spin" />Loading…</div>}

      {detail && <>
        <Facts detail={detail} />
        <LinkRow detail={detail} />
        <About detail={detail} />
        <RolesSection detail={detail} />
        <PeopleSection detail={detail} onFounders={reload} />
        <NewsSection detail={detail} />
        <TechSection detail={detail} />
        <GithubSection detail={detail} />
        <ContactsSection detail={detail} onContactsChange={setContacts} />
      </>}
    </div>
  </aside>
}

function DrawerSummary({ detail, hiringForYou }: { detail: CompanyDetail; hiringForYou: boolean }) {
  const precision = detail.geo_precision || 'city'
  const place = [detail.area, detail.address].filter(Boolean).join(' · ') || CITY
  return <>
    <div className="cm-codes">
      <TierBadge tier={detail.tier} label={detail.tier_label} />
      {detail.stage && <span className="ci-tier">{STAGES[detail.stage] || detail.stage}</span>}
      {detail.sector && <span className="ci-tier">{SECTORS[detail.sector] || detail.sector}</span>}
      {hiringForYou && <span className="cm-yours"><i aria-hidden="true" />Hiring for your role</span>}
    </div>
    <p className="cm-summary">
      {detail.summary || 'No summary yet.'}
      {detail.summary && detail.summary_source && <span className="cm-prov">{SUMMARY_SOURCE[detail.summary_source] || detail.summary_source}</span>}
    </p>
    <div className="cm-place">
      <PrecisionGlyph precision={precision} />
      <span>{place}</span>
      <small>{precisionNote(precision, detail.area)}</small>
    </div>
  </>
}

function Facts({ detail }: { detail: CompanyDetail }) {
  const headcount = detail.employee_count ? `${fmt(detail.employee_count)}` : detail.employee_count_range
  return <div className="ci-facts">
    <div className="ci-fact"><span>Headquarters</span><strong>{[detail.hq_city, detail.hq_country].filter(Boolean).join(', ') || '—'}</strong></div>
    <div className="ci-fact"><span>Headcount</span><strong>{headcount || '—'}</strong></div>
    <div className="ci-fact"><span>Founded</span><strong>{detail.founded_year || '—'}</strong></div>
    <div className="ci-fact"><span>Last raised</span><strong>{fmtDate(detail.last_funding_at) || '—'}</strong></div>
  </div>
}

function LinkRow({ detail }: { detail: CompanyDetail }) {
  const links: Array<[string, string | null | undefined, JSX.Element | null]> = [
    ['Website', detail.website_url, <Globe size={13} key="w" />],
    ['Careers', detail.careers_url, <ExternalLink size={13} key="c" />],
    ['LinkedIn', detail.linkedin_url, <Linkedin size={13} key="l" />],
    ['GitHub', detail.github?.html_url, <Github size={13} key="g" />],
    ['X', detail.x_url, null],
  ]
  const shown = links.filter(([, href]) => externalHref(href))
  if (!shown.length) return null
  return <div className="ci-links">
    {shown.map(([label, href, icon]) => <ExtLink key={label} className="ci-link" href={href}>{icon}{label}</ExtLink>)}
  </div>
}

function About({ detail }: { detail: CompanyDetail }) {
  // The summary is often the description cut at a word with "…"; showing both repeats it.
  const head = (detail.summary ?? '').replace(/…$/, '').trim()
  const repeats = Boolean(head) && (detail.description ?? '').startsWith(head)
  const description = detail.description && !repeats ? detail.description : null
  if (!description && !detail.culture_summary) return null
  return <div className="cm-about">
    {description && <p>{description}</p>}
    {detail.culture_summary && <p>{detail.culture_summary}</p>}
  </div>
}

function RolesSection({ detail }: { detail: CompanyDetail }) {
  const careers = externalHref(detail.careers_url)
  return <section className="ci-section">
    <h3><Briefcase size={13} />Open roles on TalentRadar</h3>
    {detail.jobs.length === 0
      ? <p className="ci-empty-small">
          No live postings ingested for {detail.name} yet. Their careers page is the fastest route
          {careers ? <> — <ExtLink className="purple-link" href={careers}>open it</ExtLink>.</> : '.'}
        </p>
      : detail.jobs.map(job => {
          const posting = externalHref(job.source_url)
          return <div key={job.id} className="cm-job">
            <Link className="ci-job" href={`/roles/${encodeURIComponent(job.id)}`}>
              <strong>{job.title}</strong>
              <span>
                {[job.location, job.is_remote ? 'Remote' : null, job.seniority, job.salary_raw]
                  .filter(Boolean).join(' · ') || 'Details on the posting'}
              </span>
            </Link>
            {posting && <a className="cm-job-ext" href={posting} aria-label={`Open the ${job.title} posting on the employer's site`} {...EXTERNAL_LINK_PROPS}><ExternalLink size={13} /></a>}
          </div>
        })}
    {detail.jobs.length > 0 && careers &&
      <div className="ci-links"><ExtLink className="ci-link" href={careers}><Calendar size={13} />All roles on their careers page</ExtLink></div>}
  </section>
}

function PersonRow({ person }: { person: CompanyPerson }) {
  const linkedin = externalHref(person.linkedin_url)
  const search = linkedin ? null : externalHref(person.linkedin_search_url)
  const source = externalHref(person.source_url)
  const links: ReactNode[] = []
  if (linkedin) links.push(<ExtLink key="li" href={linkedin}><Linkedin size={12} />LinkedIn</ExtLink>)
  else if (search) links.push(<ExtLink key="ls" href={search}><Linkedin size={12} />Search LinkedIn<ExternalLink size={10} /></ExtLink>)
  if (externalHref(person.x_url)) links.push(<ExtLink key="x" href={person.x_url}>X</ExtLink>)
  if (externalHref(person.github_url)) links.push(<ExtLink key="gh" href={person.github_url}><Github size={12} />GitHub</ExtLink>)
  if (externalHref(person.profile_url)) links.push(<ExtLink key="site" href={person.profile_url}><Globe size={12} />Site</ExtLink>)
  const name = person.name || 'Unnamed'
  return <div className="cm-person">
    <span className="ci-contact-icon">{initials(name)}</span>
    <div>
      <strong>{name}{person.title && <span> · {person.title}</span>}</strong>
      {links.length > 0 && <div className="cm-plinks">{links}</div>}
      <small>
        {source ? <>Source: <ExtLink href={source}>{hostOf(source)}</ExtLink></> : 'Source on file'}
        <span className={`ci-badge ${person.verified ? 'verified' : 'unverified'}`}>{person.verified ? 'verified' : 'unverified'}</span>
      </small>
    </div>
  </div>
}

function PeopleSection({ detail, onFounders }: { detail: CompanyDetail; onFounders: () => void }) {
  const { founders, leaders, engineers } = detail.people
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { setSearching(false); setSearched(false); setMessage('') }, [detail.id])

  const findFounders = async () => {
    setSearching(true); setMessage('')
    try {
      const result = await api.company.discoverFounders(detail.id)
      setMessage(result.message || (result.available ? '' : 'Founder search is not available right now.'))
      setSearched(result.available)
      if (result.available && result.people.length) onFounders()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'The founder search failed.')
    } finally { setSearching(false) }
  }

  const bigger = Boolean(detail.is_bigger)
  const headcount = detail.employee_count ? `${fmt(detail.employee_count)} people` : detail.employee_count_range

  const foundersBlock = <div key="founders">
    <div className="cm-sub"><span className="ci-stack-label">Founders</span>{founders.length > 0 && <em>{founders.length} on record</em>}</div>
    {founders.length > 0
      ? founders.map(p => <PersonRow key={p.id} person={p} />)
      : <div className="cm-empty">
          <p>No founders on record{searched ? ' yet' : ''}.</p>
          <button type="button" className="outline-button" onClick={findFounders} disabled={searching || searched}>
            {searching ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
            {searching ? 'Searching…' : searched ? 'Searched' : 'Find founders'}
          </button>
        </div>}
    {message && <p className="cm-note" role="status">{message}</p>}
  </div>

  const leadersBlock = <div key="leaders">
    <div className="cm-sub"><span className="ci-stack-label">Leadership</span></div>
    {leaders.length > 0
      ? leaders.map(p => <PersonRow key={p.id} person={p} />)
      : <div className="cm-empty"><p>No leaders on record yet.</p></div>}
  </div>

  const githubBlock = <div key="github">
    <div className="cm-sub"><span className="ci-stack-label">On GitHub</span>{engineers.length > 0 && detail.github_org && <em>public members of @{detail.github_org}</em>}</div>
    {engineers.length > 0
      ? engineers.map(p => {
          const handle = (p.github_url || '').replace(/\/+$/, '').split('/').pop() || ''
          return <div key={p.id} className="cm-gh-row">
            <span>{p.name}</span>
            {handle && <ExtLink href={p.github_url}>@{handle}</ExtLink>}
          </div>
        })
      : <div className="cm-empty"><p>{detail.github_org ? 'Not read yet — the weekly GitHub pass fills this.' : 'No public GitHub organisation on file.'}</p></div>}
  </div>

  const links = detail.linkedin_links
  const linkedinBlock = <div key="linkedin">
    <div className="cm-sub"><span className="ci-stack-label">Find on LinkedIn</span><em>opens in your own session</em></div>
    {links.length > 0
      ? <div className="ci-links cm-presets">
          {links.map((link, i) =>
            <ExtLink key={link.label} className="ci-link" href={link.url}>
              <Linkedin size={13} />{link.label}{i >= 3 && <em className="cm-yours-text">your role</em>}
            </ExtLink>)}
        </div>
      : <div className="cm-empty"><p>No LinkedIn searches available for this company.</p></div>}
    <p className="cm-li-note">Nothing here is fetched from LinkedIn. Each person links to the page that named them.</p>
  </div>

  // A bigger company is read by its headcount and leadership, and reached
  // through LinkedIn searches; a startup is read by its founders (spec §4.5).
  return <section className="ci-section" data-testid="drawer-people">
    <h3><Users size={13} />People</h3>
    {bigger
      ? <>
          <div className="cm-sub"><span className="ci-stack-label">Headcount</span><em>{headcount || 'not on record'}</em></div>
          {leadersBlock}{linkedinBlock}{githubBlock}{foundersBlock}
        </>
      : <>{foundersBlock}{leadersBlock}{githubBlock}{linkedinBlock}</>}
  </section>
}

function NewsSection({ detail }: { detail: CompanyDetail }) {
  return <section className="ci-section">
    <h3><Newspaper size={13} />News</h3>
    {detail.news.length === 0
      ? <p className="ci-empty-small">No news on record for {detail.name}.</p>
      : detail.news.map(item => {
          const line = [item.round, item.amount_text, item.investors.length ? item.investors.slice(0, 3).join(', ') : null].filter(Boolean).join(' · ')
          return <ExtLink key={item.id} className="ci-job cm-news-row" href={item.url}>
            <strong>{item.title}</strong>
            <div className="cm-meta">
              {item.category === 'funding' && <span className="cm-tag">Funding</span>}
              <span>{[item.source, fmtDate(item.published_at)].filter(Boolean).join(' · ')}</span>
            </div>
            {line && <div className="cm-round">{line}</div>}
          </ExtLink>
        })}
  </section>
}

function TechSection({ detail }: { detail: CompanyDetail }) {
  const { tech_stack_curated: curated, tech_stack_from_postings: live } = detail
  if (!curated.length && !live.length) return null
  return <section className="ci-section">
    <h3><Sparkles size={13} />Technology they work on</h3>
    {curated.length > 0 && <>
      <p className="ci-stack-label">Known stack</p>
      <div className="ci-stack">{curated.map(tech => <span key={tech}>{tech}</span>)}</div>
    </>}
    {live.length > 0 && <>
      <p className="ci-stack-label">Named in their current openings</p>
      <div className="ci-stack">{live.map(tech => <span key={tech} className="live">{tech}</span>)}</div>
    </>}
  </section>
}

function GithubSection({ detail }: { detail: CompanyDetail }) {
  const gh = detail.github
  return <section className="ci-section">
    <h3><Github size={13} />Open source</h3>
    {!gh
      ? <p className="ci-empty-small">
          {detail.github_org
            ? `No public GitHub organisation resolved for “${detail.github_org}”. It may be private, renamed, or the lookup hit GitHub's rate limit.`
            : 'No public GitHub organisation on file for this company.'}
        </p>
      : <div className="ci-gh">
          <ExtLink className="ci-gh-head" href={gh.html_url}>
            {externalHref(gh.avatar_url) && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={externalHref(gh.avatar_url) || ''} alt="" />
            )}
            <div>
              <strong>{gh.name}</strong>
              <span>@{gh.org} · {gh.public_repos} repos · {gh.followers.toLocaleString()} followers</span>
            </div>
            <ExternalLink size={14} color="var(--faint)" />
          </ExtLink>
          {gh.top_repos.map(repo =>
            <ExtLink key={repo.full_name} className="ci-repo" href={repo.html_url}>
              <span className="ci-repo-top">
                <strong>{repo.name}</strong>
                <span className="ci-repo-meta">
                  {repo.language && <span>{repo.language}</span>}
                  <span><Star size={9} style={{ verticalAlign: -1 }} /> {repo.stars.toLocaleString()}</span>
                </span>
              </span>
              {repo.description && <p>{repo.description}</p>}
            </ExtLink>)}
        </div>}
  </section>
}

function ContactsSection({ detail, onContactsChange }: {
  detail: CompanyDetail
  onContactsChange: (contacts: CompanyContact[]) => void
}) {
  const [candidates, setCandidates] = useState<ContactCandidate[] | null>(null)
  const [lookupMessage, setLookupMessage] = useState('')
  const [searching, setSearching] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')

  useEffect(() => { setCandidates(null); setLookupMessage(''); setShowForm(false); setFormError('') }, [detail.id])

  // Founders, leaders and GitHub members are shown under People; this section
  // keeps to the talent team and the user's own contacts.
  const contacts = detail.contacts.filter(c => !PEOPLE_KINDS.has(c.kind))

  const add = async (payload: NewContact) => {
    setBusy(true); setFormError('')
    try {
      const saved = await api.company.addContact(detail.id, payload)
      onContactsChange([...detail.contacts, saved])
      setShowForm(false)
      setCandidates(current => current?.filter(c => c.email !== payload.email) ?? null)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not save that contact.')
    } finally { setBusy(false) }
  }

  const remove = async (contactId: string) => {
    try {
      await api.company.removeContact(detail.id, contactId)
      onContactsChange(detail.contacts.filter(c => c.id !== contactId))
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Could not remove that contact.')
    }
  }

  const runLookup = async () => {
    setSearching(true); setLookupMessage(''); setFormError('')
    try {
      const result = await api.company.discoverContacts(detail.id)
      setCandidates(result.candidates)
      setLookupMessage(result.message || (result.available ? '' : 'Web lookup is not configured.'))
    } catch (err) {
      setLookupMessage(err instanceof Error ? err.message : 'The lookup failed.')
    } finally { setSearching(false) }
  }

  return <section className="ci-section">
    <h3><Mail size={13} />Talent contacts</h3>

    <p className="ci-note">
      <strong>Nothing here is guessed.</strong> Contacts are either a channel the company
      publishes itself, or something found on a real page — each shows where it came from.
      Named recruiters and their addresses are never invented from a naming pattern, so use
      the lookup or add one you already have.
    </p>

    {contacts.length === 0 && <p className="ci-empty-small">No contacts stored yet for {detail.name}.</p>}

    {contacts.map(contact =>
      <div key={contact.id} className="ci-contact">
        <span className="ci-contact-icon">{contact.email ? <Mail size={14} /> : <Globe size={14} />}</span>
        <div className="ci-contact-body">
          <strong>
            {contact.name || contact.title || contact.kind.replace('_', ' ')}
            {contact.is_curated && <span className="ci-badge curated">published</span>}
            <span className={`ci-badge ${contact.verified ? 'verified' : 'unverified'}`}>{contact.verified ? 'verified' : 'unverified'}</span>
          </strong>
          {contact.email && <a href={`mailto:${contact.email}`}>{contact.email}</a>}
          {contact.linkedin_url && <ExtLink href={contact.linkedin_url}>{contact.linkedin_url}</ExtLink>}
          {!contact.email && !contact.linkedin_url && contact.source_url &&
            <ExtLink href={contact.source_url}>{contact.source_url}</ExtLink>}
          {contact.notes && <small>{contact.notes}</small>}
          {contact.source_url && (contact.email || contact.linkedin_url) &&
            <small>Source: <ExtLink href={contact.source_url}>{contact.source_url}</ExtLink></small>}
        </div>
        {!contact.is_curated &&
          <button type="button" className="ci-close" onClick={() => remove(contact.id)} aria-label="Remove contact"><Trash2 size={14} /></button>}
      </div>)}

    {formError && <p className="form-error" style={{ marginTop: 12 }}>{formError}</p>}

    <div className="ci-contact-actions">
      <button type="button" className="outline-button" onClick={runLookup} disabled={searching}>
        {searching ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
        {searching ? 'Searching the web…' : 'Find contacts on the web'}
      </button>
      <button type="button" className="outline-button" onClick={() => setShowForm(!showForm)}>
        <Plus size={13} />{showForm ? 'Cancel' : 'Add a contact'}
      </button>
    </div>

    {lookupMessage && <p className="ci-empty-small" style={{ marginTop: 12 }}>{lookupMessage}</p>}

    {candidates?.map(candidate =>
      <div key={candidate.email || candidate.source_url} className="ci-candidate" style={{ marginTop: 10 }}>
        <span className="ci-contact-icon"><Mail size={14} /></span>
        <div>
          <a href={`mailto:${candidate.email}`}>{candidate.email}</a>
          <small>
            {candidate.kind === 'careers_inbox' ? 'Shared careers inbox' : 'Address on the company domain'} · found on{' '}
            <ExtLink href={candidate.source_url}>{candidate.source_title || candidate.source_url}</ExtLink>
          </small>
        </div>
        <button type="button" disabled={busy} onClick={() => add({ kind: candidate.kind, email: candidate.email, source_url: candidate.source_url, verified: false })}>Save</button>
      </div>)}

    {showForm && <ContactForm busy={busy} onSubmit={add} />}
  </section>
}

function ContactForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: NewContact) => void }) {
  const [form, setForm] = useState<NewContact>({ kind: 'recruiter', name: '', title: '', email: '', linkedin_url: '', notes: '' })
  const set = (key: keyof NewContact) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value })
  const canSubmit = useMemo(() => Boolean(form.name || form.email || form.linkedin_url), [form])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit({ ...form, verified: true })
  }

  const CONTACT_KINDS = [
    ['recruiter', 'Recruiter'], ['referral', 'Referral'], ['careers_inbox', 'Careers inbox'],
    ['careers_page', 'Careers page'], ['other', 'Other'],
  ] as const

  return <form className="ci-contact-form" onSubmit={submit}>
    <input placeholder="Name (optional)" value={form.name || ''} onChange={set('name')} />
    <input placeholder="Title, e.g. Talent Partner" value={form.title || ''} onChange={set('title')} />
    <input className="ci-span2" type="email" placeholder="Email" value={form.email || ''} onChange={set('email')} />
    <input className="ci-span2" placeholder="LinkedIn URL" value={form.linkedin_url || ''} onChange={set('linkedin_url')} />
    <select value={form.kind} onChange={set('kind')}>
      {CONTACT_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
    <input placeholder="Where did you get this?" value={form.source_url || ''} onChange={set('source_url')} />
    <textarea className="ci-span2" rows={2} placeholder="Notes — how you met, what they cover" value={form.notes || ''} onChange={set('notes')} />
    <div className="ci-form-actions">
      <button className="primary-button" disabled={busy || !canSubmit}>{busy ? 'Saving…' : 'Save contact'}</button>
      <span className="ci-empty-small">Saved only to your account.</span>
    </div>
  </form>
}
