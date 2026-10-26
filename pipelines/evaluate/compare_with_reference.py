#!/usr/bin/env python3
"""Fetch reference orthophoto tiles for the extent of one of our GeoTIFFs and write comparison images.

The tile URL template (XYZ, Web Mercator, e.g. https://example.org/{z}/{x}/{y}.jpg) is read from
<workspace>/secrets.env (the folder that contains this repository; SECRETS_ENV overrides the path), line
VAR='template', variable given by --url-var. It may contain a token, so it never goes into the repository
or into any output.
Outputs next to the input: <name>.ref.tif (reference warped onto our grid), <name>.compare.jpg
(ours | reference | 50 % blend) at --scale.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import re
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

WORKSPACE = Path(__file__).resolve().parents[3]
SECRETS = Path(os.environ.get("SECRETS_ENV", WORKSPACE / "secrets.env"))


def url_template(var: str) -> str:
    text = SECRETS.read_text()
    m = re.search(rf"^{var}='([^']+)'", text, re.M)
    if not m:
        raise SystemExit(f"{var} not found in secrets.env")
    return m.group(1)


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def merc(x: int, y: int, z: int) -> tuple[float, float]:
    half = math.pi * 6378137.0
    size = 2 * half / 2**z
    return -half + x * size, half - y * size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("geotiff", type=Path)
    ap.add_argument("--url-var", default="REFERENCE_TILES_URL", help="variable in secrets.env with the XYZ tile URL template")
    ap.add_argument("--zoom", type=int, default=19, help="zoom level of the reference tiles (z19 with 512 px tiles is about 9 cm/px in Berlin)")
    ap.add_argument("--tile-size", type=int, default=512)
    ap.add_argument("--scale", type=float, default=0.5, help="scale of the comparison JPG relative to our raster")
    args = ap.parse_args()
    tmpl = url_template(args.url_var)

    with rasterio.open(args.geotiff) as src:
        ours = src.read()
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        z = args.zoom
        x0, y0 = tile_xy(w, n, z)
        x1, y1 = tile_xy(e, s, z)
        ntiles = (x1 - x0 + 1) * (y1 - y0 + 1)
        if ntiles > 400:
            raise SystemExit(f"{ntiles} tiles at z{z}; lower --zoom")
        ts = args.tile_size
        mosaic = np.zeros(((y1 - y0 + 1) * ts, (x1 - x0 + 1) * ts, 3), np.uint8)
        got = 0
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                url = tmpl.replace("{z}", str(z)).replace("{x}", str(tx)).replace("{y}", str(ty))
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "streetlevel-ortho-compare"})
                    data = urllib.request.urlopen(req, timeout=30).read()
                    tile = np.array(Image.open(io.BytesIO(data)).convert("RGB").resize((ts, ts)))
                    mosaic[(ty - y0) * ts:(ty - y0 + 1) * ts, (tx - x0) * ts:(tx - x0 + 1) * ts] = tile
                    got += 1
                except Exception as exc:  # report without the URL: it holds the token
                    print(f"tile {z}/{tx}/{ty} failed: {type(exc).__name__} {getattr(exc, 'code', '')}")
        print(f"{got}/{ntiles} tiles at z{z}")
        if got == 0:
            return 1
        mx0, my0 = merc(x0, y0, z)
        mx1, my1 = merc(x1 + 1, y1 + 1, z)
        ref = np.zeros((3, src.height, src.width), np.uint8)
        reproject(
            np.moveaxis(mosaic, 2, 0), ref, src_crs="EPSG:3857",
            src_transform=from_bounds(mx0, my1, mx1, my0, mosaic.shape[1], mosaic.shape[0]),
            dst_crs=src.crs, dst_transform=src.transform, resampling=Resampling.bilinear,
        )
        profile = src.profile | {"count": 3}
        with rasterio.open(args.geotiff.with_suffix(".ref.tif"), "w", **profile) as dst:
            dst.write(ref)

    rgb, alpha = np.moveaxis(ours[:3], 0, 2), ours[3] > 0
    refi = np.moveaxis(ref, 0, 2)
    blend = np.where(alpha[..., None], (rgb.astype(np.uint16) + refi) // 2, refi).astype(np.uint8)
    ours_on_ref = np.where(alpha[..., None], rgb, refi // 3)
    panel = np.hstack([ours_on_ref, refi, blend])
    panel = cv2.resize(panel, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
    out = args.geotiff.with_suffix(".compare.jpg")
    cv2.imwrite(str(out), cv2.cvtColor(panel, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
