import { type LngLat, localToLngLat, type Point } from './coordinates'

/** Extent in local metres around the scene origin (x east, y north). */
export type Extent = { minX: number; minY: number; maxX: number; maxY: number }

/** Four corners in local metres: top-left, top-right, bottom-right, bottom-left. */
export type Corners = [Point, Point, Point, Point]

/** Same corners in lng/lat, in the order MapLibre expects for image sources. */
export type LngLatCorners = [LngLat, LngLat, LngLat, LngLat]

/**
 * Placement correction of one scene. It applies to every tile of every layer of that scene:
 * rotation and scale about the centre of the scene extent, then the translation dx/dy.
 * rotationDeg is counter-clockwise with north up. scale is a factor, 1 means unchanged.
 */
export type Adjustment = { dx: number; dy: number; rotationDeg: number; scale: number }

export type AdjustmentField = keyof Adjustment

export const IDENTITY_ADJUSTMENT: Adjustment = { dx: 0, dy: 0, rotationDeg: 0, scale: 1 }

const DEG_TO_RAD = Math.PI / 180

/** Removes floating point noise that repeated small steps would otherwise accumulate. */
export function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6
}

export function extentCentre(extent: Extent): Point {
  return [(extent.minX + extent.maxX) / 2, (extent.minY + extent.maxY) / 2]
}

export function extentCorners(extent: Extent): Corners {
  return [
    [extent.minX, extent.maxY],
    [extent.maxX, extent.maxY],
    [extent.maxX, extent.minY],
    [extent.minX, extent.minY],
  ]
}

/** Applies the adjustment to one local point: scale and rotate about `centre`, then translate. */
export function applyAdjustment(point: Point, adjustment: Adjustment, centre: Point): Point {
  const [x, y] = point
  const [cx, cy] = centre
  const theta = adjustment.rotationDeg * DEG_TO_RAD
  const cos = Math.cos(theta)
  const sin = Math.sin(theta)
  const vx = (x - cx) * adjustment.scale
  const vy = (y - cy) * adjustment.scale
  return [cx + cos * vx - sin * vy + adjustment.dx, cy + sin * vx + cos * vy + adjustment.dy]
}

/** Adjusts the corners and converts them to lng/lat relative to the scene origin. */
export function placeCorners(
  corners: Corners,
  origin: LngLat,
  centre: Point,
  adjustment: Adjustment,
): LngLatCorners {
  const place = (point: Point): LngLat =>
    localToLngLat(origin, applyAdjustment(point, adjustment, centre))
  return [place(corners[0]), place(corners[1]), place(corners[2]), place(corners[3])]
}

export function stepAdjustment(
  adjustment: Adjustment,
  field: AdjustmentField,
  delta: number,
): Adjustment {
  return { ...adjustment, [field]: round6(adjustment[field] + delta) }
}

export function offsetAdjustment(adjustment: Adjustment, dx: number, dy: number): Adjustment {
  return { ...adjustment, dx: round6(adjustment.dx + dx), dy: round6(adjustment.dy + dy) }
}
