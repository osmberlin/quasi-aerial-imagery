#!/usr/bin/env python3
"""Merge tiled renders (render_ground_ortho.py --bbox) into one grid: the ortho, and if present the
`.source.tif` and `.labels.tif` side files, so metrics can run on the whole scene.

    merge_tiles.py OUT.tif tile0/nearest.tif tile1/nearest.tif ...
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import rasterio
from rasterio.merge import merge


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("tiles", type=Path, nargs="+")
    args = ap.parse_args()
    for suffix, nodata in ((".tif", None), (".source.tif", -1), (".labels.tif", 0)):
        paths = [t.with_suffix(suffix) if suffix != ".tif" else t for t in args.tiles]
        if not all(p.exists() for p in paths):
            continue
        out = args.output if suffix == ".tif" else args.output.with_suffix(suffix)
        with rasterio.open(paths[0]) as first:
            tags, big = first.tags(), first.width * first.height * len(paths) > 200_000_000
        if not big:
            srcs = [rasterio.open(p) for p in paths]
            mosaic, transform = merge(srcs, nodata=nodata, method="first")
            prof = srcs[0].profile
            prof.update(width=mosaic.shape[2], height=mosaic.shape[1], transform=transform, compress="deflate")
            with rasterio.open(out, "w", **prof) as dst:
                dst.write(mosaic)
            for s in srcs:
                s.close()
        else:  # large mosaics (a 2 cm route): GDAL streams block by block instead of holding the mosaic in memory
            vrt = out.with_suffix(".vrt")
            nd = [] if nodata is None else ["-srcnodata", str(nodata), "-vrtnodata", str(nodata)]
            # gdalbuildvrt draws later files on top, so reverse the list to keep "first wins"
            subprocess.run(["gdalbuildvrt", "-q", *nd, str(vrt), *map(str, reversed(paths))], check=True)
            subprocess.run(["gdal_translate", "-q", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", "-co", "BIGTIFF=IF_SAFER",
                            str(vrt), str(out)], check=True)
            vrt.unlink()
        if tags:
            with rasterio.open(out, "r+") as dst:
                dst.update_tags(**tags)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
