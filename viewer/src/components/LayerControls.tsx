import type { ResultLayer, Scene } from '../data/manifest'
import { useSelectionActions } from '../state/selection-store'
import { useCompareOpacity, useOpacity, useShowMarkers, useViewActions } from '../state/view-store'
import { buttonClass, Section } from './ui'

type LayerControlsProps = {
  scene: Scene
  activeLayer: ResultLayer | undefined
  compareLayer: ResultLayer | undefined
}

export function LayerControls({ scene, activeLayer, compareLayer }: LayerControlsProps) {
  const { selectLayer } = useSelectionActions()
  const opacity = useOpacity()
  const compareOpacity = useCompareOpacity()
  const showMarkers = useShowMarkers()
  const { setOpacity, setCompareLayer, setCompareOpacity, setShowMarkers } = useViewActions()
  const otherLayers = scene.layers.filter((layer) => layer.id !== activeLayer?.id)

  return (
    <Section title="Ergebnis-Ebene">
      <div className="flex flex-wrap gap-1.5">
        {scene.layers.map((layer, index) => {
          const active = layer.id === activeLayer?.id
          return (
            <button
              key={layer.id}
              type="button"
              aria-pressed={active}
              onClick={() => selectLayer(layer.id)}
              className={buttonClass(active)}
            >
              <span className="tabular-nums opacity-70">{index + 1}</span>
              {layer.title}
            </button>
          )
        })}
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="flex justify-between">
          <span>Deckkraft</span>
          <span className="tabular-nums">{opacity} %</span>
        </span>
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={opacity}
          onChange={(event) => setOpacity(event.currentTarget.valueAsNumber)}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span>Vergleichs-Ebene</span>
        <select
          value={compareLayer?.id ?? ''}
          onChange={(event) =>
            setCompareLayer(event.currentTarget.value === '' ? null : event.currentTarget.value)
          }
          className="rounded-md border border-slate-300 bg-white px-2 py-1"
        >
          <option value="">keine</option>
          {otherLayers.map((layer) => (
            <option key={layer.id} value={layer.id}>
              {layer.title}
            </option>
          ))}
        </select>
      </label>

      {(scene.markers?.length ?? 0) > 0 && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showMarkers}
            onChange={(event) => setShowMarkers(event.currentTarget.checked)}
          />
          Messstellen anzeigen ({scene.markers?.length})
        </label>
      )}

      {compareLayer && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="flex justify-between">
            <span>Deckkraft Vergleich</span>
            <span className="tabular-nums">{compareOpacity} %</span>
          </span>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={compareOpacity}
            onChange={(event) => setCompareOpacity(event.currentTarget.valueAsNumber)}
          />
        </label>
      )}
    </Section>
  )
}
