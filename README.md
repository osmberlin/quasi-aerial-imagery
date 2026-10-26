# Quasi-aerial imagery from 360° street-level captures

Turn a 360° capture along a route (a GoPro MAX or MAX 2 on a bike helmet or a selfie stick) into a
top-down image of the ground that looks and works like an aerial image: georeferenced, about 2 cm per
pixel, good enough to measure path widths, and with no problem under trees or roofs, because the camera
is below them.

It is a prototype that works on a laptop, not a finished tool. Background, results and the story behind
it: **[blog post on osm-verkehrswende.org](https://www.osm-verkehrswende.org/quasi-aerial-imagery/posts/2026-10-25-quasi-aerial-imagery-from-360-photos/)**.

## Results

| Capture | What | On OpenAerialMap | In an editor |
|---|---|---|---|
| Obernai (Alsace), bike path north of the bridge, 2026-10-23 | 143 frames from the helmet at 1.90 m, about 80 x 146 m at about 2 cm | [item ef24d727](https://api.imagery.hotosm.org/browser/stac/collections/openaerialmap/items/ef24d727-3830-43be-8e87-ebe887d60e93?.language=de&.asset=asset-original) | [MAID](https://osm-editor-kit.github.io/maid/maid/?lens=none&photo_type=flat,panoramic&bg=oam:ef24d727-3830-43be-8e87-ebe887d60e93&overlays=none&map=19.87/48.459805/7.491292) |
| Mont Sainte-Odile (Alsace), walk on the convent terraces, 2026-10-24 | 591 frames from a selfie stick at 2.01 m, slopes and stairs, 3 cm | [item 1e7cb303](https://api.imagery.hotosm.org/browser/stac/collections/openaerialmap/items/1e7cb303-0c37-4904-81bf-374640b500af?.language=de) | [MAID](https://osm-editor-kit.github.io/maid/maid/?lens=none&photo_type=flat,panoramic&bg=oam:1e7cb303-0c37-4904-81bf-374640b500af&overlays=none&map=19/48.43799/7.40507) |

How good it is: in Obernai a path measured 3.82 m in the image and 3.95 m and 3.98 m on site (phone
measure app), 3–4 % short (R-191, R-214). Relative measurements are good; the absolute position is GPS
level, a metre or a few, not survey grade. Details: [docs/learnings.md#accuracy](docs/learnings.md#accuracy).

## How it works

1. **Ingest** the 360° video into frames every 1–1.5 m with GPS from the camera's telemetry.
2. **Mask the helmet** (found from what stays the same in every frame), **the sky** (textured clouds
   break the reconstruction) and **people** and things riding along (a segmentation model).
3. **Reconstruct the camera positions with [COLMAP](https://colmap.github.io/)** on the full 360°
   frame, with the GPS track as a prior for scale and position.
4. **Project, align and blend:** every ground cell takes its pixel from the full-resolution frames,
   assuming flat ground one camera height below the camera; frames are aligned to each other by optical
   flow and blended with soft seams; cars and people are left out.
5. **Export** a Cloud Optimized GeoTIFF for [HOT OpenAerialMap](https://imagery.hotosm.org/).

OpenDroneMap, which earlier attempts used, did not work for 360° street-level images; this and other
dead ends are in [docs/failed-explorations.md](docs/failed-explorations.md).

## Use it yourself

**[docs/recipe.md](docs/recipe.md)**: capture checklist, the commands for one capture end to end,
and what to do when it goes wrong. Setup and the step folders: [pipelines/README.md](pipelines/README.md).

## Repository

| Path | What |
|---|---|
| [pipelines/](pipelines/) | the steps as folders `1-ingest` … `5-export`, plus `evaluate` and `common`; `run_scene.sh` and `scene_ground.sh` chain them |
| [viewer/](viewer/) | local MapLibre app to look at results, fade them over a background map and measure |
| [docs/recipe.md](docs/recipe.md) | how to capture and process |
| [docs/learnings.md](docs/learnings.md) | what we know today and why the settings are what they are |
| [docs/failed-explorations.md](docs/failed-explorations.md) | dead ends worth knowing |
| [docs/log/](docs/log/) | every decision (`D-xxx`) and every measured result (`R-xxx`), cleaned for publication |
| [AGENTS.md](AGENTS.md) | working rules for people and coding agents |

## Data and privacy

No frames, reconstructions, model weights or videos are in this repository, and none are published.
The published images were checked for people and number plates before upload; if you still find
something identifiable, please open an issue and it will be taken down.

## Licence

Code: [MIT](LICENSE). Documentation and result images: [CC BY 4.0](LICENSE-DATA.md).
