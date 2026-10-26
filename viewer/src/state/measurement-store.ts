import { z } from 'zod'
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { Measurement } from '../geo/measurements'
import { safeLocalStorage } from './storage'

const LngLatSchema = z.tuple([z.number(), z.number()])

const MeasurementSchema = z.object({
  id: z.string(),
  layerId: z.string(),
  a: LngLatSchema,
  b: LngLatSchema,
  zoom: z.number(),
})

const BySceneIdSchema = z.record(z.string(), z.array(MeasurementSchema))

interface MeasurementStore {
  /** Finished measurements per scene id, oldest first. */
  bySceneId: Record<string, Measurement[]>
  actions: {
    addMeasurement: (sceneId: string, measurement: Omit<Measurement, 'id'>) => void
    removeMeasurement: (sceneId: string, id: string) => void
    clearMeasurements: (sceneId: string) => void
  }
}

const NO_MEASUREMENTS: Measurement[] = []

function newId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

const useMeasurementStore = create<MeasurementStore>()(
  persist(
    (set) => ({
      bySceneId: {},
      actions: {
        addMeasurement: (sceneId, measurement) =>
          set((state) => ({
            bySceneId: {
              ...state.bySceneId,
              [sceneId]: [
                ...(state.bySceneId[sceneId] ?? NO_MEASUREMENTS),
                { ...measurement, id: newId() },
              ],
            },
          })),
        removeMeasurement: (sceneId, id) =>
          set((state) => ({
            bySceneId: {
              ...state.bySceneId,
              [sceneId]: (state.bySceneId[sceneId] ?? NO_MEASUREMENTS).filter(
                (measurement) => measurement.id !== id,
              ),
            },
          })),
        clearMeasurements: (sceneId) =>
          set((state) => {
            const { [sceneId]: _removed, ...rest } = state.bySceneId
            return { bySceneId: rest }
          }),
      },
    }),
    {
      name: 'streetlevel-ortho-viewer:measurements',
      storage: createJSONStorage(() => safeLocalStorage),
      partialize: ({ actions: _actions, ...data }) => data,
      // Stored data from an older or hand-edited version must not crash the viewer.
      merge: (persisted, current) => {
        const parsed = BySceneIdSchema.safeParse(
          (persisted as { bySceneId?: unknown } | undefined)?.bySceneId,
        )
        return parsed.success ? { ...current, bySceneId: parsed.data } : current
      },
    },
  ),
)

export const useMeasurements = (sceneId: string) =>
  useMeasurementStore((state) => state.bySceneId[sceneId] ?? NO_MEASUREMENTS)

export const useMeasurementActions = () => useMeasurementStore((state) => state.actions)
