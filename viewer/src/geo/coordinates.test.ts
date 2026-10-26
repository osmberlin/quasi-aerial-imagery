import { describe, expect, it } from 'vitest'
import {
  distanceMetres,
  type LngLat,
  lngLatBounds,
  lngLatToLocal,
  localToLngLat,
  metresPerDegreeLat,
  metresPerDegreeLng,
  metresPerScreenPixel,
  offsetBetween,
} from './coordinates'

const ORIGIN: LngLat = [13.4153, 52.4947]

describe('metres per degree', () => {
  it('matches known values near Berlin and at the equator', () => {
    expect(metresPerDegreeLat(52.5)).toBeGreaterThan(111270)
    expect(metresPerDegreeLat(52.5)).toBeLessThan(111290)
    expect(metresPerDegreeLng(52.5)).toBeGreaterThan(67800)
    expect(metresPerDegreeLng(52.5)).toBeLessThan(68000)
    expect(metresPerDegreeLat(0)).toBeCloseTo(110574, -1)
    expect(metresPerDegreeLng(0)).toBeCloseTo(111319, -1)
  })
})

describe('localToLngLat', () => {
  it('maps the local origin onto the scene origin', () => {
    expect(localToLngLat(ORIGIN, [0, 0])).toEqual(ORIGIN)
  })

  it('moves north by metres of latitude and keeps the longitude', () => {
    const [lng, lat] = localToLngLat(ORIGIN, [0, 1000])
    expect(lng).toBeCloseTo(ORIGIN[0], 12)
    expect(lat - ORIGIN[1]).toBeCloseTo(1000 / metresPerDegreeLat(ORIGIN[1]), 12)
  })

  it('moves east by metres of longitude and keeps the latitude', () => {
    const [lng, lat] = localToLngLat(ORIGIN, [1000, 0])
    expect(lat).toBeCloseTo(ORIGIN[1], 12)
    expect(lng - ORIGIN[0]).toBeCloseTo(1000 / metresPerDegreeLng(ORIGIN[1]), 12)
  })
})

describe('lngLatToLocal', () => {
  it('is the exact inverse of localToLngLat', () => {
    const point: [number, number] = [-42.5, 118.25]
    const back = lngLatToLocal(ORIGIN, localToLngLat(ORIGIN, point))
    expect(back[0]).toBeCloseTo(point[0], 9)
    expect(back[1]).toBeCloseTo(point[1], 9)
  })
})

describe('offsetBetween', () => {
  it('returns the metre offset between two lng/lat positions', () => {
    const target = localToLngLat(ORIGIN, [12.5, -7.25])
    const [dx, dy] = offsetBetween(ORIGIN, ORIGIN, target)
    expect(dx).toBeCloseTo(12.5, 9)
    expect(dy).toBeCloseTo(-7.25, 9)
  })
})

describe('distanceMetres', () => {
  it('is close to the planar distance over a few hundred metres', () => {
    const end = localToLngLat(ORIGIN, [300, 400])
    expect(distanceMetres(ORIGIN, end)).toBeCloseTo(500, -1)
  })
})

describe('metresPerScreenPixel', () => {
  it('is the world size at zoom 0 on the equator', () => {
    expect(metresPerScreenPixel(0, 0)).toBeCloseTo(78271.5, 0)
  })

  it('halves for every zoom level', () => {
    expect(metresPerScreenPixel(52.5, 19)).toBeCloseTo(metresPerScreenPixel(52.5, 18) / 2, 9)
  })
})

describe('lngLatBounds', () => {
  it('returns the south-west and north-east corners', () => {
    expect(
      lngLatBounds([
        [1, 5],
        [3, 2],
        [2, 4],
      ]),
    ).toEqual([
      [1, 2],
      [3, 5],
    ])
  })
})
