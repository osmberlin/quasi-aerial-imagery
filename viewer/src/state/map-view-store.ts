import { create } from 'zustand'
import { metresPerScreenPixel } from '../geo/coordinates'

interface MapViewStore {
  loaded: boolean
  zoom: number
  /** Ground metres covered by one CSS pixel at the map centre. */
  metresPerPixel: number
  actions: {
    markLoaded: () => void
    setView: (zoom: number, lat: number) => void
  }
}

/** Live camera values, kept out of React state so that map movement does not re-render the sidebars. */
const useMapViewStore = create<MapViewStore>()((set) => ({
  loaded: false,
  zoom: 0,
  metresPerPixel: 0,
  actions: {
    markLoaded: () => set({ loaded: true }),
    setView: (zoom, lat) => set({ zoom, metresPerPixel: metresPerScreenPixel(lat, zoom) }),
  },
}))

export const useMapLoaded = () => useMapViewStore((state) => state.loaded)

export const useMapZoom = () => useMapViewStore((state) => state.zoom)

export const useMetresPerPixel = () => useMapViewStore((state) => state.metresPerPixel)

/** True when one image pixel covers more than one screen pixel. Such tiles use nearest-neighbour sampling. */
export const useIsMagnified = (layerMetresPerPixel: number) =>
  useMapViewStore((state) => state.metresPerPixel < layerMetresPerPixel)

export const useMapViewActions = () => useMapViewStore((state) => state.actions)
