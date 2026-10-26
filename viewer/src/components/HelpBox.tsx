import { Section } from './ui'

const SHORTCUTS: [keys: string, action: string][] = [
  ['1 … 9', 'Ergebnis-Ebene wählen'],
  ['← / →', 'vorige / nächste Ebene'],
  ['[ / ]', 'vorige / nächste Szene'],
  ['Leertaste halten', 'Ergebnis ausblenden (Vergleich mit dem Hintergrund)'],
  ['Umschalt + Pfeil', 'Bild um 0,05 m verschieben'],
  ['Alt + Umschalt + Pfeil', 'Bild um 0,5 m verschieben'],
  ['Esc', 'angefangene Messung abbrechen'],
]

export function HelpBox() {
  return (
    <details className="rounded-md border border-slate-200 bg-slate-50 p-2 text-sm">
      <summary className="cursor-pointer font-medium">Tastenkürzel und Messung</summary>
      <div className="mt-2 flex flex-col gap-2">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          {SHORTCUTS.map(([keys, action]) => (
            <div key={keys} className="contents">
              <dt className="font-mono text-xs whitespace-nowrap">{keys}</dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
        <Section title="Messen">
          <p>
            Zwei Punkte auf der Karte anklicken. Die Strecke steht auf der Linie; alle Messungen
            stehen in der rechten Leiste und lassen sich kopieren.
          </p>
        </Section>
        <p className="text-xs text-slate-600">
          Kürzel sind aus, solange der Cursor in einem Eingabefeld steht.
        </p>
      </div>
    </details>
  )
}
