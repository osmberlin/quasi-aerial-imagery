import { mkdir, rm, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
/**
 * Writes a small fake dataset to public/data-sample/ so the viewer runs without the real export.
 * Tiles are plain gradients with a red bar on the top edge (north), so orientation and seams show.
 * The numbers and texts are invented for testing the interface.
 *
 * Usage: bun run sample-data
 */
import { deflateSync } from 'node:zlib'

// Mirrors the data contract in src/data/manifest.ts. Kept local so this script does not pull the
// browser code (and its DOM typings) into the scripts type-check.
type Corner = [number, number]
type Tile = { image: string; corners: [Corner, Corner, Corner, Corner] }
type ResultLayer = {
  id: string
  title: string
  metersPerPixel: number
  tiles: Tile[]
  report?: Record<string, unknown>
}
type LayerVerdict = { id: string; verdict: string; good: string[]; bad: string[] }
type Assessment = {
  date: string
  summary: string
  rating: number
  measured: boolean
  layers: LayerVerdict[]
  openQuestions: string[]
  resultIds: string[]
}
type Scene = {
  id: string
  title: string
  captured: string
  frames: number
  georeferenced: boolean
  origin: [number, number]
  extentMeters: { minX: number; minY: number; maxX: number; maxY: number }
  layers: ResultLayer[]
  assessment: Assessment | null
  notes?: string
}
type Manifest = { generated: string; scenes: Scene[] }

const OUT_DIR = fileURLToPath(new URL('../public/data-sample/', import.meta.url))
const TILE_PX = 512
/** Tiles per side; each scene is cut into GRID x GRID tiles per layer. */
const GRID = 2

type Rgb = [number, number, number]

type SampleLayer = { id: string; title: string; colour: Rgb }

type SampleScene = {
  id: string
  title: string
  captured: string
  frames: number
  georeferenced: boolean
  origin: [number, number]
  extentMeters: { minX: number; minY: number; maxX: number; maxY: number }
  layers: SampleLayer[]
  assessment: Assessment | null
  notes?: string
}

const SAMPLE_SCENES: SampleScene[] = [
  {
    id: 'beispiel-nord',
    title: 'Beispiel Nord (Dummy)',
    captured: '2026-04-03',
    frames: 120,
    georeferenced: true,
    origin: [13.4153, 52.4947],
    extentMeters: { minX: -60, minY: -40, maxX: 60, maxY: 40 },
    layers: [
      { id: 'nearest', title: 'Nächster Nachbar', colour: [70, 130, 180] },
      { id: 'median', title: 'Median', colour: [180, 140, 70] },
    ],
    assessment: {
      date: '2026-10-01',
      summary: 'Testdaten: Nächster Nachbar wirkt schärfer, Median hat weniger Artefakte.',
      rating: 3,
      measured: false,
      layers: [
        {
          id: 'nearest',
          verdict: 'Scharf, mit Kanten-Artefakten',
          good: ['Markierungen klar erkennbar'],
          bad: ['Schatten an Bordsteinen'],
        },
        {
          id: 'median',
          verdict: 'Ruhiger Eindruck',
          good: ['Keine Doppelbilder'],
          bad: ['Leichte Unschärfe'],
        },
      ],
      openQuestions: ['Stimmt die Breite der Fahrbahn?'],
      resultIds: ['R-001', 'R-002'],
    },
    notes: 'Testdaten zum Ausprobieren der Oberfläche. Keine echten Messungen.',
  },
  {
    id: 'beispiel-sued',
    title: 'Beispiel Süd (Dummy)',
    captured: '2026-04-05',
    frames: 84,
    georeferenced: false,
    origin: [13.4171, 52.4921],
    extentMeters: { minX: -45, minY: -30, maxX: 45, maxY: 30 },
    layers: [
      { id: 'nearest', title: 'Nächster Nachbar', colour: [80, 160, 110] },
      { id: 'splat', title: 'Splat', colour: [140, 90, 170] },
    ],
    assessment: {
      date: '2026-10-10',
      summary: 'Testdaten: Auflösung gut, Lage nur geschätzt.',
      rating: 4,
      measured: true,
      layers: [
        {
          id: 'nearest',
          verdict: 'Gleichmäßig scharf',
          good: ['Gleichmäßige Auflösung'],
          bad: [],
        },
        {
          id: 'splat',
          verdict: 'Weicher, Details verschwimmen',
          good: ['Keine Lücken'],
          bad: ['Fassaden unscharf'],
        },
      ],
      openQuestions: [],
      resultIds: ['R-010'],
    },
  },
]

function buildLayer(scene: SampleScene, layer: SampleLayer, layerIndex: number): ResultLayer {
  const { minX, minY, maxX, maxY } = scene.extentMeters
  const tileWidth = (maxX - minX) / GRID
  const tileHeight = (maxY - minY) / GRID
  const tiles: ResultLayer['tiles'] = []
  for (let row = 0; row < GRID; row++) {
    for (let col = 0; col < GRID; col++) {
      const left = minX + col * tileWidth
      const right = left + tileWidth
      const top = maxY - row * tileHeight
      const bottom = top - tileHeight
      tiles.push({
        image: `${scene.id}/${layer.id}/${row}_${col}.png`,
        corners: [
          [left, top],
          [right, top],
          [right, bottom],
          [left, bottom],
        ],
      })
    }
  }
  return {
    id: layer.id,
    title: layer.title,
    metersPerPixel: Math.round((tileWidth / TILE_PX) * 10000) / 10000,
    tiles,
    report: {
      frames: scene.frames,
      tiles: tiles.length,
      residualsM: { mean: 0.042 + layerIndex * 0.01, max: 0.19 + layerIndex * 0.05 },
      note: 'Testdaten',
    },
  }
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c >>> 0
})

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of bytes) crc = (CRC_TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type: string, data: Uint8Array): Uint8Array {
  const out = new Uint8Array(12 + data.length)
  const view = new DataView(out.buffer)
  view.setUint32(0, data.length)
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i)
  out.set(data, 8)
  view.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)))
  return out
}

