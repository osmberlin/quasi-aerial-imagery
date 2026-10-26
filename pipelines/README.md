# Pipelines

One folder per step. Each folder's README says what goes in, what comes out and which options matter.

| Step | Folder | In → out |
|---|---|---|
| 1 | [1-ingest/](1-ingest/) | `.360` video or `.36P` photos → equirectangular (ERP) JPEG frames with GPS in the EXIF |
| 2 | [2-masks/](2-masks/) | frames → helmet mask, sky cut, per-frame masks for people and things riding along |
| 3 | [3-poses/](3-poses/) | frames + masks → COLMAP model with camera poses, scaled and placed by GPS |
| 4 | [4-ground/](4-ground/) | model + frames + masks → ground image (GeoTIFF), frames aligned and blended |
| 5 | [5-export/](5-export/) | ground image → OpenAerialMap COG + preview; viewer data |
| – | [evaluate/](evaluate/) | comparisons with references outside the pipeline (Berlin kerb survey, IGN orthophoto, any XYZ orthophoto) |
| – | [common/](common/) | `guarded_run.sh` (watchdog), `crs.py` (scene CRS) |

Two scripts chain the steps; both skip every step whose output already exists, so a failed run can
be restarted with the same command:

- [`run_scene.sh`](run_scene.sh) `<frames> <scene> <spatial|exhaustive>`: helmet mask (2), COLMAP with
  the sky cut (3), camera height calibration and a first ground image, nearest and median (4).
- [`scene_ground.sh`](scene_ground.sh) `<scene> <frames> <camera height> [out]`: the standard ground
  product (4): labels, vehicles and people excluded, co-registration, two-band blend, car layer, and in
  Berlin the kerb metrics (evaluate).

Both print their settings with `--help`. The whole procedure for a capture, with the settings that
worked: [docs/recipe.md](../docs/recipe.md).

## Setup

macOS on Apple Silicon (tested, 24 GB RAM) or Linux. Tools on `PATH`: `colmap` (4.x), `exiftool`,
`ffmpeg`/`ffprobe`, `sqlite3`, GDAL (`gdalwarp`, `gdal_translate`, `gdalbuildvrt`), and
[uv](https://docs.astral.sh/uv/). For video: the patched `max2sphere` ([1-ingest/max2sphere/](1-ingest/max2sphere/)).

```bash
brew install colmap exiftool ffmpeg gdal uv
uv venv --python 3.12 pipelines/.venv
uv pip install --python pipelines/.venv/bin/python -r pipelines/requirements.txt
```

The helmet-mask and segmentation environments are described in [2-masks/](2-masks/README.md#environments).
Each script finds its environment at a fixed place (`pipelines/.venv`, `2-masks/*/.venv`); `PYTHON`,
`SEGMENT_PYTHON` and `HELMET_MASK_DIR` override that.

## Where data goes

Frames, scenes and outputs never go into this repository (`.gitignore` here blocks images, rasters,
point clouds, weights and videos). Keep them next to it:

```
<workspace>/
├── quasi-aerial-imagery/   this repository
├── imagery/                frames and scene folders, one folder per capture
├── third-party/            max2sphere
└── secrets.env             optional: tile URL of a reference orthophoto (may contain a token)
```

Each scene writes only below its own folder; nothing is shared between scenes.

## The watchdog

Every heavy step runs under [`common/guarded_run.sh`](common/guarded_run.sh). It logs memory, swap
and disk every 5 s to `<log dir>/monitor.log`, keeps the Mac awake and kills the command (exit code 99)
when a limit is crossed: `MAX_RSS_MB`, `MIN_AVAIL_MB`, `MAX_SWAP_GROWTH_MB`, `MIN_FREE_GB`. Run one heavy
job at a time; a run without limits once froze the machine (R-083).

## Tests

```bash
pipelines/.venv/bin/python -m pytest -q pipelines/4-ground/tests pipelines/2-masks/segment/tests
cd pipelines/2-masks/helmet-mask && .venv/bin/python -m pytest -q tests
```
