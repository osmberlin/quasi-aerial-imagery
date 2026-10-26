import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { BackgroundId } from '../map/backgrounds'
import { safeLocalStorage } from './storage'

interface ViewStore {
  backgroundId: BackgroundId
  /** Opacity of the active result layer, 0 to 100. */
  opacity: number
  compareLayerId: string | null
  /** Opacity of the comparison layer, 0 to 100. */
  compareOpacity: number
  /** Scene markers (measuring spots) on the map. */
  showMarkers: boolean
  leftOpen: boolean
  rightOpen: boolean
  actions: {
    setBackground: (backgroundId: BackgroundId) => void
    setOpacity: (opacity: number) => void
    setCompareLayer: (layerId: string | null) => void
    setCompareOpacity: (opacity: number) => void
    setShowMarkers: (show: boolean) => void
    toggleLeft: () => void
    toggleRight: () => void
  }
}

const useViewStore = create<ViewStore>()(
  persist(
    (set) => ({
      backgroundId: 'openfreemap',
      opacity: 100,
      compareLayerId: null,
      compareOpacity: 50,
      showMarkers: true,
      leftOpen: true,
      rightOpen: true,
      actions: {
        setBackground: (backgroundId) => set({ backgroundId }),
        setOpacity: (opacity) => set({ opacity }),
        setCompareLayer: (compareLayerId) => set({ compareLayerId }),
        setCompareOpacity: (compareOpacity) => set({ compareOpacity }),
        setShowMarkers: (showMarkers) => set({ showMarkers }),
        toggleLeft: () => set((state) => ({ leftOpen: !state.leftOpen })),
        toggleRight: () => set((state) => ({ rightOpen: !state.rightOpen })),
      },
    }),
    {
      name: 'streetlevel-ortho-viewer:view',
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: ({ actions: _actions, ...settings }) => settings,
    },
  ),
)

export const useBackgroundId = () => useViewStore((state) => state.backgroundId)

export const useOpacity = () => useViewStore((state) => state.opacity)

export const useCompareLayerId = () => useViewStore((state) => state.compareLayerId)

export const useCompareOpacity = () => useViewStore((state) => state.compareOpacity)

export const useShowMarkers = () => useViewStore((state) => state.showMarkers)

export const useLeftOpen = () => useViewStore((state) => state.leftOpen)

export const useRightOpen = () => useViewStore((state) => state.rightOpen)

export const useViewActions = () => useViewStore((state) => state.actions)
