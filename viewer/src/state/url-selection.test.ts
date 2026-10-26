import { describe, expect, it } from 'vitest'
import {
  buildSearch,
  parseMapParam,
  readUrlMap,
  readUrlSelection,
  serializeMapParam,
} from './url-selection'

describe('readUrlSelection', () => {
  it('reads scene and layer from the query string', () => {
    expect(readUrlSelection('?scene=beispiel-nord&layer=median')).toEqual({
      sceneId: 'beispiel-nord',
      layerId: 'median',
    })
  })

  it('returns null for missing parameters', () => {
    expect(readUrlSelection('')).toEqual({ sceneId: null, layerId: null })
  })
})

describe('buildSearch', () => {
  it('writes the selection and keeps unrelated parameters', () => {
    expect(buildSearch('?foo=1&scene=old', { sceneId: 'beispiel-sued', layerId: 'splat' })).toBe(
      '?foo=1&scene=beispiel-sued&layer=splat',
    )
  })

  it('removes parameters whose selection is null', () => {
    expect(buildSearch('?scene=a&layer=b', { sceneId: 'a', layerId: null })).toBe('?scene=a')
    expect(buildSearch('?scene=a', { sceneId: null, layerId: null })).toBe('')
  })
})

describe('parseMapParam', () => {
  it('parses zoom/lat/lng', () => {
    expect(parseMapParam('17.5/52.49/13.42')).toEqual({ zoom: 17.5, lat: 52.49, lng: 13.42 })
    expect(parseMapParam('0/-90/-180')).toEqual({ zoom: 0, lat: -90, lng: -180 })
  })

  it.each([
    null,
    '',
    '17/52',
    '17/52/13/1',
    '/52/13',
    'a/b/c',
    '17/52/',
    '25/52/13',
    '-1/52/13',
    '17/91/13',
    '17/52/181',
    '@52.8,13.6,12.5z',
    '17/NaN/13',
    '17/1e1/13',
  ])('rejects %j', (value) => {
    expect(parseMapParam(value)).toBeNull()
  })
})

describe('serializeMapParam', () => {
  it('rounds by zoom', () => {
    expect(serializeMapParam({ zoom: 12.04, lat: 52.123456, lng: 13.987654 })).toBe(
      '12/52.123/13.988',
    )
    expect(serializeMapParam({ zoom: 14.96, lat: 52.123456, lng: 13.987654 })).toBe(
      '15/52.1235/13.9877',
    )
    expect(serializeMapParam({ zoom: 18.25, lat: 52.1234567, lng: 13.9876543 })).toBe(
      '18.3/52.12346/13.98765',
    )
  })

  it('round-trips through parseMapParam', () => {
    const text = serializeMapParam({ zoom: 19.2, lat: 47.89123, lng: 7.55432 })
    expect(parseMapParam(text)).toEqual({ zoom: 19.2, lat: 47.89123, lng: 7.55432 })
  })
})

describe('map in the query string', () => {
  const map = { zoom: 18.3, lat: 47.89123, lng: 7.55432 }

  it('reads a valid map and ignores an invalid one', () => {
    expect(readUrlMap('?scene=a&map=18.3/47.89123/7.55432')).toEqual(map)
    expect(readUrlMap('?map=18.3/47.89123/7.55432')).toEqual(map)
    expect(readUrlMap('?map=garbage')).toBeNull()
    expect(readUrlMap('?scene=a')).toBeNull()
  })

  it('writes scene, layer, map in that order with unescaped slashes', () => {
    expect(buildSearch('?map=1/2/3&layer=b&scene=a', { map })).toBe(
      '?scene=a&layer=b&map=18.3/47.89123/7.55432',
    )
    expect(buildSearch('', { sceneId: 'a', layerId: 'b', map })).toBe(
      '?scene=a&layer=b&map=18.3/47.89123/7.55432',
    )
  })

  it('keeps the map when only the selection changes, and other parameters stay first', () => {
    expect(
      buildSearch('?foo=1&scene=a&map=18.3/47.89123/7.55432', { sceneId: 'b', layerId: 'c' }),
    ).toBe('?foo=1&scene=b&layer=c&map=18.3/47.89123/7.55432')
  })

  it('drops an invalid map and removes the map when asked', () => {
    expect(buildSearch('?scene=a&map=garbage', { layerId: 'b' })).toBe('?scene=a&layer=b')
    expect(buildSearch('?scene=a&map=18.3/47.89123/7.55432', { map: null })).toBe('?scene=a')
  })
})
