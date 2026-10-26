import { create } from 'zustand'
import type { LngLat } from '../geo/coordinates'

interface MapToolStore {
  /** While on, dragging on the map moves the image instead of panning the view. */
  moveMode: boolean
  /** Last pointer position while dragging an image; null when not dragging. */
  dragAnchor: LngLat | null
  /** First point of a measurement in progress; finished measurements live in measurement-store. */
  measureStart: LngLat | null
  /** Set while the Space bar is held: result layers are hidden to show the background. */
  hideResults: boolean
  actions: {
    setMoveMode: (on: boolean) => void
    setDragAnchor: (anchor: LngLat | null) => void
    setMeasureStart: (point: LngLat | null) => void
    clearMeasure: () => void
    setHideResults: (hidden: boolean) => void
  }
}

const useMapToolStore = create<MapToolStore>()((set) => ({
  moveMode: false,
  dragAnchor: null,
  measureStart: null,
  hideResults: false,
  actions: {
    setMoveMode: (moveMode) => set({ moveMode, dragAnchor: null }),
    setDragAnchor: (dragAnchor) => set({ dragAnchor }),
    setMeasureStart: (measureStart) => set({ measureStart }),
    clearMeasure: () => set({ measureStart: null }),
    setHideResults: (hideResults) => set({ hideResults }),
  },
}))

export const useMoveMode = () => useMapToolStore((state) => state.moveMode)

export const useDragAnchor = () => useMapToolStore((state) => state.dragAnchor)

export const useMeasureStart = () => useMapToolStore((state) => state.measureStart)

export const useHideResults = () => useMapToolStore((state) => state.hideResults)

export const useMapToolActions = () => useMapToolStore((state) => state.actions)
