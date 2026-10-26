import type { StyleSpecification } from 'maplibre-gl'

export type BackgroundId = 'openfreemap' | 'berlin-dop-2025' | 'tilda-2026' | 'tilda-2025' | 'osm'

export type Background = {
  id: BackgroundId
  label: string
  /** Style URL or inline style. A new value switches the basemap. */
  style: string | StyleSpecification
  attribution: string
}

const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty'

// Read at build time. The aerial URLs contain a token: never log them and never hard-code them.
const TILDA_2026_URL = (import.meta.env.VITE_TILDA_2026_URL ?? '').trim()
const TILDA_2025_URL = (import.meta.env.VITE_TILDA_2025_URL ?? '').trim()

/** Raster basemap from an XYZ template. Source maxzoom 19 lets MapLibre overzoom beyond that. */
function rasterStyle(tiles: string, tileSize: number): StyleSpecification {
  return {
    version: 8,
    sources: {
      background: { type: 'raster', tiles: [tiles], tileSize, maxzoom: 19 },
    },
    layers: [{ id: 'background', type: 'raster', source: 'background' }],
  }
}

const DEFAULT_BACKGROUND: Background = {
  id: 'openfreemap',
  label: 'Karte (OpenFreeMap)',
  style: OPENFREEMAP_STYLE,
  attribution: '© OpenFreeMap © OpenMapTiles, Daten © OpenStreetMap',
}

function tildaBackground(id: BackgroundId, label: string, url: string): Background | null {
  if (url === '') return null
  return { id, label, style: rasterStyle(url, 512), attribution: 'Luftbild' }
}

// Official aerial imagery of Berlin, 20 cm, open data (Datenlizenz Deutschland – Zero – 2.0),
// served by Geoportal Berlin as WMS. No key needed, so it is the aerial for the public viewer.
const BERLIN_DOP_2025: Background = {
  id: 'berlin-dop-2025',
  label: 'Luftbild Berlin 2025 (20 cm, offen)',
  style: rasterStyle(
    'https://gdi.berlin.de/services/wms/dop_2025_fruehjahr?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap' +
      '&LAYERS=dop_2025&STYLES=&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=512&HEIGHT=512&FORMAT=image/jpeg',
    512,
  ),
  attribution: 'Luftbild: Geoportal Berlin / DOP20 2025, dl-de/zero-2-0',
}

const OSM_BACKGROUND: Background = {
  id: 'osm',
  label: 'OpenStreetMap (Raster)',
  style: rasterStyle('https://tile.openstreetmap.org/{z}/{x}/{y}.png', 256),
  attribution: '© OpenStreetMap-Mitwirkende',
}

/** Backgrounds that can be shown. The TILDA aerial options exist only when their URL is configured. */
export const BACKGROUNDS: Background[] = [
  DEFAULT_BACKGROUND,
  BERLIN_DOP_2025,
  tildaBackground('tilda-2026', 'Luftbild 2026', TILDA_2026_URL),
  tildaBackground('tilda-2025', 'Luftbild 2025', TILDA_2025_URL),
  OSM_BACKGROUND,
].filter((background): background is Background => background !== null)

/** Falls back to the default when a stored choice is no longer available. */
export function resolveBackground(id: BackgroundId): Background {
  return BACKGROUNDS.find((background) => background.id === id) ?? DEFAULT_BACKGROUND
}
