'use client'

// The base map's own CSS is imported here and nowhere else, so it ships only with
// the routes that show a map. The JS is loaded by a dynamic import inside an
// effect, for the same reason (~220 KB gzipped). The setup (bounds, style, worker,
// colour tokens, capability checks) is lib/maplibre.ts, shared with CompanyMap.
import 'maplibre-gl/dist/maplibre-gl.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AddLayerObject, GeoJSONSource, Map as MapLibreMap, Marker } from 'maplibre-gl'
import { Loader2, Maximize, Minus, Plus } from 'lucide-react'
import type { EventCard } from '@/lib/api'
import { buildPins, clusterSize, splitSources, type PinProps } from '@/lib/event-map'
import { BBOX, STYLE_URL, loadMapLibre, reducedMotion, token, webglAvailable, withAlpha } from '@/lib/maplibre'
import { EXTERNAL_LINK_PROPS } from '@/lib/safe-url'

/** Events that are neither selected nor going. They cluster. */
const SRC_PLAIN = 'ev-plain'
/** The selected event and the events the user is going to: the only amber. They never cluster. */
const SRC_LIT = 'ev-lit'
const PIN_LAYERS = ['ev-pin-solid', 'ev-pin-ring']
const LIT_LAYERS = ['ev-lit-solid', 'ev-lit-ring']
const HIT_LAYERS = [...LIT_LAYERS, ...PIN_LAYERS]
/** Up to this zoom events near each other are one cluster; above it every event is its own pin (stacks are fanned out, see lib/event-map.ts). */
const CLUSTER_MAX_ZOOM = 15
/** Give the style and the first tiles this long before the map counts as down. */
const LOAD_TIMEOUT_MS = 20000
/** Tile failures with not one tile ever drawn before the map counts as down. */
const TILE_ERRORS_BEFORE_GIVING_UP = 6

type CirclePaint = NonNullable<Extract<AddLayerObject, { type: 'circle' }>['paint']>

function pinLayers(): AddLayerObject[] {
  const ink = token('--ink', '#ECEEF0')
  const ink2 = token('--ink-2', '#C7CBD1')
  const paper = token('--paper', '#131211')
  const accent = token('--accent', '#FFB020')
  const accentSoft = token('--accent-soft', 'rgba(255,176,32,.14)')
  const accentBorder = token('--accent-border', 'rgba(255,176,32,.34)')
  const hole = withAlpha(paper, 0.72)
  const radius = (base: number): CirclePaint['circle-radius'] =>
    ['interpolate', ['linear'], ['zoom'], 9, base - 1.4, 13, base, 17, base + 2]
  const hover = (idle: string, over: string): CirclePaint['circle-color'] =>
    ['case', ['boolean', ['feature-state', 'hover'], false], over, idle]
  // A cancelled event stays on the map, dim, so it is never mistaken for one that is happening.
  const fade: CirclePaint['circle-opacity'] = ['case', ['==', ['get', 'dim'], 1], 0.4, 1]

  return [
    // Steel dot: the venue's own coordinates.
    {
      id: 'ev-pin-solid', type: 'circle', source: SRC_PLAIN,
      filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'ring'], 0]],
      paint: {
        'circle-radius': radius(5),
        'circle-color': hover(ink2, ink),
        'circle-stroke-color': paper,
        'circle-stroke-width': 1.5,
        'circle-opacity': fade,
        'circle-stroke-opacity': fade,
      },
    },
    // Hollow ring: the area only. Never drawn as if it were an address.
    {
      id: 'ev-pin-ring', type: 'circle', source: SRC_PLAIN,
      filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'ring'], 1]],
      paint: {
        'circle-radius': radius(5.6),
        'circle-color': hole,
        'circle-stroke-color': hover(ink2, ink),
        'circle-stroke-width': 1.7,
        'circle-opacity': fade,
        'circle-stroke-opacity': fade,
      },
    },
    // The lamp: the selected event, and the events the user is going to.
    {
      id: 'ev-lit-halo', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'selected'], 1],
      paint: { 'circle-radius': radius(13), 'circle-color': accentSoft, 'circle-stroke-color': accentBorder, 'circle-stroke-width': 1 },
    },
    {
      id: 'ev-lit-solid', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'ring'], 0],
      paint: { 'circle-radius': radius(6.2), 'circle-color': accent, 'circle-stroke-color': paper, 'circle-stroke-width': 1.5, 'circle-opacity': fade, 'circle-stroke-opacity': fade },
    },
    {
      id: 'ev-lit-ring', type: 'circle', source: SRC_LIT,
      filter: ['==', ['get', 'ring'], 1],
      paint: { 'circle-radius': radius(6.6), 'circle-color': hole, 'circle-stroke-color': accent, 'circle-stroke-width': 2.2, 'circle-opacity': fade, 'circle-stroke-opacity': fade },
    },
  ]
}

