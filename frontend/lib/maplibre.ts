/**
 * MapLibre setup shared by the map components: the Bengaluru bounds and style, the
 * worker wiring, the colour tokens read from globals.css, and the capability checks.
 * Each component imports its own CSS, so it ships only with the route that shows a map.
 */

/** domain/areas.py::BENGALURU_BBOX, as (south, west, north, east). */
export const BBOX = { south: 12.80, west: 77.40, north: 13.15, east: 77.80 } as const
export const STYLE_URL = '/map/graphite.json'

/**
 * The colour tokens of globals.css, read live so the map can never drift from them.
 * Read from the hidden `.map-tokens` probe rather than the page: the basemap is graphite
 * in both themes, so its pins keep the dark values when the page is light.
 */
export function token(name: string, fallback: string): string {
  let probe = document.getElementById('map-tokens')
  if (!probe) {
    probe = document.createElement('div')
    probe.id = 'map-tokens'
    probe.className = 'map-tokens'
    probe.hidden = true
    document.body.appendChild(probe)
  }
  const value = getComputedStyle(probe).getPropertyValue(name).trim()
  return value || fallback
}

export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  return m ? `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${alpha})` : hex
}

export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  } catch {
    return false
  }
}

/**
 * MapLibre 6 finds its web worker from its own `import.meta.url`, which a
 * bundler rewrites to a file: URL, so the worker would never start. The worker
 * and the shared module it imports are copied into public/map/vendor by the
 * `map-worker` script (package.json: predev, prebuild) and served from there.
 * Pinned to an exact version because the two files must match the library.
 */
export const WORKER_URL = '/map/vendor/maplibre-gl-worker.mjs'
let workerSet = false
export function installWorker(ml: typeof import('maplibre-gl')) {
  if (workerSet) return
  ml.setWorkerUrl(new URL(WORKER_URL, window.location.origin).href)
  workerSet = true
}

export const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** The MapLibre library, loaded on first use (a dynamic import) and pointed at its worker. */
export function loadMapLibre(): Promise<typeof import('maplibre-gl')> {
  return import('maplibre-gl').then(ml => {
    installWorker(ml)
    return ml
  })
}
