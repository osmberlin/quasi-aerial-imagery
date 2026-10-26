import { distance } from '@turf/distance'

/** WGS84 longitude and latitude in degrees. */
export type LngLat = [lng: number, lat: number]

/** Local metres relative to a scene origin: x east, y north. */
export type Point = [x: number, y: number]

const DEG_TO_RAD = Math.PI / 180

/** Equatorial circumference of the earth in metres (Web Mercator). */
const EARTH_CIRCUMFERENCE_M = 40075016.686

/** MapLibre's world size per zoom level in CSS pixels. */
const WORLD_SIZE_PX = 512

/** Metres per degree of latitude at `lat` (WGS84 series). */
export function metresPerDegreeLat(lat: number): number {
  const phi = lat * DEG_TO_RAD
  return (
    111132.92 - 559.82 * Math.cos(2 * phi) + 1.175 * Math.cos(4 * phi) - 0.0023 * Math.cos(6 * phi)
  )
}

/** Metres per degree of longitude at `lat` (WGS84 series, third term included). */
export function metresPerDegreeLng(lat: number): number {
  const phi = lat * DEG_TO_RAD
  return 111412.84 * Math.cos(phi) - 93.5 * Math.cos(3 * phi) + 0.118 * Math.cos(5 * phi)
}

/**
 * Converts local metres to lng/lat. Both axes use the latitude of the scene origin, so the
 * mapping is linear and `lngLatToLocal` is its exact inverse.
 */
export function localToLngLat(origin: LngLat, [x, y]: Point): LngLat {
  const [lng0, lat0] = origin
  return [lng0 + x / metresPerDegreeLng(lat0), lat0 + y / metresPerDegreeLat(lat0)]
}

/** Inverse of `localToLngLat`. */
export function lngLatToLocal(origin: LngLat, [lng, lat]: LngLat): Point {
  const [lng0, lat0] = origin
  return [(lng - lng0) * metresPerDegreeLng(lat0), (lat - lat0) * metresPerDegreeLat(lat0)]
}

/** Local metre offset from `from` to `to`, both given as lng/lat. */
export function offsetBetween(origin: LngLat, from: LngLat, to: LngLat): Point {
  const start = lngLatToLocal(origin, from)
  const end = lngLatToLocal(origin, to)
  return [end[0] - start[0], end[1] - start[1]]
}

/** Ground distance covered by one CSS pixel at latitude `lat` and zoom `zoom`. */
export function metresPerScreenPixel(lat: number, zoom: number): number {
  return (EARTH_CIRCUMFERENCE_M * Math.cos(lat * DEG_TO_RAD)) / (WORLD_SIZE_PX * 2 ** zoom)
}

/** Geodesic distance on the sphere (Turf haversine) in metres. */
export function distanceMetres(from: LngLat, to: LngLat): number {
  return distance(from, to, { units: 'meters' })
}

/** South-west and north-east corner of the box around the given points. */
export function lngLatBounds(points: readonly LngLat[]): [LngLat, LngLat] {
  const lngs = points.map((point) => point[0])
  const lats = points.map((point) => point[1])
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ]
}
