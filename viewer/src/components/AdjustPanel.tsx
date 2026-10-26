import { useState } from 'react'
import { type Adjustment, type AdjustmentField, round6 } from '../geo/adjustment'
import { useAdjustment, useAdjustmentActions } from '../state/adjustment-store'
import { useMapToolActions, useMoveMode } from '../state/map-tool-store'
import { buttonClass, Section, cn } from './ui'

type AdjustPanelProps = {
  sceneId: string
}

export function AdjustPanel({ sceneId }: AdjustPanelProps) {
  const adjustment = useAdjustment(sceneId)
  const { updateAdjustment, resetAdjustment } = useAdjustmentActions()
  const moveMode = useMoveMode()
  const { setMoveMode } = useMapToolActions()
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')

  function setField(field: AdjustmentField, value: number) {
    updateAdjustment(sceneId, (current) => ({ ...current, [field]: value }))
  }

  async function copyAdjustment() {
    const json = JSON.stringify({ sceneId, adjustment }, null, 2)
    try {
      await navigator.clipboard.writeText(json)
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

  return (
    <Section title="Lage anpassen">
      <p className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700 tabular-nums">
        {summary(adjustment)}
      </p>

      <StepField
        label="Ost (dx)"
        unit="m"
        value={adjustment.dx}
        displayFactor={1}
        digits={3}
        fine={0.05}
        coarse={0.5}
        onChange={(value) => setField('dx', value)}
      />
      <StepField
        label="Nord (dy)"
        unit="m"
        value={adjustment.dy}
        displayFactor={1}
        digits={3}
        fine={0.05}
        coarse={0.5}
        onChange={(value) => setField('dy', value)}
      />
      <StepField
        label="Drehung"
        unit="°"
        value={adjustment.rotationDeg}
        displayFactor={1}
        digits={2}
        fine={0.1}
        coarse={1}
        onChange={(value) => setField('rotationDeg', value)}
      />
      <StepField
        label="Maßstab"
        unit="%"
        value={adjustment.scale}
        displayFactor={100}
        digits={3}
        fine={0.001}
        coarse={0.01}
        onChange={(value) => setField('scale', value)}
      />

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          aria-pressed={moveMode}
          onClick={() => setMoveMode(!moveMode)}
          className={buttonClass(moveMode)}
        >
          Bild mit der Maus verschieben
        </button>
        <button type="button" onClick={() => resetAdjustment(sceneId)} className={buttonClass()}>
          Zurücksetzen
        </button>
        <button type="button" onClick={copyAdjustment} className={buttonClass()}>
          Anpassung kopieren
        </button>
      </div>
      <p
        aria-live="polite"
        className={cn('text-xs', copyState === 'failed' ? 'text-red-700' : 'text-slate-600')}
      >
        {copyMessage(copyState)}
      </p>
    </Section>
  )
}

function summary(adjustment: Adjustment): string {
  return [
    `dx ${adjustment.dx.toFixed(3)} m`,
    `dy ${adjustment.dy.toFixed(3)} m`,
    `Drehung ${adjustment.rotationDeg.toFixed(2)}°`,
    `Maßstab ${(adjustment.scale * 100).toFixed(3)} %`,
  ].join(' · ')
}

function copyMessage(state: 'idle' | 'copied' | 'failed'): string {
  if (state === 'copied') return 'In die Zwischenablage kopiert.'
  if (state === 'failed') return 'Kopieren fehlgeschlagen. Der Browser erlaubt den Zugriff nicht.'
  return ''
}

type StepFieldProps = {
  label: string
  unit: string
  /** Value in stored units (scale is a factor). */
  value: number
  /** Shown value = stored value * displayFactor. */
  displayFactor: number
  digits: number
  /** Small and large step in stored units. */
  fine: number
  coarse: number
  onChange: (next: number) => void
}

function StepField({
  label,
  unit,
  value,
  displayFactor,
  digits,
  fine,
  coarse,
  onChange,
}: StepFieldProps) {
  // While the user types, keep the raw text so "0." is not rewritten mid-input.
  const [draft, setDraft] = useState<string | null>(null)
  const shown = (value * displayFactor).toFixed(digits)
  const step = (delta: number) => onChange(round6(value + delta))
  const stepLabel = (delta: number) =>
    `${delta < 0 ? '−' : '+'}${round6(Math.abs(delta) * displayFactor)}`

  return (
    <div className="grid grid-cols-[4.5rem_1fr] items-center gap-2 text-sm">
      <span>{label}</span>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" onClick={() => step(-coarse)} className={stepButtonClass}>
          {stepLabel(-coarse)}
        </button>
        <button type="button" onClick={() => step(-fine)} className={stepButtonClass}>
          {stepLabel(-fine)}
        </button>
        <input
          type="number"
          step="any"
          aria-label={label}
          value={draft ?? shown}
          onChange={(event) => {
            const text = event.currentTarget.value
            setDraft(text)
            const parsed = Number(text)
            if (text.trim() !== '' && Number.isFinite(parsed))
              onChange(round6(parsed / displayFactor))
          }}
          onBlur={() => setDraft(null)}
          className="w-24 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-right tabular-nums"
        />
        <span className="w-3 text-slate-500">{unit}</span>
        <button type="button" onClick={() => step(fine)} className={stepButtonClass}>
          {stepLabel(fine)}
        </button>
        <button type="button" onClick={() => step(coarse)} className={stepButtonClass}>
          {stepLabel(coarse)}
        </button>
      </div>
    </div>
  )
}

const stepButtonClass =
  'rounded border border-slate-300 bg-white px-1.5 py-0.5 text-xs tabular-nums hover:bg-slate-100'
