#!/usr/bin/env python3
"""Compare kerbs in our ortho with Berlin's Straßenbefahrung 2014 kerb lines (bd_bordstein).

Our kerb = cells labelled "curb" (band 1 of `<out>.labels.tif`: the label of the frame the mosaic
took, so seams in the image are seams here too). Steps:

1. Absolute: distance from every kerb cell to the nearest reference line, as georeferenced.
2. Registered: a 2D rigid transform (rotation + shift, trimmed ICP) moves our kerb cells onto the
   reference; the residual distances are the relative accuracy, i.e. what is left after placing
   the ortho with reference points.
3. Continuity: along every reference line, every 10 cm, the signed perpendicular offset of the
   centre of our kerb band (within +-0.6 m). Jumps = changes > 5 cm between neighbouring samples;
   each jump is checked against a change of source frame (`<out>.source.tif`) at that place.

Writes `<prefix>.json` and `<prefix>_offsets.jpg` (offset profile plot per line).

    kerb_reference.py OUT.labels.tif REF.geojson --source OUT.source.tif --output PREFIX
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.crs import load_geojson_in_crs  # noqa: E402


def rigid_fit(a: np.ndarray, b: np.ndarray):
    """Least-squares rotation + translation mapping a onto b (2D)."""
    ca, cb = a.mean(0), b.mean(0)
    u, _, vt = np.linalg.svd((a - ca).T @ (b - cb))
    r = (u @ vt).T
    if np.linalg.det(r) < 0:
        vt[-1] *= -1
        r = (u @ vt).T
    return r, cb - ca @ r.T


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("labels", type=Path)
    ap.add_argument("reference", type=Path, help="bd_bordstein.geojson (reprojected to the raster CRS if its CRS differs)")
    ap.add_argument("--source", type=Path, help="<out>.source.tif on the same grid")
    ap.add_argument("--group", default="curb", help="label group that forms the line (curb, marking)")
    ap.add_argument("--band", type=int, default=1, help="1 = label of the mosaic's frame, 2 = vote")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--edge-from", type=Path,
                    help="ortho on the same grid: take the kerb position as the strongest luminance edge across the "
                         "reference line instead of the centre of the curb label band")
    ap.add_argument("--edge-near-label", type=float, default=0.0,
                    help="with --edge-from: search the edge only within this distance (m) of the curb label band centre")
    ap.add_argument("--min-edge", type=float, default=6.0, help="minimum luminance step per cell for --edge-from")
    ap.add_argument("--smooth", type=int, default=5, help="running median over this many 10 cm samples before counting jumps (1 = off)")
    ap.add_argument("--block", type=float, default=30.0, help="block size for local registration (m), 0 = off")
    ap.add_argument("--max-dist", type=float, default=1.0, help="ignore kerb cells farther than this from any reference line (m)")
    args = ap.parse_args()

    with rasterio.open(args.labels) as src:
        lab = src.read(args.band)
        groups = {int(k): v for k, v in json.loads(src.tags()["groups"]).items()}
        t, gsd, (h, w), map_crs = src.transform, src.res[0], src.shape, src.crs
    curb_id = [k for k, v in groups.items() if v == args.group][0]
    covered = lab > 0
    ys, xs = np.nonzero(lab == curb_id)
    pts = np.column_stack([t.c + (xs + 0.5) * gsd, t.f - (ys + 0.5) * gsd])

    lines = []
    for f in load_geojson_in_crs(args.reference, map_crs)["features"]:
        g = f["geometry"]
        for part in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
            lines.append(np.asarray(part, float)[:, :2])
    # densify reference lines to 1 cm points for nearest-point queries
    dense, line_id = [], []
    for k, ln in enumerate(lines):
        for p, q in zip(ln[:-1], ln[1:]):
            n = max(int(np.hypot(*(q - p)) / 0.01), 1)
            s = np.linspace(0, 1, n, endpoint=False)[:, None]
            dense.append(p + s * (q - p))
            line_id.append(np.full(n, k))
        dense.append(ln[-1:])
        line_id.append([k])
    if not dense:
        print(json.dumps({"error": "reference has no line features in this area"}))
        return 0
    dense = np.concatenate(dense)
    line_id = np.concatenate(line_id)
    tree = cKDTree(dense)

    d_abs, _ = tree.query(pts)
    near = d_abs <= 3.0  # only kerb cells plausibly belonging to a surveyed kerb
    res = {"kerb_cells": int(len(pts)), "reference_lines": len(lines)}
    res["absolute_m"] = {"median": round(float(np.median(d_abs[near])), 3), "p90": round(float(np.percentile(d_abs[near], 90)), 3),
                         "share_within_3m": round(float(near.mean()), 3)}

    # trimmed ICP: rotation + shift
    rot, sh = np.eye(2), np.zeros(2)
    sub = pts[near][:: max(int(near.sum() // 40000), 1)]
    for it in range(30):
        moved = sub @ rot.T + sh
        d, i = tree.query(moved)
        keep = d < max(np.percentile(d, 80), 0.05)
        r, s = rigid_fit(sub[keep], dense[i[keep]])
        if np.allclose(r, rot) and np.allclose(s, sh, atol=1e-4):
            break
        rot, sh = r, s
    moved = pts @ rot.T + sh
    d_reg, nn = tree.query(moved)
    used = d_reg <= args.max_dist
    centre = pts.mean(0)
    res["registration"] = {"rotation_deg": round(float(np.degrees(np.arctan2(rot[1, 0], rot[0, 0]))), 3),
                           "shift_at_centre_m": [round(float(v), 3) for v in (centre @ rot.T + sh - centre)]}
    res["registered_m"] = {"median": round(float(np.median(d_reg[used])), 3), "p90": round(float(np.percentile(d_reg[used], 90)), 3),
                           "share_within_max_dist": round(float(used.mean()), 3)}
    # fixed denominator (all kerb cells within 3 m before registration), comparable between variants
    dn = d_reg[near]
    res["registered_share_of_near_cells_within"] = {f"{t:.2f}m": round(float((dn <= t).mean()), 3) for t in (0.05, 0.10, 0.20)}

    # local registration: the same ICP per block, to separate a smooth georeference error
    # (GPS fit) from the local geometry that matters once the ortho is placed with reference points
    if args.block > 0:
        blocks = []
        bx = np.floor(pts[:, 0] / args.block).astype(int)
        by = np.floor(pts[:, 1] / args.block).astype(int)
        for key in sorted(set(zip(bx[near], by[near]))):
            sel = near & (bx == key[0]) & (by == key[1])
            if sel.sum() < 2000:
                continue
            bp = pts[sel][:: max(int(sel.sum() // 20000), 1)]
            r_, s_ = np.eye(2), np.zeros(2)
            for gate in (3.0, 2.0, 1.0, 0.6, 0.4, 0.3, 0.3, 0.3):
                d, i = tree.query(bp @ r_.T + s_)
                k = d < gate
                if k.sum() < 200:
                    break
                r_, s_ = rigid_fit(bp[k], dense[i[k]])
            d, _ = tree.query(pts[sel] @ r_.T + s_)
            c = pts[sel].mean(0)
            inl = d <= 0.3
            blocks.append({"block_sw": [int(key[0] * args.block), int(key[1] * args.block)], "kerb_cells": int(sel.sum()),
                           "share_within_0.10m": round(float((d <= 0.10).mean()), 3),
                           "shift_m": [round(float(v), 2) for v in (c @ r_.T + s_ - c)],
                           "rotation_deg": round(float(np.degrees(np.arctan2(r_[1, 0], r_[0, 0]))), 2),
                           "inlier_share_0.3m": round(float(inl.mean()), 3),
                           "inlier_median_m": round(float(np.median(d[inl])), 3) if inl.any() else None,
                           "inlier_p90_m": round(float(np.percentile(d[inl], 90)), 3) if inl.any() else None})
        res["blocks"] = blocks

    # continuity along reference lines (reference moved into our frame with the inverse transform)
    inv_rot = rot.T
    src = rasterio.open(args.source).read(1) if args.source else None
    curb = lab == curb_id
    lum = None
    if args.edge_from:
        with rasterio.open(args.edge_from) as e:
            if e.shape != (h, w) or e.transform != t:
                raise SystemExit("--edge-from must be on the labels grid")
            rgb = e.read([1, 2, 3])
            alpha = e.read(4) if e.count >= 4 else np.full((h, w), 255, np.uint8)
        lum = cv2.cvtColor(np.ascontiguousarray(np.moveaxis(rgb, 0, -1)), cv2.COLOR_RGB2GRAY).astype(np.float32)
        lum = cv2.GaussianBlur(lum, (0, 0), 1.0)
        lum[alpha == 0] = np.nan
    profiles, n_jumps, n_jump_at_seam, n_pairs, length, n_pairs_at_seam, wobble = [], 0, 0, 0, 0.0, 0, []
    for ln in lines:
        ours = (ln - sh) @ inv_rot.T
        # resample by arc length at exactly 0.1 m, so lengths and per-step jumps do not depend on vertex spacing
        seglen = np.hypot(*np.diff(ours, axis=0).T)
        arc = np.r_[0, np.cumsum(seglen)]
        if arc[-1] < 0.2:
            continue
        at = np.arange(0, arc[-1], 0.1)
        samp = np.column_stack([np.interp(at, arc, ours[:, 0]), np.interp(at, arc, ours[:, 1])])
        if len(samp) < 5:
            continue
        tang = np.gradient(samp, axis=0)
        tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
        normal = np.column_stack([-tang[:, 1], tang[:, 0]])
        offs = np.arange(-0.6, 0.6001, gsd)
        prof = samp[:, None, :] + offs[None, :, None] * normal[:, None, :]
        col = ((prof[..., 0] - t.c) / gsd).astype(int)
        row = ((t.f - prof[..., 1]) / gsd).astype(int)
        inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
        colc, rowc = col.clip(0, w - 1), row.clip(0, h - 1)
        hit = curb[rowc, colc] & inside
        cov = (covered[rowc, colc] & inside).mean(1) > 0.8
        cnt = hit.sum(1)
        if lum is None:
            off = np.where((cnt >= 3) & cov, (hit * offs[None]).sum(1) / np.maximum(cnt, 1), np.nan)
        else:
            prof_l = np.where(inside, lum[rowc, colc], np.nan)
            grad = np.abs(np.diff(prof_l, axis=1))
            ok_row = np.isfinite(grad).all(1)
            if args.edge_near_label > 0:  # only edges within this distance of the curb label band centre
                band = np.where((cnt >= 3) & cov, (hit * offs[None]).sum(1) / np.maximum(cnt, 1), np.nan)
                mids = (offs[:-1] + offs[1:]) / 2
                near_band = np.abs(mids[None, :] - band[:, None]) <= args.edge_near_label
                grad = np.where(near_band, grad, np.nan)
                ok_row = np.isfinite(band) & np.isfinite(grad).any(1)
            gi = np.nanargmax(np.where(np.isfinite(grad), grad, -1), axis=1)
            gmax = np.take_along_axis(np.nan_to_num(grad, nan=0), gi[:, None], 1)[:, 0]
            off = np.where(ok_row & (gmax >= args.min_edge), (offs[gi] + offs[gi + 1]) / 2, np.nan)
        mid = len(offs) // 2
        srcv = src[rowc[:, mid], colc[:, mid]] if src is not None else None
        if args.smooth > 1:  # running median over `smooth` samples to suppress label-edge noise
            k = args.smooth // 2
            pad = np.pad(off, k, constant_values=np.nan)
            win = np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1)
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    sm = np.nanmedian(win, axis=1)
            off = np.where(np.isfinite(off) & (np.isfinite(win).sum(1) > k), sm, np.nan)
        valid = np.isfinite(off[1:]) & np.isfinite(off[:-1])
        jump = valid & (np.abs(np.diff(off)) > 0.05)
        n_pairs += int(valid.sum())
        n_jumps += int(jump.sum())
        length += float(np.isfinite(off).sum() * 0.1)
        if srcv is not None:
            seam = srcv[1:] != srcv[:-1]
            # a jump counts as "at a seam" if the source changes within +-2 samples (20 cm)
            near_seam = (np.convolve(seam.astype(int), np.ones(5, int), mode="same") > 0)[: len(seam)]  # "same" pads short profiles
            n_jump_at_seam += int((jump & near_seam).sum())
            n_pairs_at_seam += int((valid & near_seam).sum())
        profiles.append(off)
        # wobble: deviation from a 2 m running median of the same profile (no threshold)
        pad = np.pad(off, 10, constant_values=np.nan)
        win = np.lib.stride_tricks.sliding_window_view(pad, 21)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            trend = np.nanmedian(win, axis=1)
        dev = off - trend
        wobble.extend(dev[np.isfinite(dev) & (np.isfinite(win).sum(1) >= 11)].tolist())
    valid_off = np.concatenate([p[np.isfinite(p)] for p in profiles]) if profiles else np.array([])
    res["continuity"] = {"measured_kerb_length_m": round(length, 1), "sample_pairs": n_pairs,
                         "position_from": ("edge" + (f"<={args.edge_near_label}m of label" if args.edge_near_label else "")) if lum is not None else "label", "smooth_samples": args.smooth, "jumps_over_5cm": n_jumps, "wobble_m_rms_p90": [round(float(np.sqrt(np.mean(np.square(wobble)))), 4), round(float(np.percentile(np.abs(wobble), 90)), 4)] if wobble else None, "jumps_per_10m": round(n_jumps / max(length, 1e-6) * 10, 2),
                         "jumps_at_source_change_share": round(n_jump_at_seam / max(n_jumps, 1), 3) if src is not None else None,
                         "pairs_at_source_change_share": round(n_pairs_at_seam / max(n_pairs, 1), 3) if src is not None else None,
                         "offset_spread_m": {"std": round(float(np.std(valid_off)), 3) if valid_off.size else None,
                                             "p05_p50_p95": [round(float(v), 3) for v in np.percentile(valid_off, [5, 50, 95])] if valid_off.size else None}}
    Path(f"{args.output}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))

    # plot: offset profiles of the 12 longest measured lines
    order = sorted(range(len(profiles)), key=lambda k: -np.isfinite(profiles[k]).sum())[:12]
    plot = np.full((12 * 80, 1200, 3), 255, np.uint8)
    for r, k in enumerate(order):
        p = profiles[k]
        y0 = r * 80 + 40
        cv2.line(plot, (0, y0), (1199, y0), (200, 200, 200), 1)
        xs_ = np.linspace(0, 1199, len(p)).astype(int)
        for a in range(len(p) - 1):
            if np.isfinite(p[a]) and np.isfinite(p[a + 1]):
                c = (0, 0, 255) if abs(p[a + 1] - p[a]) > 0.05 else (0, 0, 0)
                cv2.line(plot, (xs_[a], int(y0 - p[a] * 60)), (xs_[a + 1], int(y0 - p[a + 1] * 60)), c, 2)
        cv2.putText(plot, f"line {k}: {np.isfinite(p).sum() / 10:.0f} m, scale 1 px = 1.7 cm", (5, r * 80 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
    cv2.imwrite(f"{args.output}_offsets.jpg", plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
