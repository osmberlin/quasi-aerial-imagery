import { formatNumber } from '../format'
import { useMeasureStart } from '../state/map-tool-store'
import { useMapZoom, useMetresPerPixel } from '../state/map-view-store'

/** Zoom, ground resolution and a measuring hint, drawn over the map. */
export function MapStatus() {
  const zoom = useMapZoom()
  const metresPerPixel = useMetresPerPixel()
  const measureStart = useMeasureStart()

  return (
    <div
      aria-live="polite"
      className="pointer-events-none absolute bottom-3 left-1/2 flex max-w-[calc(100%-2rem)] -translate-x-1/2 flex-wrap justify-center gap-x-3 rounded-md bg-white/90 px-3 py-1.5 text-sm text-slate-800 shadow-sm"
    >
      <span className="tabular-nums">Zoom {formatNumber(zoom, 2)}</span>
      <span className="tabular-nums">{formatNumber(metresPerPixel, 4)} m/Pixel</span>
      <span className="text-slate-500">
        {measureStart
          ? 'Messen: zweiten Punkt klicken (Esc bricht ab)'
          : 'Messen: zwei Punkte klicken'}
      </span>
    </div>
  )
}
