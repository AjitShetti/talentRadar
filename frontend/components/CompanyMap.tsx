'use client'

// The base map's own CSS is imported here and nowhere else, so it ships only
// with the Company Intel route. The JS is loaded by a dynamic import inside an
// effect, for the same reason (~220 KB gzipped).
import 'maplibre-gl/dist/maplibre-gl.css'
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AddLayerObject, GeoJSONSource, Map as MapLibreMap, Marker } from 'maplibre-gl'
import type { GeoPrecision, MapCompany } from '@/lib/api'
import { EXTERNAL_LINK_PROPS } from '@/lib/safe-url'
import { BBOX, STYLE_URL, loadMapLibre, reducedMotion, token, webglAvailable, withAlpha } from '@/lib/maplibre'

const SRC_POINTS = 'cm-companies'
const SRC_LIT = 'cm-lit'
const PIN_LAYERS = ['cm-pin-solid', 'cm-pin-ring']
const LIT_LAYERS = ['cm-lit-solid', 'cm-lit-ring']
const HIT_LAYERS = [...LIT_LAYERS, ...PIN_LAYERS]
/** Above this zoom the source stops clustering, so every company is its own pin. */
const CLUSTER_MAX_ZOOM = 14
const REVEAL_ZOOM = CLUSTER_MAX_ZOOM + 0.6
/** A company known only by its area is shown at neighbourhood scale, never down to a street it is not on. */
const AREA_REVEAL_ZOOM = 14
/** Give the style and the first tiles this long before the map counts as down. */
const LOAD_TIMEOUT_MS = 20000
/** Tile failures with not one tile ever drawn before the map counts as down. */
const TILE_ERRORS_BEFORE_GIVING_UP = 6

type PinProps = { id: string; name: string; area: string | null; prec: GeoPrecision; ring: number; selected: number }
type PinCollection = {
  type: 'FeatureCollection'
  features: Array<{ type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: PinProps }>
}

function toCollection(rows: MapCompany[], selectedId: string | null): PinCollection {
  const features: PinCollection['features'] = []
  for (const row of rows) {
    // Only a position we know is drawn: a `city` row has none (services/company_map/read.py::_position).
    if (row.lat == null || row.lng == null || row.precision === 'city') continue
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [row.lng, row.lat] },
      // Solid at a real address, a ring at an area (1).
      properties: { id: row.id, name: row.name, area: row.area, prec: row.precision, ring: row.precision === 'area' ? 1 : 0, selected: row.id === selectedId ? 1 : 0 },
    })
  }
  return { type: 'FeatureCollection', features }
}

const PRECISION_NOTE: Record<GeoPrecision, string> = {
  exact: 'Exact location',
  street: 'Street-level location',
  area: 'Approximate — area only',
  city: 'Location not on record',
}

function clusterSize(count: number): number {
  return count < 10 ? 30 : count < 50 ? 36 : count < 200 ? 42 : 48
}

type CirclePaint = NonNullable<Extract<AddLayerObject, { type: 'circle' }>['paint']>

function pinLayers(): AddLayerObject[] {
  const ink = token('--ink', '#ECEEF0')
  const ink2 = token('--ink-2', '#C7CBD1')
  const paper = token('--paper', '#131211')
  const accent = token('--accent', '#2C75FF')
  const accentSoft = token('--accent-soft', 'rgba(44,117,255,.18)')
  const accentBorder = token('--accent-border', 'rgba(44,117,255,.45)')
  const hole = withAlpha(paper, 0.72)
  const radius = (base: number): CirclePaint['circle-radius'] =>
    ['interpolate', ['linear'], ['zoom'], 9, base - 1.4, 13, base, 17, base + 2]
  const hover = (idle: string, over: string): CirclePaint['circle-color'] =>
    ['case', ['boolean', ['feature-state', 'hover'], false], over, idle]

  return [
    // Steel dot: exact or street precision.
    {
      id: 'cm-pin-solid', type: 'circle', source: SRC_POINTS,
      filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'ring'], 0]],
      paint: {
        'circle-radius': radius(5),
        'circle-color': hover(ink2, ink),
        'circle-stroke-color': paper,
        'circle-stroke-width': 1.5,
      },
    },
    // Hollow ring: area precision only, never drawn as if it were an address.
    {
      id: 'cm-pin-ring', type: 'circle', source: SRC_POINTS,
      filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'ring'], 1]],
      paint: {
        'circle-radius': radius(5.6),
        'circle-color': hole,
        'circle-stroke-color': hover(ink2, ink),
        'circle-stroke-width': 1.7,
      },
    },
    // The lamp. Lit only for the selected company and companies hiring for the
    // user's target role, at whatever precision; these never cluster, so the
    // lamp is never swallowed.
    {
      id: 'cm-lit-halo', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'selected'], 1],
      paint: { 'circle-radius': radius(13), 'circle-color': accentSoft, 'circle-stroke-color': accentBorder, 'circle-stroke-width': 1 },
    },
    {
      id: 'cm-lit-solid', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'ring'], 0],
      paint: { 'circle-radius': radius(6.2), 'circle-color': accent, 'circle-stroke-color': paper, 'circle-stroke-width': 1.5 },
    },
    {
      id: 'cm-lit-ring', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'ring'], 1],
      paint: { 'circle-radius': radius(6.6), 'circle-color': hole, 'circle-stroke-color': accent, 'circle-stroke-width': 2.2 },
    },
  ]
}

