import { useRef, useState } from 'react'
import { formatDistanceLabel, measurementDistance, measurementsCopyText } from '../geo/measurements'
import { useMeasurementActions, useMeasurements } from '../state/measurement-store'
import { buttonClass, cn, Section } from './ui'

type CopyState = 'idle' | 'copied' | 'failed'

/** Finished measurements of the scene with a read-only text block to copy into chat. */
export function MeasurementPanel({ sceneId }: { sceneId: string }) {
  const measurements = useMeasurements(sceneId)
  const { removeMeasurement, clearMeasurements } = useMeasurementActions()
  const [copyState, setCopyState] = useState<CopyState>('idle')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const text = measurementsCopyText(sceneId, measurements, new Date())

  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopyState('copied')
    } catch {
      // No clipboard permission: select the text so Ctrl+C works.
      textareaRef.current?.select()
      setCopyState('failed')
    }
  }

  return (
    <Section title="Messungen">
      {measurements.length === 0 ? (
        <p className="text-sm text-slate-500">
          Noch keine Messung. Zwei Punkte auf der Karte anklicken.
        </p>
      ) : (
        <>
          <ol className="flex flex-col divide-y divide-slate-100 rounded-md border border-slate-200 text-sm">
            {measurements.map((measurement, index) => (
              <li key={measurement.id} className="flex items-center gap-2 px-2 py-1">
                <span className="w-5 text-slate-500 tabular-nums">{index + 1}</span>
                <span className="font-medium tabular-nums">
                  {formatDistanceLabel(measurementDistance(measurement))}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
                  {measurement.layerId}
                </span>
                <button
                  type="button"
                  aria-label={`Messung ${index + 1} löschen`}
                  onClick={() => removeMeasurement(sceneId, measurement.id)}
                  className="rounded px-1.5 text-slate-600 hover:bg-slate-100"
                >
                  ×
                </button>
              </li>
            ))}
          </ol>

          <textarea
            ref={textareaRef}
            readOnly
            aria-label="Messungen als Text"
            rows={Math.min(measurements.length + 1, 8)}
            value={text}
            onFocus={(event) => event.currentTarget.select()}
            className="w-full resize-y rounded-md border border-slate-300 bg-slate-50 p-2 font-mono text-xs"
          />
          <div className="flex flex-wrap gap-1.5">
            <button type="button" onClick={copy} className={buttonClass()}>
              Kopieren
            </button>
            <button
              type="button"
              onClick={() => clearMeasurements(sceneId)}
              className={buttonClass()}
            >
              Alle löschen
            </button>
          </div>
          <p
            aria-live="polite"
            className={cn('text-xs', copyState === 'failed' ? 'text-red-700' : 'text-slate-600')}
          >
            {copyState === 'copied' && 'In die Zwischenablage kopiert.'}
            {copyState === 'failed' &&
              'Kopieren fehlgeschlagen. Der Text ist markiert, bitte mit Strg+C kopieren.'}
          </p>
        </>
      )}
    </Section>
  )
}
