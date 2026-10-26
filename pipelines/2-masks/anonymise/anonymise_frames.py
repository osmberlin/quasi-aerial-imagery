#!/usr/bin/env python3
"""Blur faces and people and cover licence plates in equirectangular (ERP) frames.

Every frame is cut into pinhole views around the horizon, where faces and plates appear. Three
detectors run on each view: CenterFace (faces, via `deface`), a YOLOv9 licence-plate model (via
`open-image-models`) and YOLO person detection (via `ultralytics`). Each detection becomes a
mask in the view, the masks are mapped back onto the ERP frame, plates are filled with a flat
colour and faces and people are blurred. EXIF (including GPS) is copied from the source.

Detection is deliberately generous (low thresholds, padded boxes): a missed plate is worse than a
blurred patch of wall. Research and alternatives: pipelines/2-masks/anonymise/research.md.

    pipelines/2-masks/anonymise/.venv/bin/python pipelines/2-masks/anonymise/anonymise_frames.py IN_DIR OUT_DIR
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

VIEW = 1280
FOV = 90.0
YAWS = [i * 45.0 for i in range(8)]
PITCHES = [0.0]


def view_maps(width: int, height: int, yaw: float, pitch: float):
    """Pixel maps view -> ERP (to cut the view) and the view's rotation (to map masks back)."""
    f = VIEW / 2 / math.tan(math.radians(FOV) / 2)
    uu, vv = np.meshgrid(np.arange(VIEW) + 0.5, np.arange(VIEW) + 0.5)
    rays = np.stack([(uu - VIEW / 2) / f, (vv - VIEW / 2) / f, np.ones_like(uu)], -1)
    y, p = math.radians(yaw), math.radians(-pitch)
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    rot = ry @ rx  # view ray -> ERP camera ray (x right, y down, z forward)
    d = rays @ rot.T
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    lon, lat = np.arctan2(d[..., 0], d[..., 2]), np.arcsin(np.clip(d[..., 1], -1, 1))
    mx = ((lon / (2 * math.pi) + 0.5) * width).astype(np.float32)
    my = ((lat / math.pi + 0.5) * height).astype(np.float32)
    return mx, my, rot, f


