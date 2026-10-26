#!/usr/bin/env python3
"""Register an ortho to reference kerb lines (e.g. Straßenbefahrung 2014) with one 2D similarity,
validated on held-out blocks, and optionally write registered copies of rasters (only the
geotransform changes, no resampling).

Our kerbs are the curb-labelled cells (band 1 of `<out>.labels.tif`). A trimmed ICP fits rotation,
scale and shift. Validation: for each block of `--block` metres with enough kerb, the similarity is
fitted without that block and the block's kerb cells are measured against the reference
(leave-one-block-out). This shows how well a single placement transfers to parts it was not
fitted on, which is what a map user relies on.

    register_to_reference.py OUT.labels.tif REF.geojson --output PREFIX [--apply a.tif b.tif ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.crs import load_geojson_in_crs  # noqa: E402


def similarity_fit(a: np.ndarray, b: np.ndarray, scale: bool = True):
    """Least-squares s, R, t with b ~ s R a + t (Umeyama)."""
    ca, cb = a.mean(0), b.mean(0)
    A, B = a - ca, b - cb
    u, sv, vt = np.linalg.svd(B.T @ A / len(a))
    d = np.sign(np.linalg.det(u @ vt))
    D = np.diag([1, d])
    r = u @ D @ vt
    s = (sv * np.diag(D)).sum() / (A ** 2).sum(1).mean() if scale else 1.0
    return s, r, cb - s * r @ ca


def icp(pts, tree, dense, gates=(3.0, 2.0, 1.0, 0.6, 0.4, 0.3, 0.3, 0.3), allow_scale=True):
    s, r, t = 1.0, np.eye(2), np.zeros(2)
    for g in gates:
        moved = s * pts @ r.T + t
        d, i = tree.query(moved)
        k = d < g
        if k.sum() < 200:
            break
        s, r, t = similarity_fit(pts[k], dense[i[k]], scale=allow_scale)
    return s, r, t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("labels", type=Path)
    ap.add_argument("reference", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--block", type=float, default=30.0)
    ap.add_argument("--near", type=float, default=3.0, help="use kerb cells within this distance of the reference")
    ap.add_argument("--no-scale", action="store_true")
    ap.add_argument("--apply", type=Path, nargs="*", default=[])
    args = ap.parse_args()

    with rasterio.open(args.labels) as src:
        lab = src.read(1)
        groups = {int(k): v for k, v in json.loads(src.tags()["groups"]).items()}
        t, gsd, map_crs = src.transform, src.res[0], src.crs
    curb = [k for k, v in groups.items() if v == "curb"][0]
    ys, xs = np.nonzero(lab == curb)
    pts = np.column_stack([t.c + (xs + 0.5) * gsd, t.f - (ys + 0.5) * gsd])
    dense = []
    for f in load_geojson_in_crs(args.reference, map_crs)["features"]:
        g = f["geometry"]
        for part in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
            ln = np.asarray(part, float)[:, :2]
            for p, q in zip(ln[:-1], ln[1:]):
                n = max(int(np.hypot(*(q - p)) / 0.01), 1)
                dense.append(p + np.linspace(0, 1, n, endpoint=False)[:, None] * (q - p))
    if not dense:
        print(json.dumps({"error": "reference has no line features in this area"}))
        return 0
    dense = np.concatenate(dense)
    tree = cKDTree(dense)
    d0, _ = tree.query(pts)
    pts = pts[d0 <= args.near]
    sub = pts[:: max(len(pts) // 60000, 1)]
    s, r, tt = icp(sub, tree, dense, allow_scale=not args.no_scale)
    d_all, _ = tree.query(s * pts @ r.T + tt)

    def stats(d):
        inl = d <= 0.3
        return {"share_within_0.10m": round(float((d <= 0.10).mean()), 3), "inlier_share_0.3m": round(float(inl.mean()), 3), "median_m": round(float(np.median(d[inl])), 3) if inl.any() else None,
                "p90_m": round(float(np.percentile(d[inl], 90)), 3) if inl.any() else None}

    res = {"kerb_cells_near_reference": int(len(pts)),
           "before": stats(d0[d0 <= args.near]),
           "similarity": {"scale": round(float(s), 5), "rotation_deg": round(float(np.degrees(np.arctan2(r[1, 0], r[0, 0]))), 3),
                          "shift_at_centre_m": [round(float(v), 3) for v in (s * pts.mean(0) @ r.T + tt - pts.mean(0))]},
           "fitted_all": stats(d_all)}
    bx, by = np.floor(pts[:, 0] / args.block).astype(int), np.floor(pts[:, 1] / args.block).astype(int)
    blocks = []
    for key in sorted(set(zip(bx, by))):
        inb = (bx == key[0]) & (by == key[1])
        if inb.sum() < 2000 or (~inb).sum() < 2000:
            continue
        rest = pts[~inb][:: max((~inb).sum() // 60000, 1)]
        s2, r2, t2 = icp(rest, tree, dense, allow_scale=not args.no_scale)
        dh, _ = tree.query(s2 * pts[inb] @ r2.T + t2)
        sb, rb, tb = icp(pts[inb][:: max(inb.sum() // 20000, 1)], tree, dense, allow_scale=False)
        ds, _ = tree.query(sb * pts[inb] @ rb.T + tb)
        blocks.append({"block_sw": [int(key[0] * args.block), int(key[1] * args.block)], "cells": int(inb.sum()),
                       "held_out": stats(dh), "own_fit": stats(ds), "unregistered": stats(d0[d0 <= args.near][inb])})
    res["leave_one_block_out"] = blocks
    # transform for rasters: x' = s R x + t applied to the affine
    M = Affine(s * r[0, 0], s * r[0, 1], tt[0], s * r[1, 0], s * r[1, 1], tt[1])
    res["affine"] = list(M)[:6]
    for path in args.apply:
        with rasterio.open(path) as src:
            prof = src.profile
            data = src.read()
            tags = src.tags()
        prof.update(transform=M * prof["transform"])
        outp = path.with_name(path.stem + ".registered.tif")
        with rasterio.open(outp, "w", **prof) as dst:
            dst.write(data)
            dst.update_tags(**tags, registered_to=str(args.reference.name))
        res.setdefault("written", []).append(str(outp))
    Path(f"{args.output}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "leave_one_block_out"}))
    for b in blocks:
        print(json.dumps(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
