#!/usr/bin/env python3
"""Draw reference vector lines (e.g. Straßenbefahrung kerbs and markings; GeoJSON in any CRS, reprojected to the grid CRS) into
a transparent RGBA GeoTIFF on the grid of an existing ortho, so the viewer can overlay them.

    reference_raster.py GRID.tif OUT.tif bd_bordstein.geojson=#ff00ff be_fahrbahnmarkierunglinie.geojson=#00a0ff ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.crs import load_geojson_in_crs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("grid")
    ap.add_argument("output")
    ap.add_argument("layers", nargs="+", metavar="GEOJSON=#RRGGBB")
    ap.add_argument("--width-m", type=float, default=0.06, help="line width in metres")
    args = ap.parse_args()
    with rasterio.open(args.grid) as src:
        prof, t, g, (h, w), map_crs = src.profile, src.transform, src.res[0], src.shape, src.crs
    img = np.zeros((h, w, 4), np.uint8)
    lw = max(int(round(args.width_m / g)), 1)
    for item in args.layers:
        path, colour = item.rsplit("=", 1)
        rgb = tuple(int(colour.lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
        for ft in load_geojson_in_crs(path, map_crs)["features"]:
            geo = ft["geometry"]
            c = geo["coordinates"]
            parts = {"LineString": [c], "MultiLineString": c, "Polygon": c,
                     "MultiPolygon": [r for poly in c for r in poly]}.get(geo["type"], [])
            for p in parts:
                p = np.asarray(p, float)[:, :2]
                px = np.column_stack([(p[:, 0] - t.c) / g, (t.f - p[:, 1]) / g]).round().astype(np.int32)
                cv2.polylines(img, [px], False, (*rgb, 255), lw, cv2.LINE_AA)
    prof.update(count=4, dtype="uint8", nodata=None, photometric="RGB", compress="deflate")
    with rasterio.open(args.output, "w", **prof) as dst:
        dst.write(np.moveaxis(img, 2, 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