def erp_to_view_maps(width: int, height: int, rot: np.ndarray, f: float, rows: slice):
    """For ERP pixels in `rows`: where they land in the view (or -1 when behind or outside)."""
    r0, r1 = rows.start, rows.stop
    xs, ys = np.meshgrid(np.arange(width) + 0.5, np.arange(r0, r1) + 0.5)
    lon = (xs / width - 0.5) * 2 * math.pi
    lat = (ys / height - 0.5) * math.pi
    d = np.stack([np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], -1)
    v = d @ rot  # ERP camera ray -> view ray (rot is orthonormal)
    front = v[..., 2] > 1e-3
    u = np.where(front, f * v[..., 0] / np.maximum(v[..., 2], 1e-3) + VIEW / 2, -1)
    w = np.where(front, f * v[..., 1] / np.maximum(v[..., 2], 1e-3) + VIEW / 2, -1)
    return u.astype(np.float32), w.astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--face-threshold", type=float, default=0.3)
    ap.add_argument("--plate-threshold", type=float, default=0.15)
    ap.add_argument("--person-threshold", type=float, default=0.25)
    ap.add_argument("--max-plate-px", type=float, default=700, help="reject plate boxes wider than this in a view")
    ap.add_argument("--pad", type=float, default=0.25, help="grow every box by this fraction on each side")
    ap.add_argument("--limit", type=int, default=0, help="only the first N frames (for testing)")
    ap.add_argument("--resume", action="store_true", help="skip frames already written to OUT_DIR")
    ap.add_argument("--pitches", type=float, nargs="+", default=PITCHES,
                    help="view pitches in degrees, positive = looking down (pass 3 used 35 for plates right next to the camera)")
    args = ap.parse_args()

    from deface.centerface import CenterFace
    from open_image_models import create_detector
    from ultralytics import YOLO

    faces = CenterFace(in_shape=(VIEW, VIEW), backend="onnxrt")
    plates = create_detector("yolo-v9-t-640-license-plate-end2end", conf_thresh=args.plate_threshold,
                             providers=["CPUExecutionProvider"])
    persons = YOLO("yolo11n.pt")

    frames = sorted(p for p in args.input.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if args.limit:
        frames = frames[: args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    first = cv2.imread(str(frames[0]))
    height, width = first.shape[:2]
    # ERP rows the views can touch: from 54 deg up to (lowest view pitch + 54) deg down
    low = min(0.5 + (max(args.pitches) + 54) / 180, 1.0)
    rows = slice(int(height * 0.20), int(height * low))
    views = []
    for yaw in YAWS:
        for pitch in args.pitches:
            mx, my, rot, f = view_maps(width, height, yaw, pitch)
            bu, bv = erp_to_view_maps(width, height, rot, f, rows)
            views.append((yaw, pitch, mx, my, bu, bv))

    stats = {"frames": 0, "faces": 0, "plates": 0, "plates_rejected": 0, "persons": 0, "masked_fraction_mean": 0.0}
    for k, path in enumerate(frames):
        if args.resume and (args.output / path.name).exists():
            continue
        erp = cv2.imread(str(path))
        blur_mask = np.zeros((height, width), np.uint8)
        fill_mask = np.zeros((height, width), np.uint8)
        for yaw, pitch, mx, my, bu, bv in views:
            view = cv2.remap(erp, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
            vb = np.zeros((VIEW, VIEW), np.uint8)
            vf = np.zeros((VIEW, VIEW), np.uint8)

            def box(mask, x1, y1, x2, y2):
                pw, ph = (x2 - x1) * args.pad, (y2 - y1) * args.pad
                cv2.rectangle(mask, (int(x1 - pw), int(y1 - ph)), (int(x2 + pw), int(y2 + ph)), 255, -1)

            dets, _ = faces(cv2.cvtColor(view, cv2.COLOR_BGR2RGB), threshold=args.face_threshold)
            for d in dets:
                box(vb, *d[:4])
            stats["faces"] += len(dets)
            for d in plates.predict(view):
                b = d.bounding_box
                w, h = b.x2 - b.x1, b.y2 - b.y1
                # Reject only boxes no plate can produce. A plate right next to the camera is several hundred
                # px wide, and seen obliquely it is nearly square: a first, stricter filter (max 280 px,
                # aspect >= 1.2) let readable plates through (R-104).
                if not (8 <= w <= args.max_plate_px and 0.7 <= w / max(h, 1) <= 9):
                    stats["plates_rejected"] += 1
                    continue
                box(vf, b.x1, b.y1, b.x2, b.y2)
                stats["plates"] += 1
            res = persons.predict(view, classes=[0], conf=args.person_threshold, verbose=False, imgsz=960)[0]
            for x1, y1, x2, y2 in res.boxes.xyxy.cpu().numpy():
                box(vb, x1, y1, x2, y2)
                stats["persons"] += 1
            if vb.any():
                blur_mask[rows] |= cv2.remap(vb, bu, bv, cv2.INTER_NEAREST, borderValue=0)
            if vf.any():
                fill_mask[rows] |= cv2.remap(vf, bu, bv, cv2.INTER_NEAREST, borderValue=0)
        out = erp
        if blur_mask.any():
            strong = cv2.GaussianBlur(cv2.resize(erp, (width // 8, height // 8), interpolation=cv2.INTER_AREA), (0, 0), 3)
            strong = cv2.resize(strong, (width, height), interpolation=cv2.INTER_LINEAR)
            out = np.where(blur_mask[..., None] > 0, strong, out)
        if fill_mask.any():
            n, labels = cv2.connectedComponents(fill_mask)
            for i in range(1, n):
                region = labels == i
                out[region] = out[region].mean(0).astype(np.uint8)
        dest = args.output / path.name
        cv2.imwrite(str(dest), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        stats["frames"] += 1
        stats["masked_fraction_mean"] += float(((blur_mask | fill_mask) > 0).mean())
        print(f"  {k + 1}/{len(frames)} {path.name}: faces+persons {int((blur_mask > 0).sum())} px, plates {int((fill_mask > 0).sum())} px", flush=True)

    subprocess.run(["exiftool", "-q", "-overwrite_original", "-TagsFromFile", f"{args.input}/%f.%e", "-all:all",
                    "-ext", "jpg", "-ext", "JPG", str(args.output)], check=False)
    stats["masked_fraction_mean"] = round(stats["masked_fraction_mean"] / max(stats["frames"], 1), 5)
    (args.output / "anonymisation.json").write_text(json.dumps(
        {"tool": "anonymise_frames.py", "detectors": ["deface CenterFace", "yolo-v9-t-640-license-plate-end2end", "ultralytics yolo11n person"],
         "thresholds": {"face": args.face_threshold, "plate": args.plate_threshold, "person": args.person_threshold}, "pad": args.pad,
         "views": {"yaws": YAWS, "pitches": list(args.pitches), "size": VIEW, "fov": FOV}, **stats}, indent=1))
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
