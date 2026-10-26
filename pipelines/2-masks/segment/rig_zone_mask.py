#!/usr/bin/env python3
"""Narrow per-frame mask for things that ride along with the camera, from Vistas labels.

The static helmet mask (white = keep) covers the helmet and the fixed rig. On top of it this adds only
what the segmentation labels as rig (rear-seat passenger, bag, basket, straps) AND that sits in a
thin zone directly above the static mask AND is connected to the static mask. Nothing above the zone,
nothing outside it, and ground classes (road, sidewalk, curb, markings, ...) are never masked: the
failed temporal-stability masks covered path edges and paving, which COLMAP needs.

    pipelines/2-masks/segment/.venv/bin/python pipelines/2-masks/segment/rig_zone_mask.py \
        --labels LABELS_DIR --static-mask helmet_mask.png --frames FRAMES_DIR --out OUT_DIR [--sky-out SKY_DIR]

Inputs: `<stem>.png` Vistas labels from segment_frames.py (uint8, 255 = not covered, any ERP size).
Output per frame: `<frame file name>.png` (e.g. frame0555.jpg.png, COLMAP mask_path convention),
white = keep, black = masked, at the size of the static mask.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# Vistas ids (see labels.json written by segment_frames.py)
PERSON_LIKE = (19, 20, 21, 22)  # Person, Bicyclist, Motorcyclist, Other Rider
EGO_VEHICLE = 64
# classes the passenger, bag and straps sometimes get on this rig (measured on the hand-drawn GT frames)
RIG_EXTRA = (63, 52, 53, 55, 57, 59, 62)  # Car Mount, Bicycle, Boat, Car, Motorcycle, Other Vehicle, Wheeled Slow
DEFAULT_CLASSES = PERSON_LIKE + (EGO_VEHICLE,) + RIG_EXTRA
# never masked, whatever the labels say
GROUND_CLASSES = (2, 7, 8, 9, 10, 11, 13, 14, 15, 23, 24, 29, 36, 41, 43)  # curb, lanes, road, sidewalk, markings, terrain, manhole...
SKY = 27

DEFAULT_ZONE_PX = 450
DEFAULT_BUFFER_PX = 18
DEFAULT_CLOSE_PX = 9
DEFAULT_TOUCH_PX = 8
DEFAULT_MAX_COMPONENT_PX = 250_000  # a passenger + strap blob is 60-110k px at full res; a car beside the rider is 400k+
DEFAULT_SKY_BUFFER_PX = 10


def _ellipse(radius: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def zone_tops(static: np.ndarray) -> np.ndarray:
    """First masked row per column (static: bool, True = masked). -1 where the column has no static pixel."""
    top = static.argmax(axis=0).astype(np.int32)
    top[~static.any(axis=0)] = -1
    return top


def zone_mask(static: np.ndarray, zone_px: int) -> np.ndarray:
    """Per column the `zone_px` rows directly above the static mask's upper boundary."""
    top = zone_tops(static)
    rows = np.arange(static.shape[0], dtype=np.int32)[:, None]
    return (top[None, :] >= 0) & (rows >= top[None, :] - zone_px) & (rows < top[None, :])


