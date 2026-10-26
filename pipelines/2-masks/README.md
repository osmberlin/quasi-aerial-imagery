# 2. Masks: helmet, sky, people and things riding along

Everything in a frame that is not the world has to be masked: the helmet and the camera mount, the
person holding the selfie stick, a passenger on the rear seat, bags and straps. The sky is masked too,
but only for COLMAP. Why each mask is set the way it is: [docs/learnings.md#masks](../../docs/learnings.md#masks).

| Mask | Tool | Used by | Made by |
|---|---|---|---|
| Static helmet/rig mask, one per scene | [`helmet-mask/`](helmet-mask/) (`--method texture`, D-108) | COLMAP and the renderer | `run_scene.sh` step 1 |
| Sky above 16° | inline in [`../3-poses/colmap_poses.sh`](../3-poses/colmap_poses.sh) (`SKY_ELEV`, D-116) | COLMAP only | `run_scene.sh` step 2 |
| Semantic labels per frame (Mapillary Vistas) | [`segment/segment_frames.py`](segment/segment_frames.py) | the masks below; vehicles and people excluded from the ground image | `scene_ground.sh` step 1, or by hand |
| People per frame | [`segment/person_mask.py`](segment/person_mask.py) (D-117) | renderer (`RENDER_MASK_DIR`) | by hand |
| Rig zone per frame (passenger, bags, straps just above the helmet) | [`segment/rig_zone_mask.py`](segment/rig_zone_mask.py) | renderer (`RENDER_MASK_DIR`) | by hand |
| Blurred faces, people and plates | [`anonymise/`](anonymise/) | frames you publish (Panoramax) | by hand |

Per-frame masks go to the renderer only. Per-frame masks for COLMAP were tried and made the
reconstruction collapse ([failed explorations](../../docs/failed-explorations.md)).

## Environments

```bash
uv venv --python 3.12 pipelines/2-masks/helmet-mask/.venv
uv pip install --python pipelines/2-masks/helmet-mask/.venv/bin/python -e "pipelines/2-masks/helmet-mask[dev]"
uv venv --python 3.12 pipelines/2-masks/segment/.venv
uv pip install --python pipelines/2-masks/segment/.venv/bin/python -r pipelines/2-masks/segment/requirements.txt
# only if you publish frames:
uv venv --python 3.12 pipelines/2-masks/anonymise/.venv
uv pip install --python pipelines/2-masks/anonymise/.venv/bin/python -r pipelines/2-masks/anonymise/requirements.txt
```

Segmentation runs Mask2Former (Swin-L, Mapillary Vistas) on Apple Silicon (`--device mps`) or CUDA.

## Typical use on a bike capture with a passenger

```bash
SEG=pipelines/2-masks/segment/.venv/bin/python
# labels at the default pitch (35° down); for a selfie stick add a second run with --pitch 70 --yaws 0 90 180 270
$SEG pipelines/2-masks/segment/segment_frames.py <frames> <scene>/labels
$SEG pipelines/2-masks/segment/rig_zone_mask.py --labels <scene>/labels --static-mask <scene>/colmap/helmet_mask.png \
  --frames <frames> --out <scene>/masks-rig
$SEG pipelines/2-masks/segment/person_mask.py --labels <scene>/labels --frames <frames> --out <scene>/masks-person \
  --static-mask <scene>/colmap/helmet_mask.png --preview 6
```

Hand the label folder to `scene_ground.sh` as `LABELS=` (it then skips its own segmentation) and the
mask folder as `RENDER_MASK_DIR=`. Look at the previews before rendering. Each script's `--help` lists
all options.

## Tests

```bash
pipelines/.venv/bin/python -m pytest -q pipelines/2-masks/segment/tests
cd pipelines/2-masks/helmet-mask && .venv/bin/python -m pytest -q tests
```

The helmet-mask regression cases need reference outputs made from real frames
([`helmet-mask/test-runs/`](helmet-mask/test-runs/README.md)); without them they are skipped.
