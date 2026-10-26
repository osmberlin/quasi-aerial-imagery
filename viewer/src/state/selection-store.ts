import { create } from 'zustand'
import { readUrlSelection, writeUrlSelection } from './url-selection'

interface SelectionStore {
  sceneId: string | null
  layerId: string | null
  actions: {
    selectScene: (sceneId: string) => void
    selectLayer: (layerId: string) => void
  }
}

const initialSelection = readUrlSelection(window.location.search)

const useSelectionStore = create<SelectionStore>()((set) => ({
  sceneId: initialSelection.sceneId,
  layerId: initialSelection.layerId,
  actions: {
    selectScene: (sceneId) => set({ sceneId }),
    selectLayer: (layerId) => set({ layerId }),
  },
}))

// The URL mirrors the selection, so a reload or a shared link opens the same scene and layer.
useSelectionStore.subscribe((state) => {
  writeUrlSelection({ sceneId: state.sceneId, layerId: state.layerId })
})

export const useSelectedSceneId = () => useSelectionStore((state) => state.sceneId)

export const useSelectedLayerId = () => useSelectionStore((state) => state.layerId)

export const useSelectionActions = () => useSelectionStore((state) => state.actions)