def upscale_labels(lab: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    return cv2.resize(lab, (w, h), interpolation=cv2.INTER_NEAREST)


def rig_additions(
    lab_full: np.ndarray,
    static: np.ndarray,
    zone: np.ndarray,
    classes=DEFAULT_CLASSES,
    ground_classes=GROUND_CLASSES,
    buffer_px: int = DEFAULT_BUFFER_PX,
    close_px: int = DEFAULT_CLOSE_PX,
    touch_px: int = DEFAULT_TOUCH_PX,
    max_component_px: int = DEFAULT_MAX_COMPONENT_PX,
    stats: dict | None = None,
) -> np.ndarray:
    """Pixels to add to the static mask (bool, full res). Always inside `zone`, outside `static`."""
    h, w = static.shape
    if not zone.any():
        return np.zeros_like(static)
    rows = np.flatnonzero(zone.any(axis=1))
    pad_r = buffer_px + close_px + touch_px + 4
    r0, r1 = max(0, rows[0] - pad_r), min(h, rows[-1] + 1 + pad_r)
    pad_c = 2 * pad_r + 8  # wrap across the x=0 seam

    def band(a: np.ndarray) -> np.ndarray:
        return np.pad(a[r0:r1], ((0, 0), (pad_c, pad_c)), mode="wrap")

    lab, st, zn = band(lab_full), band(static), band(zone)
    cand = (np.isin(lab, classes) & zn).astype(np.uint8)
    if close_px > 0:
        cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, _ellipse(close_px // 2))
    cand &= zn.astype(np.uint8)
    # keep only components that touch the static mask
    n, cc, cstats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    near_static = cv2.dilate(st.astype(np.uint8), _ellipse(touch_px)).astype(bool)
    keep_ids = np.unique(cc[(cc > 0) & near_static])
    too_big = keep_ids[(keep_ids > 0) & (cstats[keep_ids, cv2.CC_STAT_AREA] > max_component_px)] if max_component_px else keep_ids[:0]
    if stats is not None:
        stats["components_too_big"] = int(len(too_big))
    keep_ids = np.setdiff1d(keep_ids, too_big)
    kept = np.isin(cc, keep_ids[keep_ids > 0])
    if stats is not None:
        stats["components"] = int(n - 1)
        stats["components_kept"] = int((keep_ids > 0).sum())
    grown = cv2.dilate(kept.astype(np.uint8), _ellipse(buffer_px)).astype(bool) if buffer_px > 0 else kept
    protected = np.isin(lab, ground_classes)
    if stats is not None:
        stats["ground_px_before_protect"] = int((grown & zn & ~st & protected).sum()) // 1  # padded copies counted once below
    added = grown & zn & ~st & ~protected
    out = np.zeros_like(static)
    out[r0:r1] = added[:, pad_c : pad_c + w]
    if stats is not None:  # the padded count above double counts the pad columns; recompute on the real columns
        stats["ground_px_before_protect"] = int((grown & zn & ~st & protected)[:, pad_c : pad_c + w].sum())
    return out


def sky_mask(lab_full: np.ndarray, buffer_px: int = DEFAULT_SKY_BUFFER_PX) -> np.ndarray:
    """White = keep, black = sky (grown by `buffer_px` so the skyline itself is dropped too)."""
    sky = (lab_full == SKY).astype(np.uint8)
    if buffer_px > 0:
        pad = buffer_px + 2
        sky = np.pad(sky, ((0, 0), (pad, pad)), mode="wrap")
        sky = cv2.dilate(sky, _ellipse(buffer_px))[:, pad:-pad]
    return np.where(sky > 0, 0, 255).astype(np.uint8)


def load_static(path: Path) -> np.ndarray:
    m = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if m is None:
        raise SystemExit(f"cannot read static mask {path}")
    return m < 128


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--static-mask", type=Path, required=True)
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--zone-px", type=int, default=DEFAULT_ZONE_PX)
    ap.add_argument("--buffer-px", type=int, default=DEFAULT_BUFFER_PX)
    ap.add_argument("--close-px", type=int, default=DEFAULT_CLOSE_PX)
    ap.add_argument("--touch-px", type=int, default=DEFAULT_TOUCH_PX)
    ap.add_argument("--max-component-px", type=int, default=DEFAULT_MAX_COMPONENT_PX, help="drop components larger than this (a car next to the rider); 0 = off")
    ap.add_argument("--classes", type=int, nargs="+", default=list(DEFAULT_CLASSES), help="Vistas ids that count as rig")
    ap.add_argument("--sky-out", type=Path, help="also write the rig-zone mask combined with sky (white = keep, black = rig or sky)")
    ap.add_argument("--sky-only", action="store_true", help="with --sky-out: sky alone, without the rig-zone mask")
    ap.add_argument("--sky-buffer-px", type=int, default=DEFAULT_SKY_BUFFER_PX)
    ap.add_argument("--names", nargs="*", help="only these frame file names")
    args = ap.parse_args()

    static = load_static(args.static_mask)
    zone = zone_mask(static, args.zone_px)
    frames = sorted(p for p in args.frames.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    if args.names:
        frames = [p for p in frames if p.name in set(args.names)]
    args.out.mkdir(parents=True, exist_ok=True)
    if args.sky_out:
        args.sky_out.mkdir(parents=True, exist_ok=True)
    done = 0
    for p in frames:
        lp = args.labels / f"{p.stem}.png"
        if not lp.exists():
            continue
        lab = upscale_labels(cv2.imread(str(lp), cv2.IMREAD_GRAYSCALE), static.shape)
        added = rig_additions(lab, static, zone, args.classes, GROUND_CLASSES, args.buffer_px, args.close_px, args.touch_px, args.max_component_px)
        final = np.where(static | added, 0, 255).astype(np.uint8)
        cv2.imwrite(str(args.out / f"{p.name}.png"), final)
        if args.sky_out:
            sky = sky_mask(lab, args.sky_buffer_px)
            cv2.imwrite(str(args.sky_out / f"{p.name}.png"), sky if args.sky_only else np.minimum(sky, final))
        done += 1
    print(f"wrote {done} masks to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