export type EventMapProps = {
  /** The page's events. Online ones and ones with no place are counted, not drawn. */
  events: EventCard[]
  selectedId: string | null
  /** Events the user is going to. Lit amber, with the selected event. Keep the Set stable between renders. */
  goingIds: Set<string>
  onSelect: (id: string) => void
  /** WebGL missing, the style failed, or the tiles are down. Fires at most once. */
  onUnavailable: (reason: string) => void
  /** Makes the "n online — see List" chip a button that goes to the list. Without it the chip is plain text. */
  onShowList?: () => void
}

export default function EventMap({ events, selectedId, goingIds, onSelect, onUnavailable, onShowList }: EventMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const clickedRef = useRef<string | null>(null)
  const onSelectRef = useRef(onSelect)
  const onUnavailableRef = useRef(onUnavailable)
  // The cluster buttons, owned by the component so a data change can drop them all.
  const markersRef = useRef(new Map<string, Marker>())
  const [ready, setReady] = useState(false)

  useEffect(() => { onSelectRef.current = onSelect; onUnavailableRef.current = onUnavailable })

  const model = useMemo(() => buildPins(events, BBOX), [events])
  const { plain, lit } = useMemo(() => splitSources(model.pins, goingIds, selectedId), [model.pins, goingIds, selectedId])
  const latest = useRef({ plain, lit })
  latest.current = { plain, lit }

  // Create the map once, when the component mounts, and remove it when it unmounts.
  useEffect(() => {
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
      m.getCanvas().setAttribute('aria-label', 'Interactive map. Drag to pan, scroll to zoom; the List view lists every event.')
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
        if ('tile' in e && e.sourceId && e.sourceId !== SRC_PLAIN && e.sourceId !== SRC_LIT) tilesDrawn += 1
      })

      // Clusters are DOM buttons: real Fragment Mono, a pointer cursor and keyboard focus,
      // none of which a WebGL label gives for free.
      const zoomInto = (clusterId: number, center: [number, number]) => {
        const source = m.getSource(SRC_PLAIN) as GeoJSONSource | undefined
        if (!source) return
        // A cluster that was re-clustered or removed since the click rejects; that is not an error.
        void source.getClusterExpansionZoom(clusterId).then(zoom => {
          m.easeTo({ center, zoom: Math.min(zoom + 0.2, 18), duration: reducedMotion() ? 0 : 450 })
        }).catch(() => undefined)
      }
      const syncClusters = () => {
        if (!m.getSource(SRC_PLAIN)) return
        const seen = new Set<string>()
        for (const feature of m.querySourceFeatures(SRC_PLAIN, { filter: ['has', 'point_count'] })) {
          const props = feature.properties as { cluster_id?: number; point_count?: number }
          const clusterId = props.cluster_id
          const count = props.point_count
          if (clusterId === undefined || count === undefined || feature.geometry.type !== 'Point') continue
          const [lng, lat] = feature.geometry.coordinates
          // Cluster ids are reused by different clusters when the data changes, so the count
          // and the position make the key describe the cluster rather than name a slot.
          const key = `${clusterId}:${count}:${lng.toFixed(5)},${lat.toFixed(5)}`
          if (seen.has(key)) continue
          seen.add(key)
          if (markers.has(key)) continue
          const el = document.createElement('button')
          el.type = 'button'
          el.className = 'cm-cluster'
          el.style.setProperty('--d', `${clusterSize(count)}px`)
          el.setAttribute('aria-label', `${count} events close together. Zoom in.`)
          const label = document.createElement('span')
          label.textContent = String(count)
          el.append(label)
          el.addEventListener('click', ev => { ev.stopPropagation(); zoomInto(clusterId, [lng, lat]) })
          markers.set(key, new ml.Marker({ element: el, anchor: 'center' }).setLngLat([lng, lat]).addTo(m))
        }
        markers.forEach((marker, key) => { if (!seen.has(key)) { marker.remove(); markers.delete(key) } })
      }

      // Pins: hover cursor, tooltip, click to select.
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
          if (name) name.textContent = props.title
          if (note) note.textContent = props.note
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
        m.addSource(SRC_PLAIN, { type: 'geojson', data: latest.current.plain, cluster: true, clusterRadius: 40, clusterMaxZoom: CLUSTER_MAX_ZOOM, promoteId: 'id' })
        m.addSource(SRC_LIT, { type: 'geojson', data: latest.current.lit, promoteId: 'id' })
        pinLayers().forEach(layer => m.addLayer(layer))
        m.on('sourcedata', e => { if (e.sourceId === SRC_PLAIN && e.isSourceLoaded) syncClusters() })
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
  }, [])

  // Drops every cluster button when the data changes; the source's own `sourcedata`
  // event rebuilds them from the new tiles.
  const dropClusters = useCallback(() => {
    markersRef.current.forEach(marker => marker.remove())
    markersRef.current.clear()
  }, [])

  // Filters, saves and selection change the data, never the map.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready) return
    dropClusters()
    ;(m.getSource(SRC_PLAIN) as GeoJSONSource | undefined)?.setData(plain)
  }, [plain, ready, dropClusters])
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready) return
    ;(m.getSource(SRC_LIT) as GeoJSONSource | undefined)?.setData(lit)
  }, [lit, ready])

  // An event selected from outside the map (a deep link, the list): bring it into view if it is out of sight.
  // A pin the user clicked is already where they are looking.
  const selectedPin = useMemo(() => model.pins.find(p => p.id === selectedId) ?? null, [model.pins, selectedId])
  useEffect(() => {
    const m = mapRef.current
    if (!m || !ready || !selectedPin) return
    const fromPin = clickedRef.current === selectedPin.id
    clickedRef.current = null
    if (fromPin) return
    const point = m.project([selectedPin.lng, selectedPin.lat])
    const canvas = m.getCanvas()
    const visible = point.x > 48 && point.x < canvas.clientWidth - 48 && point.y > 64 && point.y < canvas.clientHeight - 64
    if (!visible) m.easeTo({ center: [selectedPin.lng, selectedPin.lat], duration: reducedMotion() ? 0 : 600 })
  }, [selectedPin, ready])

  const fit = () => mapRef.current?.fitBounds([[BBOX.west, BBOX.south], [BBOX.east, BBOX.north]], { padding: 28, duration: reducedMotion() ? 0 : 500 })

  const chip = (count: number, text: string) => onShowList
    ? <button type="button" className="ci-chip cm-float cm-unpinned" data-testid="ev-map-chip" onClick={onShowList}><strong>{count}</strong> {text}</button>
    : <span className="ci-chip cm-float cm-unpinned" data-testid="ev-map-chip"><strong>{count}</strong> {text}</span>

  return <div className="cm-map-root">
    <div
      ref={containerRef}
      className="cm-map"
      data-testid="event-map"
      role="region"
      aria-label="Map of Bengaluru events. Pins are events; the List view lists them all."
    />
    {!ready && <div className="cm-plate" role="status"><Loader2 size={16} className="spin" />Loading map…</div>}
    {ready && model.pins.length === 0 && <div className="cm-plate" role="status">No events with a location here</div>}
    <div ref={tipRef} className="cm-tip" hidden aria-hidden="true"><strong /><small /></div>

    <div className="cm-rail">
      <div className="cm-float cm-zoom" role="group" aria-label="Map zoom">
        <button type="button" onClick={() => mapRef.current?.zoomIn({ duration: reducedMotion() ? 0 : 250 })} aria-label="Zoom in"><Plus size={15} /></button>
        <button type="button" onClick={() => mapRef.current?.zoomOut({ duration: reducedMotion() ? 0 : 250 })} aria-label="Zoom out"><Minus size={15} /></button>
        <button type="button" onClick={fit} aria-label="Fit Bengaluru"><Maximize size={14} /></button>
      </div>
      {model.online > 0 && chip(model.online, 'online — see List')}
      {model.unplaced > 0 && chip(model.unplaced, 'without a location — see List')}
    </div>

    <div className="cm-legend" aria-hidden="true">
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4" className="cm-key-dot" /></svg>Exact venue</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4" className="cm-key-ring" /></svg>Area only — approximate</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" className="cm-key-lit" /></svg>You&apos;re going · selected</span>
      <span><svg width="12" height="12"><circle cx="6" cy="6" r="5.4" className="cm-key-cluster" /></svg>Cluster — click to zoom</span>
    </div>

    <div className="cm-attrib">
      © <a href="https://www.openstreetmap.org/copyright" {...EXTERNAL_LINK_PROPS}>OpenStreetMap</a> contributors
      {' · '}<a href="https://openfreemap.org" {...EXTERNAL_LINK_PROPS}>OpenFreeMap</a>
    </div>
  </div>
}
