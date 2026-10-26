import { formatNumber } from '../format'
import { distanceMetres, type LngLat } from './coordinates'

/** One finished two-point measurement. Distance is derived, never stored. */
export type Measurement = {
  id: string
  layerId: string
  a: LngLat
  b: LngLat
  /** Map zoom when the second point was placed: tells how exactly the points could be set. */
  zoom: number
}

export function measurementDistance({ a, b }: Pick<Measurement, 'a' | 'b'>): number {
  return distanceMetres(a, b)
}

/** Label on the line: centimetres, German decimal comma, e.g. "3,82 m". */
export function formatDistanceLabel(metres: number): string {
  return `${formatNumber(metres, 2)} m`
}

/** Middle of the segment, where the label sits. Plain averaging is exact enough at metre scale. */
export function midpoint(a: LngLat, b: LngLat): LngLat {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
}

function coordinates([lng, lat]: LngLat): string {
  return `${lng.toFixed(7)},${lat.toFixed(7)}`
}

/** One line of the copy text. Dot decimals, so that it is unambiguous when pasted into chat. */
export function measurementLine(sceneId: string, measurement: Measurement): string {
  const { layerId, a, b, zoom } = measurement
  const metres = measurementDistance(measurement).toFixed(3)
  return `scene=${sceneId} layer=${layerId} a=${coordinates(a)} b=${coordinates(b)} d=${metres} m zoom=${zoom.toFixed(1)}`
}

/** Header line with the date, then one line per measurement. */
export function measurementsCopyText(
  sceneId: string,
  measurements: readonly Measurement[],
  date: Date,
): string {
  const header = `# Messungen ${sceneId} ${date.toISOString().slice(0, 10)}`
  return [header, ...measurements.map((measurement) => measurementLine(sceneId, measurement))].join(
    '\n',
  )
}
