import { BACKGROUNDS, type BackgroundId } from '../map/backgrounds'
import { useViewActions } from '../state/view-store'
import { Section } from './ui'

type BackgroundPickerProps = {
  activeId: BackgroundId
}

export function BackgroundPicker({ activeId }: BackgroundPickerProps) {
  const { setBackground } = useViewActions()
  return (
    <Section title="Hintergrund">
      <fieldset className="flex flex-col gap-1.5 text-sm">
        <legend className="sr-only">Hintergrundkarte</legend>
        {BACKGROUNDS.map((background) => (
          <label key={background.id} className="flex items-center gap-2">
            <input
              type="radio"
              name="background"
              value={background.id}
              checked={background.id === activeId}
              onChange={() => setBackground(background.id)}
            />
            {background.label}
          </label>
        ))}
      </fieldset>
    </Section>
  )
}
