import { z } from 'zod'

/** Data contract written by the export script. Local metres: x east, y north, relative to scene.origin. */

const PointSchema = z.tuple([z.number(), z.number()])

const TileSchema = z.object({
  /** Path relative to the data base URL, e.g. "beispiel-nord/nearest/0_1.png". */
  image: z.string().min(1),
  /** Top-left, top-right, bottom-right, bottom-left in local metres. */
  corners: z.tuple([PointSchema, PointSchema, PointSchema, PointSchema]),
})

const LayerSchema = z.object({
  id: z.string().min(1),
  title: z.string(),
  metersPerPixel: z.number().positive(),
  tiles: z.array(TileSchema),
  /** Raw numbers of the run, shown as a collapsible table. */
  report: z.record(z.string(), z.unknown()).optional(),
})

const LayerVerdictSchema = z.object({
  id: z.string(),
  verdict: z.string(),
  good: z.array(z.string()),
  bad: z.array(z.string()),
})

const AssessmentSchema = z.object({
  date: z.string(),
  summary: z.string(),
  /** 1 (unusable) to 5 (meets the goal). */
  rating: z.number().int().min(1).max(5),
  /** Has anything been checked against a real measurement? */
  measured: z.boolean(),
  layers: z.array(LayerVerdictSchema),
  openQuestions: z.array(z.string()),
  resultIds: z.array(z.string()),
})

const SceneSchema = z.object({
  id: z.string().min(1),
  title: z.string(),
  captured: z.string(),
  frames: z.number().int().nonnegative(),
  /** false means position and scale on the map are only a guess. */
  georeferenced: z.boolean(),
  /** [lng, lat] of the scene's local frame origin. */
  origin: PointSchema,
  extentMeters: z.object({
    minX: z.number(),
    minY: z.number(),
    maxX: z.number(),
    maxY: z.number(),
  }),
  layers: z.array(LayerSchema),
  assessment: AssessmentSchema.nullable(),
  notes: z.string().optional(),
  /** Points of interest from the exporter, e.g. where the owner measured with a tape. */
  markers: z.array(z.object({ position: PointSchema, label: z.string() })).optional(),
  /** Manual placement baked into the tiles by the exporter (docs/placements/<id>.json). */
  placement: z
    .object({ version: z.string(), date: z.string(), by: z.string(), reference: z.string() })
    .optional(),
})

export const ManifestSchema = z.object({
  generated: z.string(),
  scenes: z.array(SceneSchema),
})

export type Manifest = z.infer<typeof ManifestSchema>
export type Scene = Manifest['scenes'][number]
export type ResultLayer = Scene['layers'][number]
export type Assessment = NonNullable<Scene['assessment']>

// Relative to the deployment base, so the app also works under a sub-path such as GitHub Pages.
const BASE = import.meta.env.BASE_URL
const DATA_MANIFEST_URL = `${BASE}data/manifest.json`
const SAMPLE_MANIFEST_URL = `${BASE}data-sample/manifest.json`

export type LoadedManifest = {
  manifest: Manifest
  /** True when the fake sample data is shown instead of the real export. */
  isSample: boolean
  /** Prefix for tile image paths, with a trailing slash. */
  baseUrl: string
}

/**
 * Returns the parsed JSON, or null when the file is missing. The Vite dev server answers unknown
 * paths with index.html, so a missing file only counts when the response is JSON.
 */
async function fetchManifestJson(url: string): Promise<unknown> {
  const response = await fetch(url)
  const isJson = response.ok && (response.headers.get('content-type') ?? '').includes('json')
  if (!isJson) return null
  return response.json()
}

function parseManifest(json: unknown): Manifest {
  const result = ManifestSchema.safeParse(json)
  if (!result.success) {
    const first = result.error.issues[0]
    const where = first ? first.path.join('.') : '?'
    throw new Error(`Manifest ungültig (${where}): ${first?.message ?? 'unbekannter Fehler'}`)
  }
  return result.data
}

/** Loads the real export, falling back to the bundled sample data. */
export async function loadManifest(): Promise<LoadedManifest> {
  const data = await fetchManifestJson(DATA_MANIFEST_URL)
  if (data !== null) {
    return { manifest: parseManifest(data), isSample: false, baseUrl: `${BASE}data/` }
  }
  const sample = await fetchManifestJson(SAMPLE_MANIFEST_URL)
  if (sample !== null) {
    return { manifest: parseManifest(sample), isSample: true, baseUrl: `${BASE}data-sample/` }
  }
  throw new Error(
    `Kein Manifest gefunden: ${DATA_MANIFEST_URL} fehlt, ${SAMPLE_MANIFEST_URL} ebenfalls.`,
  )
}

/**
 * Key for the locally stored adjustment of a scene. A new baked placement gets a new key, so an
 * adjustment made against the previous placement is not applied on top of the new one.
 */
export function adjustmentKey(scene: Scene): string {
  return scene.placement ? `${scene.id}@${scene.placement.version}` : scene.id
}
