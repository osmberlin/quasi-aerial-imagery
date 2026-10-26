import { describe, expect, it } from 'vitest'
import {
  applyAdjustment,
  extentCentre,
  extentCorners,
  IDENTITY_ADJUSTMENT,
  offsetAdjustment,
  placeCorners,
  stepAdjustment,
} from './adjustment'
import { localToLngLat, type LngLat } from './coordinates'

const ORIGIN: LngLat = [13.4153, 52.4947]
const EXTENT = { minX: -60, minY: -40, maxX: 60, maxY: 40 }

describe('extentCentre and extentCorners', () => {
  it('finds the centre of the extent', () => {
    expect(extentCentre(EXTENT)).toEqual([0, 0])
    expect(extentCentre({ minX: 10, minY: 0, maxX: 30, maxY: 20 })).toEqual([20, 10])
  })

  it('orders the corners top-left, top-right, bottom-right, bottom-left', () => {
    expect(extentCorners(EXTENT)).toEqual([
      [-60, 40],
      [60, 40],
      [60, -40],
      [-60, -40],
    ])
  })
})

describe('applyAdjustment', () => {
  it('leaves points unchanged for the identity adjustment', () => {
    expect(applyAdjustment([3, -4], IDENTITY_ADJUSTMENT, [1, 1])).toEqual([3, -4])
  })

  it('translates by dx and dy', () => {
    const adjustment = { dx: 2, dy: -3, rotationDeg: 0, scale: 1 }
    expect(applyAdjustment([10, 10], adjustment, [0, 0])).toEqual([12, 7])
  })

  it('rotates counter-clockwise about the centre', () => {
    const adjustment = { dx: 0, dy: 0, rotationDeg: 90, scale: 1 }
    const [x, y] = applyAdjustment([10, 0], adjustment, [0, 0])
    expect(x).toBeCloseTo(0, 9)
    expect(y).toBeCloseTo(10, 9)
  })

  it('scales about the centre, not the origin', () => {
    const adjustment = { dx: 0, dy: 0, rotationDeg: 0, scale: 2 }
    expect(applyAdjustment([6, 5], adjustment, [5, 5])).toEqual([7, 5])
  })

  it('keeps the centre fixed under rotation and scale', () => {
    const adjustment = { dx: 0, dy: 0, rotationDeg: 37, scale: 1.3 }
    const [x, y] = applyAdjustment([5, -2], adjustment, [5, -2])
    expect(x).toBeCloseTo(5, 9)
    expect(y).toBeCloseTo(-2, 9)
  })
})

describe('placeCorners', () => {
  it('equals the plain conversion for the identity adjustment', () => {
    const placed = placeCorners(
      extentCorners(EXTENT),
      ORIGIN,
      extentCentre(EXTENT),
      IDENTITY_ADJUSTMENT,
    )
    const expected = extentCorners(EXTENT).map((corner) => localToLngLat(ORIGIN, corner))
    placed.forEach((corner, index) => {
      expect(corner[0]).toBeCloseTo(expected[index]?.[0] ?? Number.NaN, 12)
      expect(corner[1]).toBeCloseTo(expected[index]?.[1] ?? Number.NaN, 12)
    })
  })

  it('moves the whole placement by the translation', () => {
    const base = placeCorners(
      extentCorners(EXTENT),
      ORIGIN,
      extentCentre(EXTENT),
      IDENTITY_ADJUSTMENT,
    )
    const moved = placeCorners(extentCorners(EXTENT), ORIGIN, extentCentre(EXTENT), {
      ...IDENTITY_ADJUSTMENT,
      dx: 10,
    })
    const expectedShift = localToLngLat(ORIGIN, [10, 0])[0] - ORIGIN[0]
    expect(moved[0][0] - base[0][0]).toBeCloseTo(expectedShift, 12)
    expect(moved[0][1]).toBeCloseTo(base[0][1], 12)
  })
})

describe('stepAdjustment and offsetAdjustment', () => {
  it('steps one field without floating point drift', () => {
    let adjustment = IDENTITY_ADJUSTMENT
    for (let step = 0; step < 3; step++) adjustment = stepAdjustment(adjustment, 'dx', 0.05)
    expect(adjustment.dx).toBe(0.15)
    expect(adjustment.dy).toBe(0)
  })

  it('steps the scale factor', () => {
    expect(stepAdjustment(IDENTITY_ADJUSTMENT, 'scale', 0.001).scale).toBe(1.001)
  })

  it('offsets both axes', () => {
    const adjustment = offsetAdjustment(IDENTITY_ADJUSTMENT, -0.5, 0.25)
    expect(adjustment).toEqual({ dx: -0.5, dy: 0.25, rotationDeg: 0, scale: 1 })
  })
})
