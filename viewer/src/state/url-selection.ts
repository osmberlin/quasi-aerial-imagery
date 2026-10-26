/** Query parameters that hold the selected scene, the layer and the map viewport. Written in this order. */
const SCENE_PARAM = 'scene'
const LAYER_PARAM = 'layer'
const MAP_PARAM = 'map'

export type UrlSelection = { sceneId: string | null; layerId: string | null }

/** Map viewport as it appears in the URL: `?map=zoom/lat/lng`. */
export type MapParam = { zoom: number; lat: number; lng: number }

/** The viewer's map allows zoom up to 24 (see `maxZoom` in ViewerMap), so the URL accepts the same range. */
const MAX_ZOOM = 24

const NUMBER_PATTERN = /^-?\d+(\.\d+)?$/

function parseNumber(text: string | undefined, min: number, max: number): number | null {
  if (text === undefined || !NUMBER_PATTERN.test(text)) return null
  const value = Number(text)
  return value >= min && value <= max ? value : null
}

/** Returns null for anything that is not exactly `zoom/lat/lng` with values in range. */
export function parseMapParam(value: string | null): MapParam | null {
  if (value === null) return null
  const parts = value.split('/')
  if (parts.length !== 3) return null
  const zoom = parseNumber(parts[0], 0, MAX_ZOOM)
  const lat = parseNumber(parts[1], -90, 90)
  const lng = parseNumber(parts[2], -180, 180)
  if (zoom === null || lat === null || lng === null) return null
  return { zoom, lat, lng }
}

function round(value: number, decimals: number): number {
  return Number.parseFloat(value.toFixed(decimals))
}

/** Zoom gets 1 decimal. Lat and lng get 3 decimals below zoom 13, 4 up to zoom 17 and 5 from zoom 17. */
export function serializeMapParam({ zoom, lat, lng }: MapParam): string {
  const decimals = zoom >= 17 ? 5 : zoom < 13 ? 3 : 4
  return `${round(zoom, 1)}/${round(lat, decimals)}/${round(lng, decimals)}`
}

export function readUrlSelection(search: string): UrlSelection {
  const params = new URLSearchParams(search)
  return { sceneId: params.get(SCENE_PARAM), layerId: params.get(LAYER_PARAM) }
}

/** The map viewport from the query string, or null when it is missing or invalid. */
export function readUrlMap(search: string): MapParam | null {
  return parseMapParam(new URLSearchParams(search).get(MAP_PARAM))
}

export type UrlPatch = Partial<UrlSelection> & { map?: MapParam | null }

/**
 * Returns the query string with the patch applied. Parts that the patch leaves out keep their value
 * from `search`; a null value removes the parameter. Other parameters are kept and come first,
 * then scene, layer and map. The map value keeps its slashes unescaped.
 */
export function buildSearch(search: string, patch: UrlPatch): string {
  const params = new URLSearchParams(search)
  const current = {
    sceneId: params.get(SCENE_PARAM),
    layerId: params.get(LAYER_PARAM),
    map: parseMapParam(params.get(MAP_PARAM)),
  }
  const sceneId = patch.sceneId === undefined ? current.sceneId : patch.sceneId
  const layerId = patch.layerId === undefined ? current.layerId : patch.layerId
  const map = patch.map === undefined ? current.map : patch.map

  params.delete(SCENE_PARAM)
  params.delete(LAYER_PARAM)
  params.delete(MAP_PARAM)
  if (sceneId !== null) params.set(SCENE_PARAM, sceneId)
  if (layerId !== null) params.set(LAYER_PARAM, layerId)
  const parts = [params.toString()]
  if (map !== null) parts.push(`${MAP_PARAM}=${serializeMapParam(map)}`)
  const text = parts.filter(Boolean).join('&')
  return text ? `?${text}` : ''
}

/** Replaces the current history entry, so changing the URL does not add back-button steps. */
function replaceSearch(patch: UrlPatch): void {
  const { pathname, search, hash } = window.location
  const nextSearch = buildSearch(search, patch)
  if (nextSearch !== search) {
    window.history.replaceState(window.history.state, '', `${pathname}${nextSearch}${hash}`)
  }
}

export function writeUrlSelection(selection: UrlSelection): void {
  replaceSearch(selection)
}

/** Called on moveend, never per frame. */
export function writeUrlMap(map: MapParam): void {
  replaceSearch({ map })
}
