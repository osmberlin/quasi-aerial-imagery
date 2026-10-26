#!/usr/bin/env python3
"""Export scene results for the viewer (`viewer/`): WebP chunks plus one manifest.json.

Which scenes and which GeoTIFFs go in is read from a scene list (JSON, --scenes):

    {"scenes": [{"id": "obernai-nord", "title": "Obernai, north of the bridge", "captured": "2026-10-23",
                 "dir": "2026-10-23-obernai/scene-nord",
                 "layers": [["ground", "Ground, two-band", "ground/ground_twoband.tif"],
                            ["nearest", "Nearest frame", ["ortho/ground_nearest.registered.tif", "ortho/ground_nearest.tif"]]],
                 "anchor": [lon, lat], "notes": "...", "markers": [{"position": [lon, lat], "label": "..."}]}]}

`dir` is relative to the imagery folder, layer paths relative to `dir`; a list of paths takes the first
that exists. `anchor` (only needed for scenes without a CRS), `notes` and `markers` are optional. Optional
files next to the scene list: assessments/<scene id>.json (shown in the viewer) and placements/<scene id>.json
(a manual adjustment from the viewer, baked into the tile corners).

Each GeoTIFF is cut into chunks of at most 2048 px, written with their corner positions in local metres
(x east, y north) around the scene origin. Output goes to <imagery>/viewer-data/ (imagery never enters the
repository); viewer/public/data is a symlink to it (see viewer/README.md).

    pipelines/.venv/bin/python pipelines/5-export/export_viewer_data.py --scenes path/to/scenes.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO.parent
# Scene results live outside the repository (imagery is never committed).
IMAGERY = Path(os.environ.get("IMAGERY_DIR", WORKSPACE / "imagery"))
OUT = Path(os.environ.get("VIEWER_DATA_DIR", IMAGERY / "viewer-data"))
# Public exports must come from anonymised frames: ANONYMISED=1 reads the "<dir>-anon" results
# (same poses, projections rebuilt from blurred frames) and marks the manifest.
ANONYMISED = os.environ.get("ANONYMISED") == "1"
CHUNK = 2048
DEFAULT_SCENES = REPO / "docs" / "internal" / "viewer" / "scenes.json"


def metres_per_degree(lat0: float) -> tuple[float, float]:
    """Same series as the viewer (viewer/src/geo/coordinates.ts), so the round trip is exact."""
    phi = math.radians(lat0)
    lng = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)
    lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi) - 0.0023 * math.cos(6 * phi)
    return lng, lat


def bake(layers: list[dict], extent: list[float], adj: dict) -> None:
    """Apply a viewer adjustment to all tile corners, exactly as viewer/src/geo/adjustment.ts does:
    scale and counter-clockwise rotation about the extent centre, then translation."""
    cx, cy = (extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2
    t = math.radians(adj["rotationDeg"])
    c, s = math.cos(t), math.sin(t)
    for layer in layers:
        for tile in layer["tiles"]:
            new = []
            for x, y in tile["corners"]:
                vx, vy = (x - cx) * adj["scale"], (y - cy) * adj["scale"]
                new.append([round(cx + c * vx - s * vy + adj["dx"], 3), round(cy + s * vx + c * vy + adj["dy"], 3)])
            tile["corners"] = new
        layer["metersPerPixel"] = layer["metersPerPixel"] * adj["scale"]


def first_existing(base: Path, path: str | list[str]) -> str | None:
    for p in [path] if isinstance(path, str) else path:
        if (base / p).exists():
            return p
    return None


def export_scene(scene: dict, config_dir: Path) -> dict | None:
    base = IMAGERY / (scene["dir"] + ("-anon" if ANONYMISED else ""))
    found = [(i, t, first_existing(base, p)) for i, t, p in scene["layers"]]
    present = [(i, t, base / p) for i, t, p in found if p]
    if not present:
        print(f"skip {scene['id']}: no outputs")
        return None
    with rasterio.open(present[0][2]) as src:
        crs, bounds = src.crs, src.bounds
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    if crs:
        to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        origin = list(to_ll.transform(cx, cy))
    else:
        to_ll, origin = None, scene["anchor"]
    mx, my = metres_per_degree(origin[1])

    def local(x: float, y: float) -> list[float]:
        if to_ll is None:
            return [round(x - cx, 3), round(y - cy, 3)]
        lon, lat = to_ll.transform(x, y)
        return [round((lon - origin[0]) * mx, 3), round((lat - origin[1]) * my, 3)]

    layers, extent, frames = [], [1e9, 1e9, -1e9, -1e9], 0
    for layer_id, title, path in present:
        out_dir = OUT / scene["id"] / layer_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.webp"):
            old.unlink()
        tiles = []
        with rasterio.open(path) as src:
            data = np.moveaxis(src.read(), 0, 2)
            tr, gsd = src.transform, src.res[0]
        for r0 in range(0, data.shape[0], CHUNK):
            for c0 in range(0, data.shape[1], CHUNK):
                chunk = data[r0:r0 + CHUNK, c0:c0 + CHUNK]
                if not (chunk[..., 3] > 0).any():
                    continue
                name = f"{r0 // CHUNK}_{c0 // CHUNK}.webp"
                Image.fromarray(chunk, "RGBA").save(out_dir / name, "WEBP", quality=84, method=4)
                r1, c1 = r0 + chunk.shape[0], c0 + chunk.shape[1]
                corners = [local(*(tr * (c, r))) for c, r in ((c0, r0), (c1, r0), (c1, r1), (c0, r1))]
                tiles.append({"image": f"{scene['id']}/{layer_id}/{name}", "corners": corners})
                for x, y in corners:
                    extent = [min(extent[0], x), min(extent[1], y), max(extent[2], x), max(extent[3], y)]
        report_path = path.with_suffix(".json")
        report = json.loads(report_path.read_text()) if report_path.exists() else None
        if report:
            frames = max(frames, int(report.get("frames_used", 0)))
            report = {k: v for k, v in report.items() if k not in ("model", "images", "ply")}
        layers.append({"id": layer_id, "title": title, "metersPerPixel": gsd, "tiles": tiles, **({"report": report} if report else {})})
        print(f"  {scene['id']}/{layer_id}: {len(tiles)} chunks")
    placement = None
    placement_path = config_dir / "placements" / f"{scene['id']}.json"
    if placement_path.exists():
        placement = json.loads(placement_path.read_text())
        bake(layers, extent, placement["adjustment"])
        extent = [1e9, 1e9, -1e9, -1e9]
        for layer in layers:
            for tile in layer["tiles"]:
                for x, y in tile["corners"]:
                    extent = [min(extent[0], x), min(extent[1], y), max(extent[2], x), max(extent[3], y)]
    assessment_path = config_dir / "assessments" / f"{scene['id']}.json"
    return {
        "id": scene["id"], "title": scene["title"], "captured": scene["captured"], "frames": frames,
        "georeferenced": to_ll is not None, "origin": [round(origin[0], 7), round(origin[1], 7)],
        "extentMeters": dict(zip(("minX", "minY", "maxX", "maxY"), extent)), "layers": layers,
        "assessment": json.loads(assessment_path.read_text()) if assessment_path.exists() else None,
        **({"notes": scene["notes"]} if "notes" in scene else {}),
        **({"markers": scene["markers"]} if "markers" in scene else {}),
        **({"placement": {k: placement[k] for k in ("version", "date", "by", "reference")}} if placement else {}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenes", type=Path, default=DEFAULT_SCENES, help=f"scene list (default: {DEFAULT_SCENES.relative_to(REPO)})")
    args = ap.parse_args()
    config = json.loads(args.scenes.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in (export_scene(sc, args.scenes.resolve().parent) for sc in config["scenes"]) if s]
    manifest = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "anonymised": ANONYMISED,
                "scenes": scenes}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    print(f"{len(scenes)} scenes, {size:.0f} MB in {OUT}")


if __name__ == "__main__":
    main()
