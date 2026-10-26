#!/usr/bin/env python3
"""Ingest GoPro MAX 2 360 photos (.36P) into a frames/ folder that pipelines/run_scene.sh accepts.

A .36P file is a plain JPEG that is already equirectangular (MAX 2: 7680x3840), with GPS and
DateTimeOriginal in the EXIF. So nothing is stitched. For each photo this tool makes
OUT_DIR/frames/<name>.jpg, which is a symlink to the source (default) or a downscaled copy
(--width). run_scene.sh globs *.jpg. It also writes OUT_DIR/photos.csv and ingest.json with the GPS
spacing between consecutive photos.

The source folder is only read. Symlinks break when the card is ejected: use --copy or --width for
frames that must outlive the card.
"""

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys

import numpy as np

EARTH_RADIUS_M = 6371000.0
SUFFIXES = (".36p", ".jpg", ".jpeg")


def fail(msg):
    print(f"ingest_360_photos: error: {msg}", file=sys.stderr)
    sys.exit(1)


def haversine_m(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h))


def read_exif(paths):
    """One exiftool call. Returns {path: dict} with numeric GPS and the capture time as text."""
    out = subprocess.run(
        ["exiftool", "-json", "-n", "-Model", "-ImageWidth", "-ImageHeight", "-DateTimeOriginal",
         "-SubSecTimeOriginal", "-OffsetTimeOriginal", "-GPSLatitude", "-GPSLongitude",
         "-GPSAltitude", "-ProjectionType", *paths],
        check=True, capture_output=True, text=True)
    return {os.path.abspath(r["SourceFile"]): r for r in json.loads(out.stdout)}


def capture_seconds(rec):
    """DateTimeOriginal (+ sub-seconds, + offset) to Unix seconds, or None."""
    import calendar
    import datetime as dt
    text = rec.get("DateTimeOriginal")
    if not text:
        return None
    try:
        d = dt.datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    off = rec.get("OffsetTimeOriginal") or "+00:00"
    sign = -1 if off.startswith("-") else 1
    hh, _, mm = off.lstrip("+-").partition(":")
    shift = sign * (int(hh) * 3600 + int(mm or 0) * 60)
    sub = str(rec.get("SubSecTimeOriginal") or "0").strip()
    # GoPro writes 4 digits (5400); read them as a fraction of a second digit by digit
    frac = float("0." + sub) if sub.isdigit() else 0.0
    return calendar.timegm(d.timetuple()) - shift + frac


def downscale(src, dst, width):
    from PIL import Image
    with Image.open(src) as im:
        exif = im.info.get("exif")
        height = width // 2
        out = im.resize((width, height), Image.LANCZOS)
        kwargs = {"quality": 95, "subsampling": 0}
        if exif:
            kwargs["exif"] = exif
        out.save(dst, "JPEG", **kwargs)
    # the EXIF block still holds the original pixel size
    subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", f"-ExifImageWidth={width}",
                    f"-ExifImageHeight={height}", dst], check=True)


