import { describe, expect, it } from 'vitest'
import { localToLngLat } from './coordinates'
import {
  formatDistanceLabel,
  type Measurement,
  measurementLine,
  measurementsCopyText,
  midpoint,
} from './measurements'

const A: [number, number] = [7.4894400123, 48.4582000456]
const B = localToLngLat(A, [3, 0])
const MEASUREMENT: Measurement = { id: 'm1', layerId: 'final', a: A, b: B, zoom: 22.54 }

describe('formatDistanceLabel', () => {
  it('uses centimetres and a decimal comma', () => {
    expect(formatDistanceLabel(3.8234)).toBe('3,82 m')
    expect(formatDistanceLabel(0)).toBe('0,00 m')
  })
})

describe('midpoint', () => {
  it('is the average of both points', () => {
    expect(midpoint([0, 2], [4, 4])).toEqual([2, 3])
  })
})

describe('measurementLine', () => {
  it('has scene, layer, 7-decimal coordinates, 3-decimal metres with dot, and zoom', () => {
    const line = measurementLine('elsass-messstelle', MEASUREMENT)
    expect(line).toMatch(
      /^scene=elsass-messstelle layer=final a=7\.4894400,48\.4582000 b=7\.\d{7},48\.4582000 d=\d\.\d{3} m zoom=22\.5$/,
    )
    // Haversine on the 6371 km sphere is about 0.3 % shorter than the local 3 m offset at 48.5 N.
    expect(line).toContain(' d=2.991 m ')
  })
})

describe('measurementsCopyText', () => {
  it('starts with a dated header and has one line per measurement', () => {
    const text = measurementsCopyText(
      'elsass-messstelle',
      [MEASUREMENT, { ...MEASUREMENT, id: 'm2' }],
      new Date('2026-10-25T12:00:00Z'),
    )
    const lines = text.split('\n')
    expect(lines).toHaveLength(3)
    expect(lines[0]).toBe('# Messungen elsass-messstelle 2026-10-25')
    expect(lines[1]).toBe(lines[2])
  })
})
