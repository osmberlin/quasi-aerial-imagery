import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { type Adjustment, IDENTITY_ADJUSTMENT } from '../geo/adjustment'
import { safeLocalStorage } from './storage'

interface AdjustmentStore {
  /** Adjustment per scene id. Scenes without an entry use IDENTITY_ADJUSTMENT. */
  bySceneId: Record<string, Adjustment>
  actions: {
    updateAdjustment: (sceneId: string, update: (adjustment: Adjustment) => Adjustment) => void
    resetAdjustment: (sceneId: string) => void
  }
}

const useAdjustmentStore = create<AdjustmentStore>()(
  persist(
    (set) => ({
      bySceneId: {},
      actions: {
        updateAdjustment: (sceneId, update) =>
          set((state) => ({
            bySceneId: {
              ...state.bySceneId,
              [sceneId]: update(state.bySceneId[sceneId] ?? IDENTITY_ADJUSTMENT),
            },
          })),
        resetAdjustment: (sceneId) =>
          set((state) => {
            const { [sceneId]: _removed, ...rest } = state.bySceneId
            return { bySceneId: rest }
          }),
      },
    }),
    {
      name: 'streetlevel-ortho-viewer:adjustments',
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: ({ actions: _actions, ...data }) => data,
    },
  ),
)

export const useAdjustment = (sceneId: string) =>
  useAdjustmentStore((state) => state.bySceneId[sceneId] ?? IDENTITY_ADJUSTMENT)

export const useAdjustmentActions = () => useAdjustmentStore((state) => state.actions)