def main(argv=None):
    p = argparse.ArgumentParser(
        description=("GoPro MAX 2 360 photos (.36P, already equirectangular JPEG with EXIF GPS) to a "
                     "frames/ folder for pipelines/run_scene.sh, plus a GPS spacing summary."))
    p.add_argument("src", metavar="SRC", help="folder with .36P files (read only)")
    p.add_argument("out_dir", metavar="OUT_DIR", help="output folder, created if missing")
    p.add_argument("--width", type=int, default=None,
                   help="write downscaled JPEG copies of this width (height is half) with the EXIF "
                        "kept, instead of symlinks")
    p.add_argument("--copy", action="store_true", help="copy the files instead of symlinking")
    p.add_argument("--spacing", type=float, default=0.0, metavar="M",
                   help="keep a photo only if at least M metres from the last kept one (default: keep all)")
    args = p.parse_args(argv)

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out_dir)
    if not os.path.isdir(src):
        fail(f"no such folder: {src}")
    if shutil.which("exiftool") is None:
        fail("exiftool not on PATH")
    if args.width is not None and (args.width <= 0 or args.width % 2):
        fail("--width must be a positive even number")
    names = sorted(n for n in os.listdir(src) if n.lower().endswith(SUFFIXES) and not n.startswith("."))
    if not names:
        fail(f"no .36P or .jpg files in {src}")
    frames_dir = os.path.join(out, "frames")
    if os.path.isdir(frames_dir) and os.listdir(frames_dir):
        fail(f"{frames_dir} is not empty; remove or choose another OUT_DIR (existing output is never deleted)")
    os.makedirs(frames_dir, exist_ok=True)

    exif = read_exif([os.path.join(src, n) for n in names])
    rows, skipped = [], []
    last = None
    for n in names:
        path = os.path.join(src, n)
        rec = exif[path]
        lat, lon = rec.get("GPSLatitude"), rec.get("GPSLongitude")
        if lat is None or lon is None or (lat == 0 and lon == 0):
            skipped.append((n, "no GPS"))
            continue
        w, h = rec.get("ImageWidth"), rec.get("ImageHeight")
        if w != 2 * h:
            skipped.append((n, f"not 2:1 ({w}x{h})"))
            continue
        if args.spacing > 0 and last is not None \
                and float(haversine_m(last[0], last[1], lat, lon)) < args.spacing:
            continue
        last = (lat, lon)
        rows.append({"name": n, "lat": lat, "lon": lon, "alt": rec.get("GPSAltitude"),
                     "t": capture_seconds(rec), "w": w, "h": h, "model": rec.get("Model", "")})
    if not rows:
        fail("no usable photos (every file lacks GPS or is not equirectangular)")

    for r in rows:
        link = os.path.join(frames_dir, os.path.splitext(r["name"])[0] + ".jpg")
        path = os.path.join(src, r["name"])
        if args.width:
            downscale(path, link, args.width)
        elif args.copy:
            shutil.copy2(path, link)
        else:
            os.symlink(path, link)

    lat = np.array([r["lat"] for r in rows])
    lon = np.array([r["lon"] for r in rows])
    gaps = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:]) if len(rows) > 1 else np.array([])
    times = [r["t"] for r in rows]
    dts = np.array([b - a for a, b in zip(times[:-1], times[1:])]) if all(t is not None for t in times) else np.array([])
    with open(os.path.join(out, "photos.csv"), "w") as f:
        f.write("name,lat,lon,alt_m,capture_unix_s,gap_m_from_previous\n")
        for i, r in enumerate(rows):
            gap = "" if i == 0 else f"{gaps[i - 1]:.2f}"
            alt = "" if r["alt"] is None else f"{r['alt']:.1f}"
            t = "" if r["t"] is None else f"{r['t']:.3f}"
            f.write(f"{r['name']},{r['lat']:.8f},{r['lon']:.8f},{alt},{t},{gap}\n")

    def stats(a):
        if not a.size:
            return None
        return {"median": round(float(statistics.median(a)), 2), "min": round(float(a.min()), 2),
                "max": round(float(a.max()), 2)}

    speed = gaps / dts if gaps.size and dts.size == gaps.size and (dts > 0).all() else np.array([])
    summary = {
        "source": src,
        "out_dir": out,
        "frames_dir": frames_dir,
        "mode": f"downscaled copy {args.width}x{args.width // 2}" if args.width
                else ("copy" if args.copy else "symlink"),
        "photos": len(rows),
        "skipped": [{"file": n, "reason": why} for n, why in skipped],
        "model": sorted({r["model"] for r in rows}),
        "source_size_px": sorted({f"{r['w']}x{r['h']}" for r in rows}),
        "gps_spacing_m": stats(gaps),
        "interval_s": stats(dts),
        "speed_ms": stats(speed),
        "path_length_m": round(float(gaps.sum()), 1),
        "gaps_over_10m": int((gaps > 10).sum()) if gaps.size else 0,
    }
    with open(os.path.join(out, "ingest.json"), "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
