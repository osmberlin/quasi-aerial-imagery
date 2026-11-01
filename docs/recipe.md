# Recipe: from a 360° capture to a quasi-aerial image

This is the "use it yourself" part. It has two halves: the capture, which is your job, and the
processing, which you can run by hand with the commands below or hand to a coding agent (we used
Claude Opus 5.5 at medium effort; give it this file and the folder of your capture). Why the settings
are what they are: [learnings.md](learnings.md).

## 1. Capture

- **Camera:** GoPro MAX or MAX 2, 360° **time-lapse video**, one frame every 0.5 s (on foot, one frame
  per second was enough). A photo series every 2 s works less well: the frames are 8–12 m apart on a bike.
- **Mount:** on a bike helmet, or on a selfie stick held steady at the same height. Keep the camera
  position as constant as possible.
- **Measure the lens height** above the ground once with a folding ruler. The software estimates it,
  but a reference value is better (Obernai: 1.90 m on the helmet; Mont Sainte-Odile: 2.01 m on the stick).
- **Nothing that moves with the camera:** no flapping straps, nothing on the helmet mount. A passenger on
  the rear seat, bags and the person under the selfie stick can be masked, but each one costs image.
- **GPS:** wait for the fix before you start, and ride a few metres before the part you care about. On the MAX 2 it took a while, and samples before the
  first 3D fix carry wrong positions.
- **Passes:** ride or walk important sections there and back if you can; a single pass works too.
  Stretches that are wide, plain and open (new smooth paths, open sky, nothing near) are the hardest:
  ride them slowly and evenly, and twice where they matter (R-215).
- **Reference widths:** if you want to measure, measure one or two widths on site at clearly
  recognisable spots and note where. A longer stop is easy to find later as a cluster of GPS points.

## 2. Processing

Setup once: [../pipelines/README.md#setup](../pipelines/README.md#setup). Then, for a capture in
`<workspace>/imagery/<capture>/` (paths below are relative to the repository):

```bash
CAP=../imagery/2026-10-23-obernai
# frames with GPS from the video (1-ingest)
MAX_RSS_MB=8000 MIN_FREE_GB=12 pipelines/common/guarded_run.sh $CAP/ingest/guard_log \
  pipelines/.venv/bin/python pipelines/1-ingest/ingest_360_video.py $CAP/GS010205.360 $CAP/ingest --spacing 1.5
# helmet mask, sky cut, COLMAP poses, camera height, first ground image (2-masks, 3-poses, 4-ground)
GPS_STD=1 CAMERA_HEIGHT=1.90 pipelines/run_scene.sh $CAP/ingest/frames $CAP/scene spatial
```

Check the COLMAP result before going on ([3-poses/README.md#checking-the-result](../pipelines/3-poses/README.md#checking-the-result)):
one model with nearly all frames, a GPS residual of a few metres or less, a calibrated camera height near
the measured one. Open `$CAP/scene/ortho/ground_nearest.tif` in QGIS or export it to the viewer.

If something rides along with the camera or a person holds the stick, make per-frame masks for the
renderer ([2-masks/README.md](../pipelines/2-masks/README.md#typical-use-on-a-bike-capture-with-a-passenger)).
Then the standard ground product at 2 cm (4-ground):

```bash
LABELS=$CAP/scene/labels RENDER_MASK_DIR=$CAP/scene/masks-rig MAX_RSS_MB=7000 MIN_FREE_GB=5 \
  RENDER_ARGS="--strip 10 --max-range 12 --wall-range 6" \
  pipelines/scene_ground.sh $CAP/scene $CAP/ingest/frames 1.90 $CAP/scene/ground
```

For a walk with a selfie stick use `RENDER_ARGS="--strip 15 --max-range 15 --wall-range 6"` and masks
from `person_mask.py`.

What it took on a 24 GB Apple Silicon Mac for 957 frames over 2.17 km (R-214): COLMAP features 17 min,
matching 28 min, mapper about 70 min; the ground product 4 h 40 min. A section of 150 frames takes a
fraction of that. Run one heavy job at a time; every step runs under the watchdog.

## 3. Look, measure, export

- **Viewer:** `pipelines/5-export/export_viewer_data.py --scenes <scenes.json>`, then `cd viewer && bun run dev`.
  Switch layers, fade over a background map, measure distances with two clicks ([viewer/README.md](../viewer/README.md)).
- **Check the widths** you measured on site against the image. In Obernai the image gave 3.82 m where
  the phone measure app gave 3.95 m and 3.98 m (3–4 % short).
- **OpenAerialMap:** `pipelines/5-export/export_oam.sh $CAP/scene/ground/ground_twoband.tif <exports>/<name>`,
  look through the image at full zoom for people and number plates, then upload the `.cog.tif`
  ([5-export/README.md](../pipelines/5-export/README.md#openaerialmap)). Once on OpenAerialMap, editors can
  use it as a background, e.g. MAID with `bg=oam:<item id>`.
- **Panoramax:** frames every 2–3 m from the same video, anonymised first
  ([1-ingest/README.md](../pipelines/1-ingest/README.md#frames-for-panoramax)). Mapillary takes the
  `.360` video directly with its uploader.

## When it goes wrong

| Symptom | Likely cause | What to do |
|---|---|---|
| Several COLMAP models, or "no initial pair" | sky not masked, or a uniform stretch | keep `SKY_ELEV=16`; split the capture into sections and skip the uniform part |
| A long flat route curls up in height | free altitude prior | `FLAT_PRIOR=1 GPS_STD_Z=5` |
| A render tile is killed by the watchdog | tile area too large | smaller `TILE_M` |
| Walls and hedges stretch into rays | non-ground pixels used far from the camera | `--wall-range 6` (needs labels) |
| Blurred ground | median over frames far apart | use the nearest or two-band product; denser frames |
| Holes where cars were | vehicles are excluded on purpose | the car layer `cars.tif` shows the footprints |
