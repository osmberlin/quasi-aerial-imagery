#!/usr/bin/env python3
"""Look for licence plates that survived anonymisation.

Runs the plate detector again, at a low threshold, on the anonymised frames and writes a contact
sheet of every hit, so a person can confirm each one is a false alarm or a miss. Hits on the flat
fill of an already covered plate are skipped (uniform patches).

    pipelines/2-masks/anonymise/.venv/bin/python pipelines/2-masks/anonymise/check_residual_plates.py ANON_DIR SHEET.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymise_frames import VIEW, YAWS, PITCHES, view_maps  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("anon_dir", type=Path)
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--threshold", type=float, default=0.15)
    ap.add_argument("--pitches", type=float, nargs="+", default=[0.0, 35.0], help="view pitches, positive = down")
    args = ap.parse_args()
    from open_image_models import create_detector

    det = create_detector("yolo-v9-t-640-license-plate-end2end", conf_thresh=args.threshold, providers=["CPUExecutionProvider"])
    frames = sorted(p for p in args.anon_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    h, w = cv2.imread(str(frames[0])).shape[:2]
    maps = [view_maps(w, h, yaw, pitch)[:2] for yaw in YAWS for pitch in args.pitches]
    hits = []
    for path in frames:
        erp = cv2.imread(str(path))
        for mx, my in maps:
            view = cv2.remap(erp, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
            for d in det.predict(view):
                b = d.bounding_box
                x1, y1, x2, y2 = (int(max(v, 0)) for v in (b.x1, b.y1, b.x2, b.y2))
                patch = view[y1:y2, x1:x2]
                if patch.size == 0 or patch.reshape(-1, 3).std(0).max() < 4:  # our own flat fill
                    continue
                pad = 40
                crop = view[max(y1 - pad, 0):y2 + pad, max(x1 - pad, 0):x2 + pad].copy()
                cv2.rectangle(crop, (x1 - max(x1 - pad, 0), y1 - max(y1 - pad, 0)),
                              (x2 - max(x1 - pad, 0), y2 - max(y1 - pad, 0)), (0, 0, 255), 1)
                hits.append((path.name, round(d.confidence, 3), cv2.resize(crop, (200, int(200 * crop.shape[0] / max(crop.shape[1], 1))))))
    print(json.dumps({"frames": len(frames), "hits": len(hits), "list": [(n, c) for n, c, _ in hits][:200]}))
    if hits:
        tiles = []
        for name, conf, img in hits[:120]:
            t = np.zeros((170, 204, 3), np.uint8)
            t[: min(img.shape[0], 150), :200] = img[:150]
            cv2.putText(t, f"{name} {conf}", (2, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
            tiles.append(t)
        tiles += [np.zeros_like(tiles[0])] * ((-len(tiles)) % 6)
        sheet = np.vstack([np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)])
        cv2.imwrite(str(args.sheet), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return 0


if __name__ == "__main__":
    sys.exit(main())
