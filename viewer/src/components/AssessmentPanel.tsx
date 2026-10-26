import type { Assessment, ResultLayer, Scene } from '../data/manifest'
import { ReportTable } from './ReportTable'
import { Badge, cn, Section } from './ui'

type AssessmentPanelProps = {
  scene: Scene
  activeLayer: ResultLayer | undefined
}

export function AssessmentPanel({ scene, activeLayer }: AssessmentPanelProps) {
  const assessment = scene.assessment
  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">Bewertung</h2>
        <p className="text-sm text-slate-600">{scene.title}</p>
      </header>

      {scene.notes && (
        <p className="rounded-md bg-amber-50 p-2 text-sm text-amber-900">{scene.notes}</p>
      )}

      {assessment ? (
        <AssessmentBody assessment={assessment} scene={scene} activeLayerId={activeLayer?.id} />
      ) : (
        <p className="text-sm text-slate-500">Keine Bewertung für diese Szene.</p>
      )}

      {activeLayer && (
        <details className="rounded-md border border-slate-200 p-2 text-sm">
          <summary className="cursor-pointer font-medium">Rohdaten: {activeLayer.title}</summary>
          <div className="mt-2">
            {activeLayer.report ? (
              <ReportTable data={activeLayer.report} />
            ) : (
              <p className="text-slate-500">Kein Bericht für diese Ebene.</p>
            )}
          </div>
        </details>
      )}
    </div>
  )
}

type AssessmentBodyProps = {
  assessment: Assessment
  scene: Scene
  activeLayerId: string | undefined
}

function AssessmentBody({ assessment, scene, activeLayerId }: AssessmentBodyProps) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <Rating value={assessment.rating} />
        <Badge tone={assessment.measured ? 'good' : 'bad'}>
          gegen Messung geprüft: {assessment.measured ? 'ja' : 'nein'}
        </Badge>
      </div>
      <p className="text-xs text-slate-500">Bewertet am {assessment.date}</p>
      <p className="text-sm">{assessment.summary}</p>

      <Section title="Ebenen">
        <ul className="flex flex-col gap-2">
          {assessment.layers.map((entry) => {
            const active = entry.id === activeLayerId
            const title = scene.layers.find((layer) => layer.id === entry.id)?.title ?? entry.id
            return (
              <li
                key={entry.id}
                className={cn(
                  'flex flex-col gap-1 rounded-md border p-2 text-sm',
                  active ? 'border-sky-500 bg-sky-50' : 'border-slate-200',
                )}
              >
                <p className="font-medium">
                  {title} {active && <Badge tone="info">aktiv</Badge>}
                </p>
                <p>{entry.verdict}</p>
                {entry.good.map((item) => (
                  <p key={item} className="text-emerald-800">
                    ✓ {item}
                  </p>
                ))}
                {entry.bad.map((item) => (
                  <p key={item} className="text-red-800">
                    ✗ {item}
                  </p>
                ))}
              </li>
            )
          })}
        </ul>
      </Section>

      <Section title="Offene Fragen">
        {assessment.openQuestions.length > 0 ? (
          <ul className="list-inside list-disc text-sm">
            {assessment.openQuestions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">Keine.</p>
        )}
      </Section>

      <Section title="Ergebnis-IDs">
        <div className="flex flex-wrap gap-1.5">
          {assessment.resultIds.map((id) => (
            <span key={id} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
              {id}
            </span>
          ))}
        </div>
      </Section>
    </>
  )
}

function Rating({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-1" role="img" aria-label={`Bewertung ${value} von 5`}>
      {[1, 2, 3, 4, 5].map((step) => (
        <span
          key={step}
          className={cn('size-3 rounded-full', step <= value ? 'bg-slate-900' : 'bg-slate-200')}
        />
      ))}
      <span className="ml-1 text-sm tabular-nums">{value} / 5</span>
    </span>
  )
}