export type CompanyMapProps = {
  /** The rows the filters leave. Rows without a position are ignored here. */
  rows: MapCompany[]
  /** Companies hiring for the user's target role. Lit in the accent. */
  litIds: ReadonlySet<string>
  selectedId: string | null
  /** The selected company, looked up in the unfiltered rows so a filter never unlights it. */
  selected: MapCompany | null
  /** The map is only created the first time it is shown, and resized each time after. */
  active: boolean
  /** Width in px that something over the map covers on the right, so a revealed pin is not hidden behind it. */
  rightInset: number
  onSelect: (id: string) => void
  /** Sits on the right-hand rail above the zoom controls (the stats plate). */
  railTop?: ReactNode
  /** Sits on the right-hand rail under the zoom controls. */
  railBottom?: ReactNode
  /** WebGL missing, the style failed, or the tiles are down. Fires at most once. */
  onUnavailable: (reason: string) => void
}

export default function CompanyMap({ rows, litIds, selectedId, selected, active, rightInset, onSelect, railTop, railBottom, onUnavailable }: CompanyMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const insetRef = useRef(rightInset)
  const clickedRef = useRef<string | null>(null)
  const onSelectRef = useRef(onSelect)
  const onUnavailableRef = useRef(onUnavailable)
  // The cluster buttons, owned by the component so a data change can drop them all
  // (see `syncClusters`). Keyed by `clusterKey`, not by the bare cluster id.
  const markersRef = useRef(new Map<string, Marker>())
  const [started, setStarted] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => { onSelectRef.current = onSelect; onUnavailableRef.current = onUnavailable })
  useEffect(() => { insetRef.current = rightInset }, [rightInset])
  useEffect(() => { if (active) setStarted(true) }, [active])

  // Clusters count only what is neither lit nor selected: both are always their own
  // pin (litData), so leaving the selected company in here would count it twice.
  const plainRows = useMemo(() => rows.filter(r => !litIds.has(r.id) && r.id !== selectedId), [rows, litIds, selectedId])
  const pointsData = useMemo(() => toCollection(plainRows, null), [plainRows])
  const litData = useMemo(() => {
    const lit = rows.filter(r => litIds.has(r.id))
    if (selected && !litIds.has(selected.id)) lit.push(selected)
    return toCollection(lit, selectedId)
  }, [rows, litIds, selected, selectedId])
  const latest = useRef({ pointsData, litData })
  latest.current = { pointsData, litData }

  // Create the map: once, the first time it is shown, and never again.
  useEffect(() => {
    if (!started) return
    const host = containerRef.current
    if (!host) return
    let cancelled = false
    let failed = false
    let loaded = false
    let tilesDrawn = 0
    let tileErrors = 0
    let hoverKey: { source: string; id: string } | null = null
    const markers = markersRef.current
    let map: MapLibreMap | null = null
    let timer: ReturnType<typeof setTimeout> | undefined

    const fail = (reason: string) => {
      if (failed || cancelled) return
      failed = true
      onUnavailableRef.current(reason)
    }

    if (!webglAvailable()) {
      fail('This browser cannot draw the map (WebGL is off).')
      return
    }

    void loadMapLibre().then(ml => {
      if (cancelled) return
      try {
        map = new ml.Map({
          container: host,
          style: STYLE_URL,
          bounds: [[BBOX.west, BBOX.south], [BBOX.east, BBOX.north]],
          fitBoundsOptions: { padding: 28 },
          attributionControl: false,
          dragRotate: false,
          pitchWithRotate: false,
          minZoom: 8,
          maxZoom: 18,
        })
      } catch {
        fail('The map could not start (WebGL is unavailable).')
        return
      }
      const m = map
      m.touchZoomRotate.disableRotation()
      m.getCanvas().setAttribute('aria-label', 'Interactive map. Drag to pan, scroll to zoom; the List view lists every company.')
      mapRef.current = m
      timer = setTimeout(() => { if (!loaded) fail('The map is taking too long to load.') }, LOAD_TIMEOUT_MS)

      m.on('error', e => {
        // A tile that will not load is not the map being down until none ever has.
        if ('tile' in e) {
          tileErrors += 1
          if (tilesDrawn === 0 && tileErrors >= TILE_ERRORS_BEFORE_GIVING_UP) fail('The map tiles could not be loaded.')
          return
        }
        if (!loaded) {
          const detail = e.error instanceof Error ? e.error.message : ''
          fail(`The map style could not be loaded${detail ? `: ${detail.slice(0, 120)}` : '.'}`)
        }
      })
      m.on('sourcedata', e => {
        if ('tile' in e && e.sourceId && e.sourceId !== SRC_POINTS && e.sourceId !== SRC_LIT) tilesDrawn += 1
      })

      // ── Clusters are DOM buttons: the page's own type, a pointer cursor, and
      // keyboard focus, none of which a WebGL label gives for free.
      const zoomInto = (sourceId: string, clusterId: number, center: [number, number]) => {
        const source = m.getSource(sourceId) as GeoJSONSource | undefined
        if (!source) return
        // A cluster that was re-clustered or removed since the click rejects; that is not an error.
        void source.getClusterExpansionZoom(clusterId).then(zoom => {
          m.easeTo({ center, zoom: Math.min(zoom + 0.2, 18), duration: reducedMotion() ? 0 : 450 })
        }).catch(() => undefined)
      }
      const syncClusters = () => {
        const seen = new Set<string>()
        const source = SRC_POINTS
        if (m.getSource(source)) {
          for (const feature of m.querySourceFeatures(source, { filter: ['has', 'point_count'] })) {
            const props = feature.properties as { cluster_id?: number; point_count?: number }
            const clusterId = props.cluster_id
            const count = props.point_count
            if (clusterId === undefined || count === undefined || feature.geometry.type !== 'Point') continue
            const [lng, lat] = feature.geometry.coordinates
            // MapLibre's cluster ids are (index << 5) + (zoom + 1) + points.length, so two
            // different filter states with the same number of points hand out the same id
            // for different clusters. An id alone would keep the old button, at its old
            // position with its old count. The count and the position make the key
            // describe the cluster rather than name a slot.
            const key = `${clusterId}:${count}:${lng.toFixed(5)},${lat.toFixed(5)}`
            if (seen.has(key)) continue
            seen.add(key)
            if (markers.has(key)) continue
            const el = document.createElement('button')
            el.type = 'button'
            el.className = 'cm-cluster'
            el.style.setProperty('--d', `${clusterSize(count)}px`)
            el.setAttribute('aria-label', `${count} companies close together. Zoom in.`)
            const label = document.createElement('span')
            label.textContent = String(count)
            el.append(label)
            el.addEventListener('click', ev => { ev.stopPropagation(); zoomInto(source, clusterId, [lng, lat]) })
            markers.set(key, new ml.Marker({ element: el, anchor: 'center' }).setLngLat([lng, lat]).addTo(m))
          }
        }
        markers.forEach((marker, id) => { if (!seen.has(id)) { marker.remove(); markers.delete(id) } })
      }

      // ── Pins: hover cursor, tooltip, click to select.
      const pick = (point: { x: number; y: number }) => {
        const layers = HIT_LAYERS.filter(id => m.getLayer(id))
        if (!layers.length) return undefined
        return m.queryRenderedFeatures([[point.x - 6, point.y - 6], [point.x + 6, point.y + 6]], { layers })[0]
      }
      const clearHover = () => {
        if (hoverKey) m.setFeatureState({ source: hoverKey.source, id: hoverKey.id }, { hover: false })
        hoverKey = null
        m.getCanvas().style.cursor = ''
        if (tipRef.current) tipRef.current.hidden = true
      }
      m.on('mousemove', e => {
        const feature = pick(e.point)
        if (!feature) { clearHover(); return }
        const props = feature.properties as PinProps
        if (!hoverKey || hoverKey.id !== props.id || hoverKey.source !== feature.source) {
          if (hoverKey) m.setFeatureState({ source: hoverKey.source, id: hoverKey.id }, { hover: false })
          hoverKey = { source: feature.source, id: props.id }
          m.setFeatureState({ source: feature.source, id: props.id }, { hover: true })
        }
        m.getCanvas().style.cursor = 'pointer'
        const tip = tipRef.current
        if (tip) {
          const [name, note] = Array.from(tip.querySelectorAll('strong, small'))
          if (name) name.textContent = props.name
          if (note) note.textContent = [PRECISION_NOTE[props.prec], props.area].filter(Boolean).join(' · ')
          tip.style.left = `${e.point.x}px`
          tip.style.top = `${e.point.y}px`
          tip.hidden = false
        }
      })
      m.getCanvas().addEventListener('mouseleave', clearHover)
      m.on('movestart', clearHover)
      m.on('click', e => {
        const feature = pick(e.point)
        if (!feature) return
        const id = (feature.properties as PinProps).id
        clickedRef.current = id
        onSelectRef.current(id)
      })

      m.on('load', () => {
        if (cancelled) return
        loaded = true
        if (timer) clearTimeout(timer)
        m.addSource(SRC_POINTS, { type: 'geojson', data: latest.current.pointsData, cluster: true, clusterRadius: 46, clusterMaxZoom: CLUSTER_MAX_ZOOM, promoteId: 'id' })
        m.addSource(SRC_LIT, { type: 'geojson', data: latest.current.litData, promoteId: 'id' })
        pinLayers().forEach(layer => m.addLayer(layer))
        m.on('sourcedata', e => { if (e.sourceId === SRC_POINTS && e.isSourceLoaded) syncClusters() })
        m.on('moveend', syncClusters)
        m.on('idle', syncClusters)
        setReady(true)
      })
    }).catch(() => fail('The map library could not be loaded.'))

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      markers.forEach(marker => marker.remove())
      markers.clear()
      if (map) map.remove()
      mapRef.current = null
      setReady(false)
    }
  }, [started])

  // Drops every cluster button when the data changes.
  const dropClusters = useCallback(() => {
    markersRef.current.forEach(marker => marker.remove())
    markersRef.current.clear()
  }, [])

  // Filters and selection change the data, never the map. New data means new
  // clusters: drop the buttons now; the source's own `sourcedata` event
  // rebuilds them from the new tiles.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready) return
    dropClusters()
    ;(m.getSource(SRC_POINTS) as GeoJSONSource | undefined)?.setData(pointsData)
  }, [pointsData, ready, dropClusters])
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready) return
    ;(m.getSource(SRC_LIT) as GeoJSONSource | undefined)?.setData(litData)
  }, [litData, ready])

  // Shown again after the List view: the container had no size in between.
  useEffect(() => {
    if (active && ready) mapRef.current?.resize()
  }, [active, ready])

  // A newly selected company: pan to it if it is out of sight (behind the
  // drawer counts), and zoom in if the selection did not come from a pin click,
  // so it is not left inside a cluster.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready || !active || !selected || selected.lat == null || selected.lng == null || selected.precision === 'city') return
    const fromPin = clickedRef.current === selected.id
    clickedRef.current = null
    const inset = insetRef.current
    const point = m.project([selected.lng, selected.lat])
    const canvas = m.getCanvas()
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    const visible = point.x > 48 && point.x < w - inset - 48 && point.y > 64 && point.y < h - 64
    const zoom = m.getZoom()
    if (fromPin && visible) return
    m.easeTo({
      center: [selected.lng, selected.lat],
      zoom: fromPin ? zoom : selected.precision === 'area' ? Math.max(zoom, AREA_REVEAL_ZOOM) : Math.max(zoom, REVEAL_ZOOM),
      offset: [-inset / 2, 0],
      duration: reducedMotion() ? 0 : 600,
    })
  }, [selected, ready, active])

  const fit = () => mapRef.current?.fitBounds([[BBOX.west, BBOX.south], [BBOX.east, BBOX.north]], { padding: 28, duration: reducedMotion() ? 0 : 500 })

  return <div className="cm-map-root">
    <div
      ref={containerRef}
      className="cm-map"
      data-testid="company-map"
      role="region"
      aria-label="Map of Bengaluru employers with a known location. The List view lists every employer."
    />
    {!ready && <div className="cm-plate" role="status">Loading map…</div>}
    <div ref={tipRef} className="cm-tip" hidden aria-hidden="true"><strong /><small /></div>

    <div className="cm-rail">
      {railTop}
      <div className="cm-float cm-zoom" role="group" aria-label="Map zoom">
        <button type="button" onClick={() => mapRef.current?.zoomIn({ duration: reducedMotion() ? 0 : 250 })} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => mapRef.current?.zoomOut({ duration: reducedMotion() ? 0 : 250 })} aria-label="Zoom out">−</button>
        <button type="button" onClick={fit} aria-label="Fit Bengaluru">Fit</button>
      </div>
      {railBottom}
    </div>

    <div className="cm-legend" aria-hidden="true">
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4" className="cm-key-dot" /></svg>Exact or street address</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4" className="cm-key-ring" /></svg>Area only — approximate</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" className="cm-key-lit" /></svg>{litIds.size > 0 ? 'Hiring for your role · selected' : 'Selected'}</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5.4" className="cm-key-cluster" /></svg>Cluster — click to zoom</span>
    </div>

    <div className="cm-attrib">
      © <a href="https://www.openstreetmap.org/copyright" {...EXTERNAL_LINK_PROPS}>OpenStreetMap</a> contributors
      {' · '}<a href="https://openfreemap.org" {...EXTERNAL_LINK_PROPS}>OpenFreeMap</a>
    </div>
  </div>
}
