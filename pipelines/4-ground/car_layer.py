#!/usr/bin/env python3
"""Car layer from projected labels: footprints where (almost) every frame that sees a cell calls it a vehicle.

Projected onto the ground, a car's body lands outside its footprint in a different direction for
each camera, so the label vote is mostly "vehicle" only under the car (silhouette intersection on
the ground plane). Cells with a vehicle vote share of at least `--min-share` are kept, cleaned
morphologically, and components smaller than `--min-area` are dropped.

Writes `<prefix>.tif` (RGBA: colour from `--colour`, alpha = footprint), `<prefix>.geojson`
(footprint polygons, EPSG:4326) and `<prefix>.json` (counts and areas).

    car_layer.py LABELS.tif --colour NEAREST.tif --output PREFIX
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_geom


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("labels", type=Path, help="<out>.labels.tif from render_ground_ortho.py --labels")
    ap.add_argument("--colour", type=Path, required=True, help="ortho on the same grid to colour the cars")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--min-share", type=int, default=80, help="minimum vote share of the vehicle group, percent")
    ap.add_argument("--min-area", type=float, default=1.5, help="smallest footprint kept, m2")
    args = ap.parse_args()

    with rasterio.open(args.labels) as src:
        vote, share = src.read(2), src.read(3)
        groups = {int(k): v for k, v in json.loads(src.tags()["groups"]).items()}
        prof, gsd, crs, transform = src.profile, src.res[0], src.crs, src.transform
    vehicle = [k for k, v in groups.items() if v == "vehicle"][0]
    mask = ((vote == vehicle) & (share >= args.min_share)).astype(np.uint8)
    k = max(int(round(0.1 / gsd)), 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (4 * k + 1, 4 * k + 1)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] * gsd * gsd >= args.min_area
    mask = keep[lab].astype(np.uint8)
    areas = sorted((stats[1:, cv2.CC_STAT_AREA][keep[1:]] * gsd * gsd).round(2).tolist(), reverse=True)

    with rasterio.open(args.colour) as src:
        rgb = src.read([1, 2, 3])
    p = dict(prof, count=4, dtype="uint8", nodata=None, photometric="RGB")
    with rasterio.open(f"{args.output}.tif", "w", **p) as dst:
        dst.write(rgb * mask[None], indexes=[1, 2, 3])
        dst.write(mask * 255, 4)
    feats = []
    for geom, val in shapes(mask, mask=mask > 0, transform=transform):
        feats.append({"type": "Feature", "properties": {}, "geometry": transform_geom(crs, "EPSG:4326", geom)})
    Path(f"{args.output}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    info = {"min_share": args.min_share, "footprints": len(areas), "area_m2_total": round(sum(areas), 1),
            "area_m2_largest": areas[:10], "vehicle_vote_cells_m2": round(float((vote == vehicle).sum() * gsd * gsd), 1)}
    Path(f"{args.output}.json").write_text(json.dumps(info, indent=1))
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
