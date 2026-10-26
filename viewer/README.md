# Orthophoto viewer

Local web app to inspect orthophoto results on a map: scenes, result layers (blended with opacity),
a georeferencing adjustment per scene, a distance measure, and the assessment of each run.

## Run

```bash
cd viewer
bun install
bun run dev        # http://localhost:5199
```

Without real data the app shows the bundled sample scenes and a "Beispieldaten" badge. Generate them
again with `bun run sample-data` (writes `public/data-sample/`).

Other scripts:

| Script                            | What it does                                                                         |
| --------------------------------- | ------------------------------------------------------------------------------------ |
| `bun run build`                   | Production build into `dist/`. The build does not copy `public/`, so it has no data. |
| `bun run type-check`              | `tsc` for the app and for the scripts.                                               |
| `bun run lint`                    | oxlint with type-aware rules.                                                        |
| `bun run format` / `format-check` | oxfmt, write or check.                                                               |
| `bun run test`                    | Vitest unit tests (geometry, URL state).                                             |
| `bun run check`                   | type-check, lint, format-check and tests in parallel.                                |

## Real data

Export scene results with [`pipelines/5-export/export_viewer_data.py`](../pipelines/5-export/README.md#viewer)
and link the export folder to `public/data`:

```bash
ln -s /absolute/path/to/export public/data
```

The app loads `/data/manifest.json` and falls back to `/data-sample/manifest.json` when it is missing.
`public/data` is git-ignored.

## Data contract

The export writes `manifest.json` next to its tile images. The schema is in
`src/data/manifest.ts` (Zod). In short:

- `scenes[]`: `id`, `title`, `captured`, `frames`, `georeferenced`, `origin` `[lng, lat]`,
  `extentMeters` `{minX, minY, maxX, maxY}`, `layers[]`, `assessment` (or `null`), `notes?`.
- `layers[]`: `id`, `title`, `metersPerPixel`, `tiles[]`, `report?` (any JSON object, shown as a table).
- `tiles[]`: `image` (path relative to the data folder, e.g. `beispiel-nord/nearest/0_1.png`) and
  `corners` in local metres, order top-left, top-right, bottom-right, bottom-left.
- Local metres are x east and y north from `origin`. They are converted with the WGS84 series at the
  origin latitude (`src/geo/coordinates.ts`).

## Environment variables

Set them in `.env.local` (git-ignored). Both are optional. An empty or missing value hides the option.

- `VITE_TILDA_2026_URL`: XYZ template for the "Luftbild 2026" background (512 px tiles).
- `VITE_TILDA_2025_URL`: XYZ template for the "Luftbild 2025" background.

These URLs contain a token. They are read at build time and must not be logged or committed.

## Shortcuts

| Key                     | Action                                            |
| ----------------------- | ------------------------------------------------- |
| `1` … `9`               | select result layer                               |
| `←` / `→`               | previous / next layer                             |
| `[` / `]`               | previous / next scene                             |
| `Space` (hold)          | hide result layers to compare with the background |
| `Shift` + arrow         | move image by 0.05 m                              |
| `Alt` + `Shift` + arrow | move image by 0.5 m                               |
| `Esc`                   | cancel a measurement in progress                  |

Shortcuts are off while a text or number field has focus.

## Adjustment

Per scene, stored in the browser (localStorage) and copied with "Anpassung kopieren" as JSON:

```json
{ "sceneId": "...", "adjustment": { "dx": 0, "dy": 0, "rotationDeg": 0, "scale": 1 } }
```

- `dx` east and `dy` north in metres, applied after rotation and scale.
- `rotationDeg` counter-clockwise, about the centre of `extentMeters`.
- `scale` is a factor (1 = unchanged). The panel shows it in percent.

## Notes

- The URL holds the selected scene and layer (`?scene=…&layer=…`).
- Raster tiles switch to nearest-neighbour sampling once one image pixel covers more than one screen pixel.
- The map zooms up to level 24. Raster backgrounds are overzoomed beyond their maximum zoom of 19.

## Measuring

Click two points on the map. The line stays on top of the result images and carries its length
as a label ("3,82 m", centimetres). Finished measurements are listed in the right sidebar per
scene (localStorage `streetlevel-ortho-viewer:measurements`) with delete buttons and a read-only
text block; "Kopieren" copies it. One line per measurement, dot decimals:

```
# Messungen elsass-messstelle 2026-10-25
scene=elsass-messstelle layer=nearest a=7.4894745,48.4581967 b=7.4895121,48.4581964 d=2.769 m zoom=21.0
```

Scene markers (red rings with their label, e.g. the tape-measure spots) are HTML markers above the
image tiles; "Messstellen anzeigen" in the left sidebar switches them off.
