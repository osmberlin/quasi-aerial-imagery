#!/usr/bin/env python3
"""Download the French IGN orthophoto (Géoplateforme WMS, ORTHOIMAGERY.ORTHOPHOTOS) for a window into
a GeoTIFF in the scene CRS, as a visual reference outside Berlin (France only).

The WMS does not offer ETRS89 UTM, so tiles are requested in RGF93 / Lambert-93 (EPSG:2154, the French
realisation of ETRS89) at the target pixel size and warped to the scene CRS grid.

    fetch_ign_ortho.py --crs EPSG:25832 --bbox E0 N0 E1 N1 --output ign.tif [--gsd 0.2]
    fetch_ign_ortho.py --like ground_nearest.tif --output ign.tif      # window and CRS of an existing raster

`--crs` defaults to env ORTHO_CRS. Licence of the data: Etalab open licence 2.0 (IGN BD ORTHO).
"""
from __future__ import annotations

import argparse
import io
import math
import os
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

WMS = "https://data.geopf.fr/wms-r"
LAYER = "ORTHOIMAGERY.ORTHOPHOTOS"
WORK_CRS = "EPSG:2154"
TILE_PX = 2000


def get_tile(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> np.ndarray:
    query = urllib.parse.urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": LAYER, "STYLES": "", "CRS": WORK_CRS,
        "BBOX": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", "WIDTH": w, "HEIGHT": h, "FORMAT": "image/png",
    })
    req = urllib.request.Request(f"{WMS}?{query}", headers={"User-Agent": "streetlevel-ortho-fetch-ign"})
    data = urllib.request.urlopen(req, timeout=60).read()
    try:
        return np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception:
        raise SystemExit(f"WMS did not return an image: {data[:300]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crs", default=os.environ.get("ORTHO_CRS"), help="scene CRS of the output (default: env ORTHO_CRS)")
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("E0", "N0", "E1", "N1"), help="window in the scene CRS, metres")
    ap.add_argument("--like", type=Path, help="raster whose CRS and bounds define the window")
    ap.add_argument("--gsd", type=float, default=0.2, help="pixel size in metres (the source is 0.2 m)")
    ap.add_argument("--margin", type=float, default=0.0, help="widen the window by this many metres")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--max-pixels", type=int, default=40_000_000)
    args = ap.parse_args()
    if args.like:
        with rasterio.open(args.like) as src:
            crs, bbox = src.crs.to_string(), tuple(src.bounds)
    elif args.bbox and args.crs:
        crs, bbox = args.crs, tuple(args.bbox)
    else:
        ap.error("give --like, or --bbox with --crs (or env ORTHO_CRS)")
    e0, n0, e1, n1 = bbox[0] - args.margin, bbox[1] - args.margin, bbox[2] + args.margin, bbox[3] + args.margin

    # target grid in the scene CRS
    w_out, h_out = int(math.ceil((e1 - e0) / args.gsd)), int(math.ceil((n1 - n0) / args.gsd))
    # source window in Lambert-93, with a few pixels of slack for the warp
    sx0, sy0, sx1, sy1 = transform_bounds(crs, WORK_CRS, e0, n0, e1, n1, densify_pts=21)
    pad = 3 * args.gsd
    sx0, sy0 = math.floor((sx0 - pad) / args.gsd) * args.gsd, math.floor((sy0 - pad) / args.gsd) * args.gsd
    w_src, h_src = int(math.ceil((sx1 + pad - sx0) / args.gsd)), int(math.ceil((sy1 + pad - sy0) / args.gsd))
    sy1 = sy0 + h_src * args.gsd
    if w_src * h_src > args.max_pixels:
        raise SystemExit(f"{w_src} x {h_src} px is more than --max-pixels {args.max_pixels}; use a smaller window")
    src_img = np.zeros((h_src, w_src, 3), np.uint8)
    for ty in range(0, h_src, TILE_PX):
        for tx in range(0, w_src, TILE_PX):
            w, h = min(TILE_PX, w_src - tx), min(TILE_PX, h_src - ty)
            x0, x1 = sx0 + tx * args.gsd, sx0 + (tx + w) * args.gsd
            y1, y0 = sy1 - ty * args.gsd, sy1 - (ty + h) * args.gsd
            src_img[ty:ty + h, tx:tx + w] = get_tile(x0, y0, x1, y1, w, h)
    print(f"downloaded {w_src} x {h_src} px in {WORK_CRS}")

    out = np.zeros((3, h_out, w_out), np.uint8)
    reproject(np.moveaxis(src_img, 2, 0), out, src_transform=from_origin(sx0, sy1, args.gsd, args.gsd), src_crs=WORK_CRS,
              dst_transform=from_origin(e0, n1, args.gsd, args.gsd), dst_crs=crs, resampling=Resampling.bilinear)
    with rasterio.open(args.output, "w", driver="GTiff", width=w_out, height=h_out, count=3, dtype="uint8", crs=crs,
                       transform=from_origin(e0, n1, args.gsd, args.gsd), compress="deflate", photometric="RGB") as dst:
        dst.write(out)
        dst.update_tags(source=f"IGN Géoplateforme WMS {LAYER}", fetched_in=WORK_CRS)
    print(f"{args.output}: {w_out} x {h_out} px, {crs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
