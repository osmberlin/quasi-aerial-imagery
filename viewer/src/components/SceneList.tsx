import type { Scene } from '../data/manifest'
import { useSelectionActions } from '../state/selection-store'
import { Badge, cn, Section } from './ui'

type SceneListProps = {
  scenes: Scene[]
  selectedId: string
}

export function SceneList({ scenes, selectedId }: SceneListProps) {
  const { selectScene } = useSelectionActions()
  return (
    <Section title="Szenen">
      <ul className="flex flex-col gap-1.5">
        {scenes.map((scene) => {
          const selected = scene.id === selectedId
          return (
            <li key={scene.id}>
              <button
                type="button"
                aria-current={selected ? 'true' : undefined}
                onClick={() => selectScene(scene.id)}
                className={cn(
                  'flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left',
                  selected
                    ? 'border-sky-600 bg-sky-50'
                    : 'border-slate-200 bg-white hover:bg-slate-50',
                )}
              >
                <span className="font-medium">{scene.title}</span>
                <span className="text-xs text-slate-600 tabular-nums">
                  {scene.captured} · {scene.frames} Frames
                </span>
                {scene.placement ? (
                  <Badge tone="info">manuell eingepasst ({scene.placement.date})</Badge>
                ) : (
                  !scene.georeferenced && <Badge tone="warning">nicht georeferenziert</Badge>
                )}
              </button>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}