/** Minimal 8-bit RGB PNG encoder; enough for generated test tiles. */
function encodePng(size: number, pixel: (x: number, y: number) => Rgb): Uint8Array {
  const raw = new Uint8Array(size * (1 + size * 3))
  for (let y = 0; y < size; y++) {
    const rowStart = y * (1 + size * 3)
    raw[rowStart] = 0 // filter type: none
    for (let x = 0; x < size; x++) {
      const [r, g, b] = pixel(x, y)
      const offset = rowStart + 1 + x * 3
      raw[offset] = r
      raw[offset + 1] = g
      raw[offset + 2] = b
    }
  }

  const header = new Uint8Array(13)
  const headerView = new DataView(header.buffer)
  headerView.setUint32(0, size)
  headerView.setUint32(4, size)
  header[8] = 8 // bit depth
  header[9] = 2 // colour type: RGB
  // compression, filter and interlace stay 0

  const signature = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])
  const parts = [
    signature,
    pngChunk('IHDR', header),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', new Uint8Array()),
  ]
  const total = parts.reduce((sum, part) => sum + part.length, 0)
  const png = new Uint8Array(total)
  let offset = 0
  for (const part of parts) {
    png.set(part, offset)
    offset += part.length
  }
  return png
}

function tilePixel(colour: Rgb, row: number, col: number): (x: number, y: number) => Rgb {
  const [r, g, b] = colour
  const clamp = (value: number) => Math.max(0, Math.min(255, Math.round(value)))
  return (x, y) => {
    if (y < 12) return [200, 30, 30]
    let shade = 0.55 + 0.45 * (x / TILE_PX) - 0.1 * (row + col) - 0.15 * (y / TILE_PX)
    if (x % 64 === 0 || y % 64 === 0) shade *= 0.7
    return [clamp(r * shade), clamp(g * shade), clamp(b * shade)]
  }
}

async function main(): Promise<void> {
  await rm(OUT_DIR, { recursive: true, force: true })

  const scenes: Scene[] = []
  for (const sampleScene of SAMPLE_SCENES) {
    const layers = sampleScene.layers.map((layer, layerIndex) =>
      buildLayer(sampleScene, layer, layerIndex),
    )
    for (const [layerIndex, layer] of layers.entries()) {
      const sampleLayer = sampleScene.layers[layerIndex]
      if (!sampleLayer) continue
      for (const [tileIndex, tile] of layer.tiles.entries()) {
        const row = Math.floor(tileIndex / GRID)
        const col = tileIndex % GRID
        const path = join(OUT_DIR, tile.image)
        await mkdir(dirname(path), { recursive: true })
        await writeFile(path, encodePng(TILE_PX, tilePixel(sampleLayer.colour, row, col)))
      }
    }
    scenes.push({
      id: sampleScene.id,
      title: sampleScene.title,
      captured: sampleScene.captured,
      frames: sampleScene.frames,
      georeferenced: sampleScene.georeferenced,
      origin: sampleScene.origin,
      extentMeters: sampleScene.extentMeters,
      layers,
      assessment: sampleScene.assessment,
      notes: sampleScene.notes,
    })
  }

  const manifest: Manifest = { generated: new Date().toISOString(), scenes }
  await writeFile(join(OUT_DIR, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(`Wrote ${scenes.length} sample scenes to ${OUT_DIR}`)
}

await main()
