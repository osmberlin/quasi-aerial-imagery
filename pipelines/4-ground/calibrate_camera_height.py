#!/usr/bin/env python3
"""Find the camera height above ground from the images themselves.

For a trial height, the ground patch between two neighbouring frames is projected from both
frames. Only at the true height do the two projections show the same paving at the same place,
so the height with the highest mean normalised cross-correlation over all neighbouring pairs is
the camera height (in the model's metric units, i.e. tied to the GPS scale of the model).
It replaces a guessed "about 2 m".
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

from render_ground_ortho import level_rotation, pick_convention, project, read_model, reference_xy, fit_similarity_2d

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--model", type=Path, required=True)
ap.add_argument("--images", type=Path, required=True)
ap.add_argument("--mask", type=Path)
ap.add_argument("--heights", type=float, nargs=3, default=[1.5, 2.3, 0.025], metavar=("FROM", "TO", "STEP"))
ap.add_argument("--patch", type=float, default=4.0, help="patch edge in metres")
ap.add_argument("--side-offset", type=float, default=2.5,
                help="patch centres lie this far left and right of the track: on the track itself the rider is in the way")
ap.add_argument("--gsd", type=float, default=0.06, help="coarse on purpose: poses are not good to the paving-joint level")
ap.add_argument("--highpass-m", type=float, default=0.5, help="structures larger than this are removed before matching")
ap.add_argument("--max-pairs", type=int, default=40)
ap.add_argument("--max-gap", type=float, default=8.0, help="use frame pairs at most this far apart, metres")
ap.add_argument("--output", type=Path)
args = ap.parse_args()

images, points = read_model(args.model / "txt")
cam = [l for l in (args.model / "txt" / "cameras.txt").read_text().splitlines() if not l.startswith("#")][0].split()
width, height = int(cam[2]), int(cam[3])
conv, _ = pick_convention(images, points, width, height)
level, _ = level_rotation(images)
images = {n: (r @ level.T, t, level @ c, o) for n, (r, t, c, o) in images.items()}
names, utm, _, _ = reference_xy(images, {}, list(images), args.images, False, 2.0)
centres = np.array([images[n][2] for n in names])
scale, _, _ = fit_similarity_2d(centres[:, :2], utm)   # model units -> metres
mask = cv2.imread(str(args.mask), cv2.IMREAD_GRAYSCALE) if args.mask else None

pairs = [(i, i + 1) for i in range(len(names) - 1) if 1.5 < scale * np.linalg.norm(centres[i + 1, :2] - centres[i, :2]) < args.max_gap]
pairs = pairs[:: max(1, len(pairs) // args.max_pairs)][: args.max_pairs]
n_px = int(args.patch / args.gsd)
off = (np.arange(n_px) + 0.5) * args.gsd - args.patch / 2
gx, gy = np.meshgrid(off / scale, off / scale)
cache = {}


def frame(i):
    if i not in cache:
        if len(cache) > 3:
            cache.pop(next(iter(cache)))
        cache[i] = cv2.cvtColor(cv2.imread(str(args.images / names[i])), cv2.COLOR_BGR2GRAY)
    return cache[i]


def patch(i, mid, z):
    rot, t, _, _ = images[names[i]]
    world = np.stack([mid[0] + gx, mid[1] + gy, np.full_like(gx, z)], -1)
    u, v = project(conv, world @ rot.T + t, width, height)
    mu, mv = (u % width).astype(np.float32), np.clip(v, 0, height - 1).astype(np.float32)
    img = cv2.remap(frame(i), mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP).astype(np.float32)
    ok = np.ones_like(img, bool) if mask is None else cv2.remap(mask, mu, mv, cv2.INTER_NEAREST, borderMode=cv2.BORDER_WRAP) > 127
    return img, ok


heights = np.arange(args.heights[0], args.heights[1] + 1e-9, args.heights[2])
rows = []
for i, j in pairs:
    along = centres[j, :2] - centres[i, :2]
    normal = np.array([-along[1], along[0]]) / np.linalg.norm(along)
    cz = (centres[i, 2] + centres[j, 2]) / 2
    for side in (-1, 1):
        mid = (centres[i, :2] + centres[j, :2]) / 2 + side * normal * args.side_offset / scale
        row = np.full(len(heights), np.nan)
        for k, h in enumerate(heights):
            a, oka = patch(i, mid, cz - h / scale)
            b, okb = patch(j, mid, cz - h / scale)
            ok = oka & okb
            if ok.mean() < 0.6:
                break
            # high-pass so that exposure differences and shading do not decide the match
            sig = args.highpass_m / args.gsd
            a, b = a - cv2.GaussianBlur(a, (0, 0), sig), b - cv2.GaussianBlur(b, (0, 0), sig)
            a, b = a[ok], b[ok]
            row[k] = float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum() + 1e-9))
        rows.append(row)
scores = np.array(rows) if rows else np.full((0, len(heights)), np.nan)  # no usable patch at all
valid = ~np.isnan(scores).any(1)
mean = scores[valid].mean(0) if valid.any() else np.full(len(heights), np.nan)
best = heights[int(np.argmax(mean))]
# only patches with a clear peak say something (texture-less asphalt or a parked car do not)
clear = scores[valid].max(1) > 0.3
per_pair = heights[np.argmax(scores[valid][clear], 1)] if clear.any() else np.array([np.nan])
report = {
    "patches_used": int(valid.sum()), "patches_with_clear_peak": int(clear.sum()), "heights_m": [round(float(h), 3) for h in heights],
    "mean_ncc": [round(float(x), 4) for x in mean], "best_height_m": round(float(best), 3),
    "per_pair_best_median_m": round(float(np.median(per_pair)), 3),
    "per_pair_best_p25_p75_m": [round(float(x), 3) for x in np.percentile(per_pair, [25, 75])],
    "model_scale_to_metres": round(float(scale), 5),
}
print(json.dumps(report, indent=1))
if args.output:
    args.output.write_text(json.dumps(report, indent=1))
