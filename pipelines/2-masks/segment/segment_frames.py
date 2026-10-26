#!/usr/bin/env python3
"""Semantic labels for equirectangular frames (Mapillary Vistas classes).

Each ERP frame is cut into pinhole views (yaws every 45 deg, pitched down), every view is labelled
with Mask2Former trained on Mapillary Vistas, and the labels are mapped back to a reduced ERP grid.
Where views overlap, the view whose centre is closest to the ray wins. Output per frame:
`<out>/<stem>.png`, uint8 Vistas class id (255 = not covered), plus a colour preview of the first
frames and `labels.json` with the class names.

    pipelines/2-masks/segment/.venv/bin/python pipelines/2-masks/segment/segment_frames.py FRAMES_DIR OUT_DIR --limit 5
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "anonymise"))
import anonymise_frames as af  # noqa: E402

MODEL = "facebook/mask2former-swin-large-mapillary-vistas-semantic"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frames", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--view", type=int, default=768, help="pinhole view size in px")
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--yaws", type=float, nargs="+", default=[i * 45.0 for i in range(8)])
    ap.add_argument("--pitch", type=float, default=35.0, help="view pitch, positive = down")
    ap.add_argument("--erp-width", type=int, default=2048, help="width of the written label ERP")
    ap.add_argument("--names", nargs="*", help="only these frame file names")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="mps")
    args = ap.parse_args()

    import torch
    from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation

    proc = AutoImageProcessor.from_pretrained(args.model)
    net = Mask2FormerForUniversalSegmentation.from_pretrained(args.model).to(args.device).eval()
    id2label = net.config.id2label
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "labels.json").write_text(json.dumps({int(k): v for k, v in id2label.items()}, indent=1))

    frames = sorted(p for p in args.frames.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if args.names:
        frames = [p for p in frames if p.name in set(args.names)]
    if args.limit:
        frames = frames[: args.limit]
    af.VIEW, af.FOV = args.view, args.fov
    h0, w0 = cv2.imread(str(frames[0])).shape[:2]
    views = [af.view_maps(w0, h0, yaw, args.pitch) for yaw in args.yaws]
    ew, eh = args.erp_width, args.erp_width // 2
    # for every cell of the reduced ERP: the best view and where it lands in that view
    best = np.full((eh, ew), -1, np.int16)
    bz = np.full((eh, ew), -np.inf, np.float32)
    bu = np.zeros((eh, ew), np.float32)
    bv = np.zeros((eh, ew), np.float32)
    xs, ys = np.meshgrid(np.arange(ew) + 0.5, np.arange(eh) + 0.5)
    lon, lat = (xs / ew - 0.5) * 2 * math.pi, (ys / eh - 0.5) * math.pi
    d = np.stack([np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], -1)
    for k, (_, _, rot, f) in enumerate(views):
        v = d @ rot
        z = v[..., 2]
        u = f * v[..., 0] / np.maximum(z, 1e-3) + args.view / 2
        w = f * v[..., 1] / np.maximum(z, 1e-3) + args.view / 2
        ok = (z > 1e-3) & (u >= 0) & (u < args.view) & (w >= 0) & (w < args.view) & (z > bz)
        best[ok], bz[ok], bu[ok], bv[ok] = k, z[ok], u[ok], w[ok]
    ui, vi = bu.astype(np.int32).clip(0, args.view - 1), bv.astype(np.int32).clip(0, args.view - 1)

    rng = np.random.default_rng(0)
    palette = rng.integers(0, 255, (256, 3), dtype=np.uint8)
    t0 = time.time()
    for n, path in enumerate(frames):
        erp = cv2.imread(str(path))
        crops = [cv2.cvtColor(cv2.remap(erp, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP), cv2.COLOR_BGR2RGB)
                 for mx, my, _, _ in views]
        labels = []
        with torch.no_grad():
            for img in crops:
                inp = proc(images=img, return_tensors="pt").to(args.device)
                outp = net(**inp)
                seg = proc.post_process_semantic_segmentation(outp, target_sizes=[(args.view, args.view)])[0]
                labels.append(seg.to("cpu").numpy().astype(np.uint8))
        stack = np.stack(labels)
        lab = np.full((eh, ew), 255, np.uint8)
        cov = best >= 0
        lab[cov] = stack[best[cov], vi[cov], ui[cov]]
        cv2.imwrite(str(args.out / f"{path.stem}.png"), lab)
        if n < 3:
            small = cv2.resize(erp, (ew, eh), interpolation=cv2.INTER_AREA)
            prev = cv2.addWeighted(small, 0.45, palette[lab][..., ::-1], 0.55, 0)
            cv2.imwrite(str(args.out / f"{path.stem}.preview.jpg"), prev, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(json.dumps({"frame": path.name, "n": n + 1, "of": len(frames), "s_per_frame": round((time.time() - t0) / (n + 1), 1)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
