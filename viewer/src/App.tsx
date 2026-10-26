import { AdjustPanel } from './components/AdjustPanel'
import { AssessmentPanel } from './components/AssessmentPanel'
import { BackgroundPicker } from './components/BackgroundPicker'
import { HelpBox } from './components/HelpBox'
import { LayerControls } from './components/LayerControls'
import { MeasurementPanel } from './components/MeasurementPanel'
import { SceneList } from './components/SceneList'
import { Badge, buttonClass } from './components/ui'
import { adjustmentKey, type LoadedManifest } from './data/manifest'
import { useManifest } from './data/use-manifest'
import { offsetAdjustment } from './geo/adjustment'
import { useKeyboardShortcuts } from './keyboard/use-keyboard-shortcuts'
import { resolveBackground } from './map/backgrounds'
import { MapStatus } from './map/MapStatus'
import { ViewerMap } from './map/ViewerMap'
import { useAdjustment, useAdjustmentActions } from './state/adjustment-store'
import { useMapToolActions } from './state/map-tool-store'
import {
  useSelectedLayerId,
  useSelectedSceneId,
  useSelectionActions,
} from './state/selection-store'
import {
  useCompareLayerId,
  useBackgroundId,
  useCompareOpacity,
  useLeftOpen,
  useOpacity,
  useRightOpen,
  useViewActions,
} from './state/view-store'

export function App() {
  const manifestQuery = useManifest()
  if (manifestQuery.isPending) return <Message>Lade Daten …</Message>
  if (manifestQuery.isError) {
    const error = manifestQuery.error
    return <Message tone="error">{error instanceof Error ? error.message : String(error)}</Message>
  }
  return <Viewer loaded={manifestQuery.data} />
}

function Message({ tone = 'info', children }: { tone?: 'info' | 'error'; children: string }) {
  return (
    <div className="flex h-dvh items-center justify-center bg-slate-100 p-6">
      <p
        role={tone === 'error' ? 'alert' : 'status'}
        className={tone === 'error' ? 'max-w-xl text-red-800' : 'text-slate-700'}
      >
        {children}
      </p>
    </div>
  )
}

function Viewer({ loaded }: { loaded: LoadedManifest }) {
  const { manifest, isSample, baseUrl } = loaded
  const scenes = manifest.scenes

  const selectedSceneId = useSelectedSceneId()
  const selectedLayerId = useSelectedLayerId()
  const { selectScene, selectLayer } = useSelectionActions()
  const compareLayerId = useCompareLayerId()
  const backgroundId = useBackgroundId()
  const opacity = useOpacity()
  const compareOpacity = useCompareOpacity()
  const leftOpen = useLeftOpen()
  const rightOpen = useRightOpen()
  const { toggleLeft, toggleRight } = useViewActions()
  const { updateAdjustment } = useAdjustmentActions()
  const { setHideResults, clearMeasure } = useMapToolActions()

  const scene = scenes.find((candidate) => candidate.id === selectedSceneId) ?? scenes[0]
  const activeLayer =
    scene?.layers.find((layer) => layer.id === selectedLayerId) ?? scene?.layers[0]
  const compareLayer = scene?.layers.find(
    (layer) => layer.id === compareLayerId && layer.id !== activeLayer?.id,
  )
  const adjustment = useAdjustment(scene ? adjustmentKey(scene) : '')
  const background = resolveBackground(backgroundId)

  useKeyboardShortcuts({
    selectLayerIndex: (index) => {
      const layer = scene?.layers[index]
      if (layer) selectLayer(layer.id)
    },
    stepLayer: (step) => {
      if (!scene) return
      const index = scene.layers.findIndex((layer) => layer.id === activeLayer?.id)
      const next = scene.layers[Math.min(Math.max(index + step, 0), scene.layers.length - 1)]
      if (next) selectLayer(next.id)
    },
    stepScene: (step) => {
      if (!scene) return
      const index = scenes.findIndex((candidate) => candidate.id === scene.id)
      const next = scenes[Math.min(Math.max(index + step, 0), scenes.length - 1)]
      if (next) selectScene(next.id)
    },
    nudge: (dx, dy) => {
      if (scene) updateAdjustment(adjustmentKey(scene), (value) => offsetAdjustment(value, dx, dy))
    },
    clearMeasure,
    setHideResults,
  })

  if (!scene) return <Message>Das Manifest enthält keine Szenen.</Message>

  return (
    <div className="flex h-dvh overflow-hidden bg-slate-100 text-slate-900">
      {leftOpen && (
        <aside className="flex w-[340px] shrink-0 flex-col gap-5 overflow-y-auto border-r border-slate-200 bg-white p-4">
          <header className="flex flex-col items-start gap-2">
            <h1 className="text-lg font-semibold">Orthofoto-Viewer</h1>
            {isSample && <Badge tone="warning">Beispieldaten</Badge>}
          </header>
          <SceneList scenes={scenes} selectedId={scene.id} />
          <LayerControls scene={scene} activeLayer={activeLayer} compareLayer={compareLayer} />
          <BackgroundPicker activeId={background.id} />
          <AdjustPanel sceneId={adjustmentKey(scene)} />
          <HelpBox />
        </aside>
      )}

      <main className="relative min-w-0 flex-1">
        <ViewerMap
          scene={scene}
          dataBaseUrl={baseUrl}
          activeLayer={activeLayer}
          compareLayer={compareLayer}
          opacity={opacity}
          compareOpacity={compareOpacity}
          adjustment={adjustment}
          background={background}
        />
        <MapStatus />
        <button
          type="button"
          onClick={toggleLeft}
          aria-expanded={leftOpen}
          className={`absolute top-3 left-3 z-10 ${buttonClass()}`}
        >
          Linke Leiste
        </button>
        <button
          type="button"
          onClick={toggleRight}
          aria-expanded={rightOpen}
          className={`absolute top-3 right-3 z-10 ${buttonClass()}`}
        >
          Rechte Leiste
        </button>
      </main>

      {rightOpen && (
        <aside className="flex w-[360px] shrink-0 flex-col gap-5 overflow-y-auto border-l border-slate-200 bg-white p-4">
          <MeasurementPanel sceneId={scene.id} />
          <AssessmentPanel scene={scene} activeLayer={activeLayer} />
        </aside>
      )}
    </div>
  )
}
