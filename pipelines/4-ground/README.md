# 4. Ground: project, align and blend

The ground image is not rasterised from a 3D model. For every output cell (2 cm by default) the
renderer looks up the pixel that sees it in the full-resolution source frames, assuming the ground
lies one camera height below the cameras. Then frames are aligned to each other and blended.
Why it is done this way: [docs/learnings.md#ground-rendering](../../docs/learnings.md#ground-rendering);
what did not work: [docs/failed-explorations.md](../../docs/failed-explorations.md).

| Script | What |
|---|---|
| [`render_ground_ortho.py`](render_ground_ortho.py) | the renderer: COLMAP model + frames + masks → RGBA GeoTIFF and a `.json` report. Blends `nearest` (sharp), `median` (clean, a little blurred), `twoband` (nearest detail on a smooth base, softer seams). Optional side files: `.labels.tif`, `.source.tif` (which frame), `.disagreement.tif` |
| [`calibrate_camera_height.py`](calibrate_camera_height.py) | camera height from the images: the height at which neighbouring frames show the same paving at the same place |
| [`coregister_frames.py`](coregister_frames.py) | per-frame ground offsets from optical flow between overlapping frames (`--export-patches` of the renderer in, `--frame-offsets` out) |
| [`merge_tiles.py`](merge_tiles.py) | merges tiles rendered with `--bbox` (and their side files); streams through `gdalbuildvrt` for large mosaics |
| [`car_layer.py`](car_layer.py) | car footprints where almost every frame labels a cell as vehicle |

## The standard ground product: `scene_ground.sh`

[`../scene_ground.sh`](../scene_ground.sh) chains these after `run_scene.sh` (D-099):

1. semantic labels per frame (skipped with `LABELS=<existing folder>`),
2. a base mosaic with labels,
3. co-registration of the frames on the ground with vehicles and people excluded (`ROUNDS`, 2),
4. the ground without cars: `ground_nearest.tif` and `ground_twoband.tif` (the deliverable),
5. the car layer `cars.tif`,
6. in Berlin: kerb and marking metrics and a registration against the Straßenbefahrung ([`../evaluate/`](../evaluate/)).

```bash
LABELS=<labels> RENDER_MASK_DIR=<per-frame masks> TILES=40 \
  RENDER_ARGS="--strip 10 --max-range 12 --wall-range 6" \
  pipelines/scene_ground.sh <scene> <frames> <camera height in m> <out>
```

Renders run in `TILES` strips along the longer side of the scene and are merged. Memory grows with the
tile area, not with the number of frames: on a 630 x 690 m route, 16 tiles peaked at 8 GB, 40 tiles
stayed under 7 GB (R-214). Each tile runs under the watchdog with `MAX_RSS_MB` (3500 by default).

Useful renderer options (pass them through `RENDER_ARGS`):

- `--strip M` — how far beside the track to render (default 4 m, `--max-range` 6 m); with `--max-range` larger than the
  strip the nearest frame still wins near the track, so the centre stays sharp.
- `--wall-range M` (needs labels) — pixels not labelled as ground are used only within M metres, so
  walls and hedges do not stretch into rays (walked captures: `--max-range 15 --wall-range 6`, D-120).
- `--gsd` — pixel size; `scene_ground.sh` reads `GSD` (default 0.02).
- `--crs` — scene CRS; default is the UTM zone at the median GPS position (`ORTHO_CRS` also works).

`render_ground_ortho.py --help` lists every option; several (`--ground-dem`, `--ground-model`,
`--line-tiles`, `--raise-groups`, `--object-regions`) belong to experiments that are documented in the
logs but are not part of the standard product.

## Tests

```bash
pipelines/.venv/bin/python -m pytest -q pipelines/4-ground/tests
```
