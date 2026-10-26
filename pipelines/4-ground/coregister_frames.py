#!/usr/bin/env python3
"""Co-register frames on the ground: per-frame ground offsets from dense optical flow between the
ground patches of overlapping frames (kerb jumps were found to sit at frame
switches).

For every pair of frames whose patches overlap, DIS optical flow is computed on the high-passed
luminance of the overlap (cells valid in both, both within `--max-dist` of their cameras, and
textured). The robust median flow f_ij is the shift of frame j's picture of the ground relative
to frame i's. With shifts s_i per frame, f_ij = s_j - s_i; the s_i are solved by weighted least
squares with sum(s) = 0 (keeps the georeference) and a small pull towards 0. The renderer then
samples frame i at cell + s_i (`--frame-offsets`).

    coregister_frames.py PATCH_DIR OUT.json [--max-dist 4 --min-cells 4000]
Patches come from render_ground_ortho.py --export-patches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import lsqr


def solve_rigid(args, names, index, pairs) -> int:
    """Unknowns per frame: tx, ty (m), theta (rad) about the camera. Observation per block at p:
    f = s_b(p) - s_a(p), with s_i(p) = t_i + theta_i * perp(p - c_i), perp(x, y) = (-y, x)."""
    n = len(names)
    cams = np.array([index["frames"][nm]["cam"] for nm in names])
    nobs = sum(len(p[6]) for p in pairs)
    A = lil_matrix((2 * nobs + 3 * n + 3, 3 * n))
    rhs = np.zeros(2 * nobs + 3 * n + 3)
    r = 0
    blocks = []
    for a, b, *_, obs in pairs:
        for east, north, fe, fn, cnt in obs:
            w = np.sqrt(min(cnt, 1600) / 1600)
            ra, rb = np.array([east, north]) - cams[a], np.array([east, north]) - cams[b]
            # east component
            A[r, 3 * b], A[r, 3 * b + 2], A[r, 3 * a], A[r, 3 * a + 2] = w, -w * rb[1], -w, w * ra[1]
            rhs[r] = w * fe
            r += 1
            A[r, 3 * b + 1], A[r, 3 * b + 2], A[r, 3 * a + 1], A[r, 3 * a + 2] = w, w * rb[0], -w, -w * ra[0]
            rhs[r] = w * fn
            r += 1
            blocks.append((a, b, east, north, fe, fn))
    for i in range(n):
        A[r, 3 * i], A[r + 1, 3 * i + 1], A[r + 2, 3 * i + 2] = args.pull, args.pull, args.pull * 5
        r += 3
    for k in range(3):  # no net shift or rotation of the whole scene
        for i in range(n):
            A[r, 3 * i + k] = 10.0
        r += 1
    sol = lsqr(A.tocsr()[:r], rhs[:r], atol=1e-12, btol=1e-12)[0].reshape(n, 3)

    def shift(i, e, nn):
        rel = np.array([e, nn]) - cams[i]
        return sol[i, :2] + sol[i, 2] * np.array([-rel[1], rel[0]])
    before = [np.hypot(fe, fn) for *_, fe, fn in blocks]
    after = [np.hypot(*(shift(b, e, nn) - shift(a, e, nn) - np.array([fe, fn]))) for a, b, e, nn, fe, fn in blocks]
    offsets = {names[i]: [round(float(sol[i, 0]), 4), round(float(sol[i, 1]), 4), round(float(sol[i, 2]), 6)] for i in range(n)}
    info = {"model": "rigid", "pairs": len(pairs), "blocks": len(blocks), "frames": n,
            "block_flow_before_m_p50_p90": [round(float(np.median(before)), 4), round(float(np.percentile(before, 90)), 4)],
            "block_residual_after_m_p50_p90": [round(float(np.median(after)), 4), round(float(np.percentile(after, 90)), 4)],
            "rotation_deg_p50_p90_max": [round(float(np.degrees(np.median(np.abs(sol[:, 2])))), 3),
                                         round(float(np.degrees(np.percentile(np.abs(sol[:, 2]), 90))), 3),
                                         round(float(np.degrees(np.abs(sol[:, 2]).max())), 3)]}
    args.output.write_text(json.dumps(offsets, indent=0))
    args.output.with_suffix(".info.json").write_text(json.dumps(info, indent=1))
    print(json.dumps(info))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("patches", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--max-dist", type=float, default=4.0, help="use cells within this distance of both cameras (m)")
    ap.add_argument("--min-cells", type=int, default=4000)
    ap.add_argument("--pull", type=float, default=0.05, help="weight pulling each shift towards 0")
    ap.add_argument("--max-shift-m", type=float, default=0.5, help="discard pair flows larger than this")
    ap.add_argument("--rigid", action="store_true",
                    help="per frame a shift plus a small rotation about its camera, from block-wise flow")
    ap.add_argument("--block", type=int, default=40, help="block size in cells for --rigid")
    args = ap.parse_args()

    index = json.loads((args.patches / "index.json").read_text())
    gsd = index["gsd"]
    names = list(index["frames"])
    data = {}
    for n in names:
        z = np.load(args.patches / f"{Path(n).stem}.npz")
        g = cv2.cvtColor(z["patch"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        hp = g - cv2.GaussianBlur(g, (0, 0), 0.3 / gsd / 3)
        data[n] = (index["frames"][n]["y0"], index["frames"][n]["x0"], hp, z["ok"] & (z["d"] <= args.max_dist * 10))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    pairs = []
    for a in range(len(names)):
        ya, xa, ha, oka = data[names[a]]
        for b in range(a + 1, len(names)):
            yb, xb, hb, okb = data[names[b]]
            y0, x0 = max(ya, yb), max(xa, xb)
            y1, x1 = min(ya + ha.shape[0], yb + hb.shape[0]), min(xa + ha.shape[1], xb + hb.shape[1])
            if y1 - y0 < 50 or x1 - x0 < 50:
                continue
            sa = (slice(y0 - ya, y1 - ya), slice(x0 - xa, x1 - xa))
            sb = (slice(y0 - yb, y1 - yb), slice(x0 - xb, x1 - xb))
            both = oka[sa] & okb[sb]
            if both.sum() < args.min_cells:
                continue
            ia, ib = ha[sa], hb[sb]
            tex = (np.abs(ia) > 4) & (np.abs(ib) > 4) & both
            if tex.sum() < args.min_cells // 4:
                continue
            to8 = lambda im: np.clip(im * 4 + 128, 0, 255).astype(np.uint8)
            flow = dis.calc(to8(np.where(both, ia, 0)), to8(np.where(both, ib, 0)), None)
            fx, fy = flow[..., 0][tex], flow[..., 1][tex]
            mx, my = float(np.median(fx)), float(np.median(fy))
            spread = float(np.median(np.hypot(fx - mx, fy - my)))
            if np.hypot(mx, my) * gsd > args.max_shift_m:
                continue
            obs = []
            if args.rigid:  # block medians: position (east, north) and flow (east, north) in metres
                for by in range(0, tex.shape[0] - args.block + 1, args.block):
                    for bx in range(0, tex.shape[1] - args.block + 1, args.block):
                        t_ = tex[by:by + args.block, bx:bx + args.block]
                        if t_.sum() < args.block * args.block // 6:
                            continue
                        fxb = float(np.median(flow[by:by + args.block, bx:bx + args.block, 0][t_]))
                        fyb = float(np.median(flow[by:by + args.block, bx:bx + args.block, 1][t_]))
                        if abs(fxb - mx) * gsd > 0.3 or abs(fyb - my) * gsd > 0.3:
                            continue
                        east = index["e0"] + (x0 + bx + args.block / 2) * gsd
                        north = index["n1"] - (y0 + by + args.block / 2) * gsd
                        obs.append((east, north, fxb * gsd, -fyb * gsd, int(t_.sum())))
            pairs.append((a, b, mx, my, int(tex.sum()), spread, obs))
    n = len(names)
    if args.rigid:
        return solve_rigid(args, names, index, pairs)
    rows = len(pairs) * 2 + 2 * n + 2
    A = lil_matrix((rows, 2 * n))
    rhs = np.zeros(rows)
    r = 0
    for a, b, mx, my, cnt, spread, _ in pairs:
        w = np.sqrt(min(cnt, 40000) / 40000) / max(spread, 0.5)
        for k, f in ((0, mx), (1, my)):
            A[r, 2 * b + k], A[r, 2 * a + k], rhs[r] = w, -w, w * f
            r += 1
    for i in range(n):
        for k in range(2):
            A[r, 2 * i + k] = args.pull
            r += 1
    for k in range(2):  # sum of shifts = 0
        for i in range(n):
            A[r, 2 * i + k] = 10.0
        r += 1
    sol = lsqr(A.tocsr(), rhs, atol=1e-10, btol=1e-10)[0].reshape(n, 2)
    # pixel shifts (x right = east, y down = south) -> metres east/north
    offsets = {names[i]: [round(float(sol[i, 0] * gsd), 4), round(float(-sol[i, 1] * gsd), 4)] for i in range(n)}
    resid = [np.hypot((sol[b, 0] - sol[a, 0]) - mx, (sol[b, 1] - sol[a, 1]) - my) * gsd for a, b, mx, my, _, _, _ in pairs]
    raw = [np.hypot(mx, my) * gsd for _, _, mx, my, _, _, _ in pairs]
    mag = np.hypot(sol[:, 0], sol[:, 1]) * gsd
    info = {"pairs": len(pairs), "frames": n,
            "pair_shift_before_m_p50_p90": [round(float(np.median(raw)), 4), round(float(np.percentile(raw, 90)), 4)],
            "pair_residual_after_m_p50_p90": [round(float(np.median(resid)), 4), round(float(np.percentile(resid, 90)), 4)],
            "frame_offset_m_p50_p90_max": [round(float(np.median(mag)), 4), round(float(np.percentile(mag, 90)), 4), round(float(mag.max()), 4)]}
    args.output.write_text(json.dumps(offsets, indent=0))
    args.output.with_suffix(".info.json").write_text(json.dumps(info, indent=1))
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
