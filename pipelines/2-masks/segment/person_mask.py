#!/usr/bin/env python3
"""Per-frame ERP masks that hide every person, from Vistas labels (white = usable, black = masked).

A pixel is person when ANY label dir says so for that frame (PERSON_LIKE + EGO_VEHICLE by default). Person
components smaller than --min-component-px (at label resolution) are dropped as noise, the rest is upscaled
to the full frame size, grown by --buffer-px full-resolution pixels (disc, wrapped across the x=0 seam) and
removed from the output. --static-mask (white = keep) is ANDed into every output.

    pipelines/2-masks/segment/.venv/bin/python pipelines/2-masks/segment/person_mask.py \
        --labels LABELS_A [LABELS_B ...] --frames FRAMES_DIR --out OUT_DIR \
        [--static-mask helmet_mask.png] [--buffer-px 40] [--names frame0001.jpg ...] [--preview 6]

Inputs: `<labels dir>/<stem>.png` from segment_frames.py (uint8 Vistas ids, 255 = not covered, any ERP size),
`<frames dir>/*.jpg` for the frame names and full size.
Output per frame: `<OUT>/<frame file name>.png` (e.g. frame0001.jpg.png, the render_ground_ortho.py --mask-dir
convention), white = keep, black = masked. `<OUT>/person_mask.json` has the shares (fraction of pixels) per
frame and over frames, the arguments and the frames without any label file (static mask only).
With --preview N, `<OUT>/preview/<stem>.jpg` (1600 px wide, masked area tinted red) for N evenly spread frames.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

import rig_zone_mask as rz

PERSON_LIKE = rz.PERSON_LIKE
EGO_VEHICLE = rz.EGO_VEHICLE
DEFAULT_CLASSES = PERSON_LIKE + (EGO_VEHICLE,)
DEFAULT_BUFFER_PX = 40
DEFAULT_MIN_COMPONENT_PX = 30
UNLABELLED = 255
PROGRESS_EVERY = 50
PREVIEW_WIDTH = 1600


def label_person(stem: str, label_dirs: list[Path], classes, min_component_px: int) -> np.ndarray | None:
    """Person mask at the label grid: union over the label dirs, tiny components dropped.

    None when no label dir has a file for this frame.
    """
    person = None
    for d in label_dirs:
        p = d / f"{stem}.png"
        if not p.exists():
            continue
        lab = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if lab is None:
            raise SystemExit(f"cannot read label {p}")
        hit = np.isin(lab, classes) & (lab != UNLABELLED)
        if person is None:
            person = hit
        else:
            if hit.shape != person.shape:  # dirs at different ERP sizes: bring this one to the first grid
                hit = cv2.resize(hit.astype(np.uint8), (person.shape[1], person.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
            person |= hit
    if person is None:
        return None
    n, cc, stats, _ = cv2.connectedComponentsWithStats(person.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_component_px
    return keep[cc]


def grow(person: np.ndarray, buffer_px: int) -> np.ndarray:
    """Pixels within `buffer_px` of a person pixel (exact Euclidean disc, wrapped across the ERP seam in x)."""
    if buffer_px <= 0 or not person.any():
        return person
    pad = buffer_px
    inv = np.pad((~person).astype(np.uint8), ((0, 0), (pad, pad)), mode="wrap")
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return dist[:, pad:-pad] <= buffer_px


def load_keep(path: Path) -> np.ndarray:
    """uint8 keep mask from a static mask (white = keep), 255 = keep, 0 = masked."""
    return np.where(rz.load_static(path), 0, 255).astype(np.uint8)


def write_preview(frame: Path, out_mask: np.ndarray, dest: Path) -> None:
    img = cv2.imread(str(frame), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read frame {frame}")
    h, w = img.shape[:2]
    ph = round(h * PREVIEW_WIDTH / w)
    small = cv2.resize(img, (PREVIEW_WIDTH, ph), interpolation=cv2.INTER_AREA)
    masked = cv2.resize(out_mask, (PREVIEW_WIDTH, ph), interpolation=cv2.INTER_NEAREST) == 0
    small[masked] = (0.5 * small[masked] + 0.5 * np.array([0, 0, 255])).astype(np.uint8)
    cv2.imwrite(str(dest), small, [cv2.IMWRITE_JPEG_QUALITY, 85])


def _stats(vals: list[float]) -> dict:
    a = np.asarray(vals, dtype=np.float64)
    return {"median": round(float(np.median(a)), 6), "p95": round(float(np.percentile(a, 95)), 6), "max": round(float(a.max()), 6)}


def _jsonable(v):
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, nargs="+", required=True, help="label dirs from segment_frames.py; person if any dir says so")
    ap.add_argument("--frames", type=Path, required=True, help="JPEG frames (names and full size)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--static-mask", type=Path, help="white = keep, ANDed into every output (resized if needed)")
    ap.add_argument("--classes", type=int, nargs="+", default=list(DEFAULT_CLASSES), help="Vistas ids that count as person")
    ap.add_argument("--buffer-px", type=int, default=DEFAULT_BUFFER_PX, help="dilation in FULL-resolution pixels; 0 = off")
    ap.add_argument("--min-component-px", type=int, default=DEFAULT_MIN_COMPONENT_PX, help="drop person components smaller than this at label resolution")
    ap.add_argument("--names", nargs="*", help="only these frame file names")
    ap.add_argument("--preview", type=int, default=0, help="write N evenly spread previews to OUT/preview/")
    args = ap.parse_args(argv)

    for d in args.labels:
        if not d.is_dir():
            raise SystemExit(f"not a label folder: {d}")
    if not args.frames.is_dir():
        raise SystemExit(f"not a frame folder: {args.frames}")
    static = load_keep(args.static_mask) if args.static_mask is not None else None

    frames = sorted(p for p in args.frames.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if args.names:
        wanted = set(args.names)
        frames = [p for p in frames if p.name in wanted]
    if not frames:
        raise SystemExit(f"no frames to process in {args.frames}")

    labelled = [i for i, p in enumerate(frames) if any((d / f"{p.stem}.png").exists() for d in args.labels)]
    preview_idx: set[int] = set()
    if args.preview > 0 and labelled:
        picks = np.linspace(0, len(labelled) - 1, min(args.preview, len(labelled))).round().astype(int)
        preview_idx = {labelled[k] for k in picks}
    args.out.mkdir(parents=True, exist_ok=True)
    if preview_idx:
        (args.out / "preview").mkdir(parents=True, exist_ok=True)

    per_frame: list[dict] = []
    no_labels: list[str] = []
    for i, fp in enumerate(frames):
        gray = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise SystemExit(f"cannot read frame {fp}")
        h, w = gray.shape
        del gray
        keep = np.full((h, w), 255, np.uint8) if static is None else static
        if keep.shape != (h, w):
            keep = cv2.resize(keep, (w, h), interpolation=cv2.INTER_NEAREST)

        lab_person = label_person(fp.stem, args.labels, args.classes, args.min_component_px)
        if lab_person is None:
            no_labels.append(fp.name)
            person = np.zeros((h, w), bool)
        else:
            person_full = cv2.resize(lab_person.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
            person = grow(person_full, args.buffer_px)

        out = np.where(person, 0, keep).astype(np.uint8)
        cv2.imwrite(str(args.out / f"{fp.name}.png"), out)
        per_frame.append({
            "frame": fp.name,
            "person": round(np.count_nonzero(person) / person.size, 6),
            "total": round(np.count_nonzero(out == 0) / out.size, 6),
        })
        if i in preview_idx:
            write_preview(fp, out, args.out / "preview" / f"{fp.stem}.jpg")
        if (i + 1) % PROGRESS_EVERY == 0:
            print(json.dumps({"progress": i + 1, "of": len(frames), "frame": fp.name}), flush=True)

    report = {
        "shares": "fraction of pixels; person = masked by persons after the buffer, total = all black incl. static mask",
        "args": {k: _jsonable(v) for k, v in vars(args).items()},
        "frames": len(frames),
        "frames_without_labels": {"count": len(no_labels), "names": no_labels},
        "person_share": _stats([r["person"] for r in per_frame]),
        "total_share": _stats([r["total"] for r in per_frame]),
        "per_frame": per_frame,
    }
    (args.out / "person_mask.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({"wrote": len(frames), "out": str(args.out), "frames_without_labels": len(no_labels),
                      "person_share": report["person_share"], "total_share": report["total_share"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
