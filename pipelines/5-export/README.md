# 5. Export: OpenAerialMap, viewer

| Script | What |
|---|---|
| [`export_oam.sh`](export_oam.sh) | a ground image (e.g. `ground_twoband.tif` from `scene_ground.sh`) → `<out>.cog.tif` (Cloud Optimized GeoTIFF, EPSG:3857, JPEG 90 with a transparency mask) and `<out>.preview.jpg` |
| [`export_viewer_data.py`](export_viewer_data.py) | GeoTIFFs of one or more scenes → WebP chunks and `manifest.json` for the [viewer](../../viewer/) |

## OpenAerialMap

```bash
pipelines/5-export/export_oam.sh <scene>/ground/ground_twoband.tif <exports>/obernai-nord
```

Before uploading, look through the image at full zoom for people and number plates. People are
masked by a segmentation model, not by hand, so body parts can remain at the edges of masks. Then
upload the `.cog.tif` at [upload.imagery.hotosm.org](https://upload.imagery.hotosm.org/). Suggested form
values for a street-level capture: platform "other" (bike helmet / selfie stick), sensor "GoPro MAX 2",
acquisition start and end from the capture, licence CC BY 4.0, and a description that says the ground
was assumed flat and that the position is GPS-level (a metre or a few). Examples: the two items linked
from the [README](../../README.md#results).

Big outputs: a 2 cm image of a 2 km route is about 32,000 x 35,000 px and the COG about 40 MB, but the
intermediate GeoTIFFs of `scene_ground.sh` need several GB. Put the output folder on external storage
if the disk is tight.

## Viewer

```bash
pipelines/.venv/bin/python pipelines/5-export/export_viewer_data.py --scenes <scenes.json>
cd viewer && bun run dev
```

The scene list format is in the script's docstring (`--help`). Output goes to `<workspace>/imagery/viewer-data/`,
which `viewer/public/data` links to; see [viewer/README.md](../../viewer/README.md).

## Frames for Panoramax and Mapillary

See [1-ingest](../1-ingest/README.md#frames-for-panoramax): frames every 2-3 m, anonymised with
[`../2-masks/anonymise/`](../2-masks/anonymise/).
