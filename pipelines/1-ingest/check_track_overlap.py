#!/usr/bin/env python3
"""Measure the overlap between the two halves of the side strips of a GoPro MAX / MAX 2 .360 track.

Each EAC track is [side strip | centre face | side strip]. A side strip holds one cube face as two
halves that were cut from different lenses; the halves show the same scene content a fixed number
of pixels apart (MAX 5.6K: 64 px, measured 2026-10-21). max2sphere blends the halves at that
offset, where offset = 2 * blend of its frame template. The template for the MAX 2 sizes is an
estimate, because the only MAX 2 sample had one lens covered. Run this on a capture with both
lenses uncovered and a textured scene to measure the offset:

  pipelines/.venv/bin/python pipelines/1-ingest/check_track_overlap.py SRC.360 [--time 30]

It prints the offset per strip and the blend to pass as `ingest_360_video.py --blend`. Strips
without texture or with a covered lens are reported as "no match".
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

# track size -> (strip width, template blend in max2sphere). Same table as the C source.
TEMPLATES = {(4096, 1344): (1376, 32), (2272, 736): (768, 16), (5888, 1920): (1984, 46),
             (3008, 960): (1024, 23)}
PATCH_W = 9
MAX_LAG = 200


def run(cmd):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as err:
        sys.exit(f"check_track_overlap: {cmd[0]} failed: {err.stderr.strip()[-600:]}")


def video_streams(src):
    out = run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,width,height",
               "-of", "json", src])
    vs = [s for s in json.loads(out.stdout)["streams"] if s.get("codec_type") == "video"]
    if len(vs) != 2:
        sys.exit(f"check_track_overlap: expected two video tracks, found {len(vs)}")
    return [s["index"] for s in vs], (vs[0]["width"], vs[0]["height"])


def measure_strip(strip, half, rng):
    """Offset d (pixels) with strip[:, x + d] ~ strip[:, x] for x just left of the cut at `half`.
    The patches are whole columns (the halves come from different lenses, so a small patch matches
    badly); a match counts if it beats the median cost over all lags clearly and the columns have
    texture."""
    gray = strip.astype(np.float32)
    h, w = gray.shape
    lags, costs = [], []
    for x in range(half - 80, half - PATCH_W, 3):
        patch = gray[:, x:x + PATCH_W]
        if patch.std() < 12:  # no texture, the match would be arbitrary
            continue
        ds = np.arange(half - x, min(MAX_LAG, w - PATCH_W - x))
        if ds.size < 30:
            continue
        cost = np.array([np.abs(patch - gray[:, x + d:x + d + PATCH_W]).mean() for d in ds])
        i = int(cost.argmin())
        if cost[i] < 0.6 * np.median(cost):
            lags.append(int(ds[i]))
            costs.append(float(cost[i]))
    return lags, costs


def main():
    p = argparse.ArgumentParser(description="Measure the strip half overlap of a GoPro MAX / MAX 2 .360 file.")
    p.add_argument("src", metavar="SRC.360")
    p.add_argument("--time", type=float, default=30.0, help="video second of the frame to use (default 30)")
    args = p.parse_args()
    streams, size = video_streams(args.src)
    if size not in TEMPLATES:
        sys.exit(f"check_track_overlap: track size {size[0]}x{size[1]} is not a known layout")
    strip_w, blend = TEMPLATES[size]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(streams):
            png = os.path.join(tmp, f"track{i}.png")
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(args.time), "-i",
                 args.src, "-map", f"0:{s}", "-frames:v", "1", png])
            img = np.asarray(Image.open(png).convert("L"))
            for name, x0 in (("left strip", 0), ("right strip", img.shape[1] - strip_w)):
                lags, costs = measure_strip(img[:, x0:x0 + strip_w], strip_w // 2, None)
                results.append((f"track 0:{s} {name}", lags, costs))
    print(f"track size {size[0]}x{size[1]}, strip width {strip_w}, template blend {blend} px "
          f"(expected offset {2 * blend} px)")
    found = []
    for name, lags, costs in results:
        if len(lags) < 6:
            print(f"  {name}: no match ({len(lags)} usable patches)")
            continue
        d = int(np.median(lags))
        spread = float(np.percentile(lags, 90) - np.percentile(lags, 10))
        print(f"  {name}: offset {d} px (patches {len(lags)}, 10-90 % spread {spread:.0f} px)")
        found.append(d)
    if found:
        d = int(np.median(found))
        print(f"offset {d} px -> blend {round(d / 2)}: use ingest_360_video.py --blend {round(d / 2)}"
              + ("" if round(d / 2) != blend else " (equals the template, no change needed)"))
    else:
        print("no strip gave a match: use a frame with both lenses uncovered and a textured scene "
              "close to the camera (try --time with another value)")


if __name__ == "__main__":
    main()
