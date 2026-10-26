#!/usr/bin/env python3
"""Ingest a GoPro MAX or MAX 2 .360 video into GPS-tagged equirectangular (ERP) frames.

Steps (method and numbers: pipelines/1-ingest/video-to-erp.md):
  1. GPS track: exiftool -ee reads the GPMF GPS samples (normal video: about 18 per GPMF packet,
     one packet per second; time-lapse: one packet and one sample per video frame); ffprobe gives
     the packet times of the GPMF stream. Writes OUT_DIR/gps_samples.csv.
  2. Candidate frames at k/FPS seconds of video time get GPS by linear interpolation. Greedy
     thinning keeps a candidate only if it is at least SPACING metres from the last kept one.
     Writes OUT_DIR/selected_frames.csv. A time-lapse (video time runs about 15x slower than
     capture time) is detected from the GPS clock; every video frame is then a candidate.
  3. ffmpeg decodes both EAC tracks (the two hvc1 video streams; indices found with ffprobe) in
     one pass and writes only the kept candidates. trek-view max2sphere (patched with the MAX 2
     frame sizes, see pipelines/1-ingest/max2sphere) stitches each pair to one ERP JPEG.
  4. One batched exiftool call writes GPS and capture-time EXIF into all frames.
  5. Intermediate files are removed unless --keep-candidates is given. A JSON summary goes to
     stdout and to OUT_DIR/ingest.json.

Frame names: OUT_DIR/frames/frameNNNN.jpg, NNNN = 1 + candidate index k, so frame0001 is t = 0.
This is the numbering of the earlier 2 fps frame sets (ffmpeg fps filter output, 1-based).
"""

import argparse
import calendar
import datetime as dt
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# max2sphere binary: $MAX2SPHERE, else <workspace>/third-party/max2sphere/max2sphere (the folder that
# contains this repository), else max2sphere on PATH. Build it with the patch in max2sphere/.
_WS_MAX2SPHERE = Path(__file__).resolve().parents[3] / "third-party" / "max2sphere" / "max2sphere"
DEFAULT_MAX2SPHERE = os.environ.get("MAX2SPHERE") or (
    str(_WS_MAX2SPHERE) if _WS_MAX2SPHERE.exists() else "max2sphere")
TRACK_SLOTS = (0, 5)  # max2sphere reads track0/ and track5/; the stream indices differ by camera
# Track sizes max2sphere has a frame template for (width, height) -> (label, native ERP width,
# default ERP width). Default width: 5376 for 5.6K and 8K tracks (same memory and disk budget as
# the MAX 1 pipeline; 8K native is 7680), native for the small 3K mode.
LAYOUTS = {
    (4096, 1344): ("5.6K (MAX, MAX 2)", 5376, 5376),
    (2272, 736): ("2.7K (MAX)", 2944, 2944),
    (5888, 1920): ("8K (MAX 2)", 7680, 5376),
    (3008, 960): ("3K (MAX 2)", 3840, 3840),
}
GPMF_PACKET_S = 1.001  # GPMF packets sit on a 1.001 s grid (measured, see research doc)
TIMELAPSE_FACTOR = 1.5  # capture time / video time above this: time-lapse (measured 14.9 on a 0.5 s time-lapse)
EARTH_RADIUS_M = 6371000.0
PER_PACKET_TAGS = ("GPSHPositioningError", "GPSMeasureMode", "GPSDateTime")
PER_SAMPLE_TAGS = ("GPSLatitude", "GPSLongitude", "GPSAltitude", "GPSSpeed")
GPMF_LINE = re.compile(r"^\[Doc(\d+)(?:-(\d+))?\]\s+(\w+)\s*:\s*(.*)$")
GPS_CSV_FIELDS = ["t_s", "doc", "idx", "lat", "lon", "alt_m", "hpe_m", "measure_mode",
                  "speed_kmh", "gps_datetime"]  # exiftool GPSSpeed is km/h (checked against positions)
SELECTED_CSV_FIELDS = ["frame_index", "cand_index", "t_s", "lat", "lon", "alt_m", "utc"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def fail(msg):
    print(f"ingest_360_video: error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    """Run a command with captured output; on failure stop with the tail of stderr."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as err:
        fail(f"{cmd[0]} exited {err.returncode}: {err.stderr.strip()[-800:]}")


def haversine_m(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h))


def num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


def gps_epoch(text):
    """'2026:03:19 14:57:09.524' (UTC, as exiftool prints GPSDateTime) to Unix seconds."""
    for fmt in ("%Y:%m:%d %H:%M:%S.%f", "%Y:%m:%d %H:%M:%S"):
        try:
            d = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return calendar.timegm(d.timetuple()) + d.microsecond / 1e6
    return None


def exif_time(epoch):
    """Unix seconds to EXIF DateTimeOriginal and SubSecTimeOriginal (UTC)."""
    sec = math.floor(epoch)
    ms = int(round((epoch - sec) * 1000))
    if ms == 1000:
        sec, ms = sec + 1, 0
    stamp = dt.datetime.fromtimestamp(sec, dt.timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
    return stamp, f"{ms:03d}"


def parse_rate(text):
    try:
        a, _, b = text.partition("/")
        return float(a) / float(b) if b else float(a)
    except (ValueError, ZeroDivisionError):
        return math.nan


def probe(src):
    """Container and track facts. The two EAC tracks are the two video streams (MAX: 0:0 and 0:5,
    MAX 2: 0:0 and 0:3 for a time-lapse or 0:4 for a normal video), found by type and not by index."""
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=index,codec_type,codec_name,codec_tag_string,width,height,"
               "avg_frame_rate,nb_frames:stream_disposition=attached_pic",
               "-of", "json", src])
    info = json.loads(out.stdout)
    gpmd = [s["index"] for s in info["streams"] if s.get("codec_tag_string") == "gpmd"]
    if len(gpmd) != 1:
        fail(f"expected exactly one GPMF (gpmd) stream, found {len(gpmd)}")
    video = [s for s in info["streams"] if s.get("codec_type") == "video"
             and not s.get("disposition", {}).get("attached_pic")]
    if len(video) != 2:
        fail(f"expected exactly two video tracks, found {len(video)}; not a GoPro MAX .360 layout")
    dims = {(s["width"], s["height"]) for s in video}
    if len(dims) != 1:
        fail(f"the two EAC tracks differ in size: {sorted(dims)}")
    size = dims.pop()
    if size not in LAYOUTS:
        known = ", ".join(f"{w}x{h}" for w, h in LAYOUTS)
        fail(f"track size {size[0]}x{size[1]} has no max2sphere frame template (known: {known})")
    frames = [int(s["nb_frames"]) for s in video if str(s.get("nb_frames", "")).isdigit()]
    model = run(["exiftool", "-s3", "-Model", src]).stdout.strip() or "unknown"
    return {
        "duration": float(info["format"]["duration"]),
        "gpmd": gpmd[0],
        "streams": [s["index"] for s in video],
        "size": size,
        "fps": parse_rate(video[0].get("avg_frame_rate", "")),
        "nb_frames": min(frames) if frames else None,
        "model": model,
    }


def gpmf_packet_times(src, stream):
    out = run(["ffprobe", "-v", "error", "-select_streams", str(stream),
               "-show_entries", "packet=pts_time", "-of", "csv=p=0", src])
    times = []
    for line in out.stdout.splitlines():
        if line.strip():
            value = line.split(",")[0].strip()
            if value == "N/A":
                fail("GPMF packet without pts_time")
            times.append(float(value))
    return times


def read_gpmf(src, packet_pts):
    """GPS samples from exiftool -ee. Sample j of packet k is at pts_k + j * 1.001 / n_k."""
    out = run(["exiftool", "-ee", "-n", "-G3", "-s", "-GPSLatitude", "-GPSLongitude",
               "-GPSAltitude", "-GPSHPositioningError", "-GPSMeasureMode", "-GPSSpeed",
               "-GPSDateTime", src])
    docs = {}
    for line in out.stdout.splitlines():
        m = GPMF_LINE.match(line.rstrip())
        if not m:
            continue
        doc, idx, tag, value = int(m.group(1)), int(m.group(2) or 0), m.group(3), m.group(4).strip()
        entry = docs.setdefault(doc, {"packet": {}, "samples": {}})
        if tag in PER_PACKET_TAGS:
            entry["packet"][tag] = value
        elif tag in PER_SAMPLE_TAGS:
            entry["samples"].setdefault(idx, {})[tag] = value

    rows = []
    pkt_t, pkt_epoch = [], []
    skipped_packets = 0
    for doc in sorted(docs):
        if not 1 <= doc <= len(packet_pts):
            skipped_packets += 1
            continue
        packet, samples = docs[doc]["packet"], docs[doc]["samples"]
        t0 = packet_pts[doc - 1]
        epoch = gps_epoch(packet["GPSDateTime"]) if "GPSDateTime" in packet else None
        # packets before the first fix carry a wrong date (2021 on the MAX 2) and wrong positions
        if epoch is not None and str(packet.get("GPSMeasureMode", "")) == "3":
            pkt_t.append(t0)
            pkt_epoch.append(epoch)
        n = max(len(samples), 1)
        for idx in sorted(samples):
            s = samples[idx]
            lat, lon = num(s.get("GPSLatitude")), num(s.get("GPSLongitude"))
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            rows.append({
                "t_s": t0 + idx * GPMF_PACKET_S / n,
                "doc": doc,
                "idx": idx,
                "lat": lat,
                "lon": lon,
                "alt": num(s.get("GPSAltitude")),
                "hpe": num(packet.get("GPSHPositioningError")),
                "mode": packet.get("GPSMeasureMode", ""),
                "speed": num(s.get("GPSSpeed")),
                "gps_datetime": packet.get("GPSDateTime", ""),
            })
    if skipped_packets:
        log(f"warning: {skipped_packets} GPMF packets had no matching packet time, skipped")
    rows.sort(key=lambda r: r["t_s"])
    return rows, np.array(pkt_t), np.array(pkt_epoch)


def write_gps_csv(path, rows):
    with open(path, "w", newline="") as f:
        f.write(",".join(GPS_CSV_FIELDS) + "\n")
        for r in rows:
            vals = [f"{r['t_s']:.4f}", r["doc"], r["idx"], f"{r['lat']:.8f}", f"{r['lon']:.8f}",
                    "" if not math.isfinite(r["alt"]) else f"{r['alt']:.3f}",
                    "" if not math.isfinite(r["hpe"]) else f"{r['hpe']:.2f}",
                    r["mode"],
                    "" if not math.isfinite(r["speed"]) else f"{r['speed']:.4f}",
                    r["gps_datetime"]]
            f.write(",".join(str(v) for v in vals) + "\n")


class Track:
    """Linear interpolation of the GPS track in time."""

    def __init__(self, rows):
        self.t = np.array([r["t_s"] for r in rows])
        self.lat = np.array([r["lat"] for r in rows])
        self.lon = np.array([r["lon"] for r in rows])
        alt = np.array([r["alt"] for r in rows])
        ok = np.isfinite(alt)
        self.alt_t, self.alt = self.t[ok], alt[ok]
        sp = np.array([r["speed"] for r in rows])
        ok = np.isfinite(sp)
        self.speed_t, self.speed = self.t[ok], sp[ok]

    def speed_at(self, q, half_window):
        """Median GPS speed (km/h, from the GPMF GPSSpeed) within +-half_window video seconds."""
        if not self.speed.size:
            return np.full_like(q, math.nan)
        lo = np.searchsorted(self.speed_t, q - half_window)
        hi = np.searchsorted(self.speed_t, q + half_window, side="right")
        return np.array([np.median(self.speed[a:b]) if b > a else math.nan for a, b in zip(lo, hi)])

    def at(self, q):
        lat = np.interp(q, self.t, self.lat)
        lon = np.interp(q, self.t, self.lon)
        if self.alt.size:
            alt = np.interp(q, self.alt_t, self.alt)
        else:
            alt = np.full_like(q, math.nan)
        return lat, lon, alt


def select_candidates(track, fps, first, n, spacing, min_speed=0.0, half_window=1.0):
    """Candidates k/fps for first <= k < n (q and the position arrays are indexed by k). Skip a
    candidate while the median GPS speed around it is below min_speed (km/h): at a stop the rider
    and bike stand in view and GPS jitter would pass the spacing test. Keep one if >= spacing from
    the last kept."""
    q = np.arange(n) / fps
    lat, lon, alt = track.at(q)
    slow = track.speed_at(q, half_window) < min_speed if min_speed > 0 else np.zeros(n, bool)
    kept = []
    last = None
    for k in range(first, n):
        if slow[k]:
            continue
        if last is None or float(haversine_m(last[0], last[1], lat[k], lon[k])) >= spacing:
            kept.append(k)
            last = (lat[k], lon[k])
    return q, lat, lon, alt, kept


def capture_clock(pkt_t, pkt_epoch):
    """Least-squares line epoch = a + b * video_time through the GPMF packets. b is the number of
    capture seconds per video second: about 1 for a normal video, about 15 for a 0.5 s time-lapse."""
    if pkt_t.size < 2:
        return None
    b, a = np.polyfit(pkt_t, pkt_epoch, 1)
    return float(a), float(b)


SELECT_CHUNK = 40  # ffmpeg rejects select expressions much longer than about 120 terms


def extract_candidates(src, streams, fps, native, kept, dirs, stop=None):
    """One ffmpeg process decodes the input once and feeds one output per (track, chunk of kept
    candidates). fps= defines the candidate slots (same as the earlier frame sets) unless the
    candidate rate is the native frame rate (time-lapse: every frame), where candidate k is source
    frame k; select keeps candidate k where n == k. Output files are then renumbered 1..len(kept)
    per track in candidate order; the caller maps them back to candidate indices.
    streams: the two stream indices, dirs: output folder per stream index, stop: last video second
    needed (decoding ends one second later)."""
    chunks = [kept[i:i + SELECT_CHUNK] for i in range(0, len(kept), SELECT_CHUNK)]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if stop is not None:  # input duration limit: stop decoding after the window, n is unchanged
        cmd += ["-t", f"{stop:.3f}"]
    # few threads: with default threading an 8K MAX 2 decode with a dozen outputs peaked at 9 GB
    cmd += ["-threads", "2", "-filter_threads", "1", "-i", src]
    fps_filter = "" if native else f"fps={fps:g},"
    for s in streams:
        for j, chunk in enumerate(chunks):
            expr = "+".join(f"eq(n\\,{k})" for k in chunk)
            part = os.path.join(dirs[s], f"part{j:02d}")
            os.makedirs(part)
            cmd += ["-map", f"0:{s}", "-vf", f"{fps_filter}select='{expr}'",
                    "-fps_mode", "passthrough", "-q:v", "1", "-threads", "1", os.path.join(part, "frame%04d.jpg")]
    run(cmd)
    for s in streams:
        seq = 0
        for j, chunk in enumerate(chunks):
            part = os.path.join(dirs[s], f"part{j:02d}")
            names = sorted(os.listdir(part))
            if len(names) != len(chunk):
                fail(f"ffmpeg wrote {len(names)} frames for track 0:{s} chunk {j}, expected {len(chunk)}")
            for name in names:
                seq += 1
                os.replace(os.path.join(part, name), os.path.join(dirs[s], f"frame{seq:04d}.jpg"))
            os.rmdir(part)
        if seq != len(kept):
            fail(f"ffmpeg wrote {seq} frames for track 0:{s}, expected {len(kept)}")


def stitch(max2sphere, cand_root, count, width, cache_dir, stitched_dir, log_path, blend=None):
    """max2sphere writes its lookup table (about 700 MB at width 5376, 1.4 GB at 7680) into the
    working directory. blend overrides the overlap blend of the frame template (MAX2SPHERE_BLEND)."""
    env = dict(os.environ)
    if blend:
        env["MAX2SPHERE_BLEND"] = str(blend)
    cmd = [max2sphere, "-w", str(width), "-n", "1", "-m", str(count),
           "-o", os.path.join(stitched_dir, "frame%04d.jpg"),
           os.path.join(cand_root, "track%d", "frame%04d.jpg")]
    with open(log_path, "w") as log_file:
        log_file.write("$ " + " ".join(cmd) + "\n")
        log_file.flush()
        rc = subprocess.run(cmd, cwd=cache_dir, env=env, stdout=log_file,
                            stderr=subprocess.STDOUT).returncode
    if rc != 0:
        fail(f"max2sphere exited with {rc}, see {log_path}")
    produced = sorted(os.listdir(stitched_dir))
    expected = [f"frame{i:04d}.jpg" for i in range(1, count + 1)]
    if produced != expected:
        fail(f"max2sphere produced {len(produced)} frames, expected {count}; see {log_path}")


def write_exif(frames, out_dir):
    """One exiftool call for all frames, using an argument file with -execute per frame."""
    lines = []
    for f in frames:
        lines += ["-overwrite_original",
                  f"-GPSLatitude={abs(f['lat']):.8f}", f"-GPSLatitudeRef={'N' if f['lat'] >= 0 else 'S'}",
                  f"-GPSLongitude={abs(f['lon']):.8f}", f"-GPSLongitudeRef={'E' if f['lon'] >= 0 else 'W'}"]
        if math.isfinite(f["alt"]):
            lines += [f"-GPSAltitude={abs(f['alt']):.2f}", f"-GPSAltitudeRef={0 if f['alt'] >= 0 else 1}"]
        if f["utc"] is not None:
            stamp, ms = exif_time(f["utc"])
            lines += [f"-DateTimeOriginal={stamp}", f"-SubSecTimeOriginal={ms}",
                      "-OffsetTimeOriginal=+00:00"]
        lines += [f["path"], "-execute"]
    args_path = os.path.join(out_dir, ".exif_args.txt")
    with open(args_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    res = run(["exiftool", "-@", args_path])
    os.remove(args_path)
    updated = sum(int(m.group(1)) for m in re.finditer(r"(\d+) image files? updated", res.stdout))
    if updated != len(frames):
        fail(f"exiftool updated {updated} files, expected {len(frames)}")


def image_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "csv=s=x:p=0", path])
    return out.stdout.strip()


def main(argv=None):
    p = argparse.ArgumentParser(
        description=("Ingest a GoPro MAX or MAX 2 .360 video (normal or time-lapse): GPS track from "
                     "GPMF, candidate frames thinned to a minimum GPS spacing, both EAC tracks "
                     "stitched to equirectangular JPEGs with max2sphere, GPS and capture-time EXIF "
                     "written in one batch."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Output: OUT_DIR/frames/frameNNNN.jpg (NNNN = 1 + candidate index, t = (NNNN-1)/FPS s "
                "of video time), gps_samples.csv, selected_frames.csv, ingest.json, max2sphere.log.\n"
                "Run with pipelines/.venv/bin/python (needs numpy)."))
    p.add_argument("src", metavar="SRC.360", help="GoPro MAX or MAX 2 .360 video")
    p.add_argument("out_dir", metavar="OUT_DIR", help="output folder, created if missing")
    p.add_argument("--spacing", type=float, default=1.5, metavar="M",
                   help="keep consecutive frames at least M metres apart by GPS (default 1.5)")
    p.add_argument("--min-speed", type=float, default=3.0, metavar="KMH",
                   help="skip frames while the GPS speed is below KMH km/h, i.e. stops (default 3; 0 keeps all)")
    p.add_argument("--fps", type=float, default=None,
                   help="candidate rate in frames per second of video time (default: 2 for a "
                        "normal video, the native frame rate for a time-lapse)")
    p.add_argument("--timelapse", choices=("auto", "yes", "no"), default="auto",
                   help="time-lapse handling; auto compares the GPS clock with the video clock "
                        "(default auto)")
    p.add_argument("--start", type=float, default=0.0, metavar="S",
                   help="first video second to consider (default 0)")
    p.add_argument("--end", type=float, default=None, metavar="S",
                   help="last video second to consider (default: end of the video)")
    p.add_argument("--max2sphere", default=DEFAULT_MAX2SPHERE, metavar="PATH",
                   help="max2sphere binary, built with the MAX 2 templates (default: %(default)s)")
    p.add_argument("--width", type=int, default=None,
                   help="ERP width in pixels; height is half (default 5376 for 5.6K and 8K tracks, "
                        "3840 for 3K tracks; 8K native is 7680)")
    p.add_argument("--blend", type=int, default=None, metavar="PX",
                   help="overlap blend of the max2sphere frame template in pixels (MAX 2 8K: 46 and 3K: "
                        "23 are estimates; measure with check_track_overlap.py on a two-lens capture)")
    p.add_argument("--keep-candidates", action="store_true",
                   help="keep the per-track candidate JPEGs and the max2sphere lookup cache")
    args = p.parse_args(argv)

    if args.spacing <= 0 or (args.fps is not None and args.fps <= 0) \
            or (args.width is not None and args.width <= 0):
        fail("--spacing, --fps and --width must be positive")
    if args.start < 0 or (args.end is not None and args.end <= args.start):
        fail("--start must be >= 0 and --end greater than --start")
    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out_dir)
    if not os.path.isfile(src):
        fail(f"no such file: {src}")
    if not (os.path.isfile(args.max2sphere) and os.access(args.max2sphere, os.X_OK)):
        fail(f"max2sphere not found or not executable: {args.max2sphere}")
    for tool in ("ffmpeg", "ffprobe", "exiftool"):
        if shutil.which(tool) is None:
            fail(f"{tool} not on PATH")

    frames_dir = os.path.join(out, "frames")
    cand_root = os.path.join(out, "candidates")
    cache_dir = os.path.join(out, ".cache")
    stitched_dir = os.path.join(out, ".stitched")
    for d in (frames_dir, cand_root, cache_dir, stitched_dir):
        if os.path.isdir(d) and os.listdir(d):
            fail(f"{d} is not empty; remove or choose another OUT_DIR (existing output is never deleted)")
    os.makedirs(out, exist_ok=True)
    started = time.time()

    log(f"probing {os.path.basename(src)}")
    info = probe(src)
    duration, streams = info["duration"], info["streams"]
    src_w, src_h = info["size"]
    layout_name, native_width, default_width = LAYOUTS[info["size"]]
    width = args.width or default_width
    log(f"camera {info['model']}, tracks 0:{streams[0]} and 0:{streams[1]} {src_w}x{src_h} "
        f"({layout_name}), {info['fps']:.3f} fps, {info['nb_frames']} frames")
    packet_pts = gpmf_packet_times(src, info["gpmd"])
    log(f"reading GPMF GPS (stream 0:{info['gpmd']}, {len(packet_pts)} packets)")
    rows, pkt_t, pkt_epoch = read_gpmf(src, packet_pts)
    if len(rows) < 2:
        fail(f"only {len(rows)} GPS fixes found in the GPMF track")
    not_3d = sum(1 for r in rows if r["mode"] != "3")
    write_gps_csv(os.path.join(out, "gps_samples.csv"), rows)
    log(f"GPS fixes: {len(rows)} ({not_3d} not 3D fixes)")
    if not_3d > len(rows) / 2:
        log("warning: most GPS fixes are not 3D fixes (no sky view?); positions are unreliable")
    elif not_3d:
        rows = [r for r in rows if str(r["mode"]) == "3"]
        log(f"dropped {not_3d} samples without a 3D fix; frames before {rows[0]['t_s']:.1f} s video time "
            f"and after {rows[-1]['t_s']:.1f} s are not used")

    clock = capture_clock(pkt_t, pkt_epoch)
    speed_factor = clock[1] if clock else math.nan
    if args.timelapse == "auto":
        timelapse = bool(clock) and speed_factor > TIMELAPSE_FACTOR
    else:
        timelapse = args.timelapse == "yes"
        if timelapse and not clock:
            fail("--timelapse yes needs GPSDateTime in the GPMF packets to know the capture time")
    vfps = info["fps"]
    fps = args.fps if args.fps is not None else (vfps if timelapse else 2.0)
    native = abs(fps - vfps) < 1e-3 * vfps
    if timelapse and not native:
        log(f"warning: time-lapse with --fps {fps:g}, not the native {vfps:.3f}: skipping video frames")
    if timelapse:
        interval = speed_factor / vfps
        log(f"time-lapse: {speed_factor:.2f} capture s per video s, {interval:.3f} s between frames")

    track = Track(rows)
    # last candidate: a time-lapse has one candidate per source frame (n == frame index)
    if native and info["nb_frames"]:
        n_total = info["nb_frames"]
    else:
        n_total = int(math.floor(duration * fps + 1e-9)) + 1
    first = int(math.ceil(max(args.start, track.t[0]) * fps - 1e-9))
    n_total = min(n_total, int(math.floor(track.t[-1] * fps + 1e-9)) + 1)
    if args.end is not None:
        n_total = min(n_total, int(math.floor(args.end * fps + 1e-9)) + 1)
    if first >= n_total:
        fail("the --start/--end window holds no frames")
    # +-2 s of capture time around each candidate (video time runs slower in a time-lapse)
    half = 2.0 / (speed_factor if timelapse else 1.0)
    q, lat, lon, alt, kept = select_candidates(track, fps, first, n_total, args.spacing, args.min_speed, half)
    if args.min_speed > 0:
        log(f"stops: candidates below {args.min_speed:g} km/h (median over +-2 s) skipped")
    if not kept:
        fail("no candidate frames selected")
    log(f"candidates: {n_total - first} at {fps:g} fps, kept {len(kept)} at >= {args.spacing:g} m")

    if timelapse:
        # GPSDateTime of a time-lapse packet jitters by +-0.1 s, so use the fitted clock
        utc = clock[0] + clock[1] * q
    elif pkt_t.size >= 2:
        utc = np.interp(q, pkt_t, pkt_epoch)
    else:
        utc = np.full_like(q, math.nan)
    selected = []
    for seq, k in enumerate(kept, start=1):
        selected.append({
            "frame_index": k + 1, "cand_index": k, "t_s": float(q[k]),
            "lat": float(lat[k]), "lon": float(lon[k]), "alt": float(alt[k]),
            "utc": float(utc[k]) if math.isfinite(utc[k]) else None,
        })
    with open(os.path.join(out, "selected_frames.csv"), "w", newline="") as f:
        f.write(",".join(SELECTED_CSV_FIELDS) + "\n")
        for s in selected:
            stamp = "" if s["utc"] is None else " ".join(exif_time(s["utc"]))
            alt_text = f"{s['alt']:.2f}" if math.isfinite(s["alt"]) else ""
            f.write(f"{s['frame_index']},{s['cand_index']},{s['t_s']:.3f},{s['lat']:.8f},"
                    f"{s['lon']:.8f},{alt_text},{stamp}\n")

    dirs = {}
    for stream, slot in zip(streams, TRACK_SLOTS):
        dirs[stream] = os.path.join(cand_root, f"track{slot}")
        os.makedirs(dirs[stream], exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(stitched_dir, exist_ok=True)
    os.makedirs(frames_dir, exist_ok=True)

    log(f"extracting {len(kept)} candidates from streams 0:{streams[0]} and 0:{streams[1]}")
    extract_candidates(src, streams, fps, native, kept, dirs,
                       None if args.end is None else args.end + 1.0)
    log(f"stitching with max2sphere, width {width}")
    stitch(args.max2sphere, cand_root, len(kept), width, cache_dir, stitched_dir,
           os.path.join(out, "max2sphere.log"), args.blend)

    frames = []
    for seq, s in enumerate(selected, start=1):
        name = f"frame{s['frame_index']:04d}.jpg"
        path = os.path.join(frames_dir, name)
        os.replace(os.path.join(stitched_dir, f"frame{seq:04d}.jpg"), path)
        frames.append({"path": path, "lat": s["lat"], "lon": s["lon"], "alt": s["alt"], "utc": s["utc"]})
    shutil.rmtree(stitched_dir, ignore_errors=True)

    log(f"writing GPS EXIF into {len(frames)} frames")
    write_exif(frames, out)

    if not args.keep_candidates:
        shutil.rmtree(cand_root, ignore_errors=True)
        shutil.rmtree(cache_dir, ignore_errors=True)

    kept_lat = np.array([s["lat"] for s in selected])
    kept_lon = np.array([s["lon"] for s in selected])
    gaps = haversine_m(kept_lat[:-1], kept_lon[:-1], kept_lat[1:], kept_lon[1:]) if len(selected) > 1 else np.array([])
    if gaps.size:
        spacing_stats = {"median": round(float(statistics.median(gaps)), 3),
                         "min": round(float(gaps.min()), 3), "max": round(float(gaps.max()), 3)}
    else:
        spacing_stats = {"median": None, "min": None, "max": None}
    steps = haversine_m(lat[first:-1], lon[first:-1], lat[first + 1:], lon[first + 1:])
    out_bytes = sum(os.path.getsize(os.path.join(frames_dir, f)) for f in os.listdir(frames_dir))
    summary = {
        "source": src,
        "out_dir": out,
        "camera_model": info["model"],
        "track_layout": layout_name,
        "video_streams": streams,
        "timelapse": timelapse,
        "capture_s_per_video_s": None if math.isnan(speed_factor) else round(speed_factor, 3),
        "erp_width": width,
        "blend_override": args.blend,
        "frames": len(selected),
        "frames_dir": frames_dir,
        "frame_size_px": image_size(frames[0]["path"]),
        "source_size_px": f"{src_w}x{src_h}",
        "fps": fps,
        "spacing_requested_m": args.spacing,
        "candidates": n_total - first,
        "spacing_between_kept_m": spacing_stats,
        "path_length_m": round(float(steps.sum()), 1),
        "duration_s": round(duration, 3),
        "gps_fixes": len(rows),
        "gps_fixes_not_3d": not_3d,
        "output_bytes": int(out_bytes),
        "output_mb": round(out_bytes / 1e6, 1),
        "keep_candidates": bool(args.keep_candidates),
        "elapsed_s": round(time.time() - started, 1),
    }
    if timelapse:
        summary["capture_interval_s"] = round(speed_factor / vfps, 3)
        summary["capture_duration_s"] = round(speed_factor * duration, 1)
        summary["median_speed_ms"] = round(float(np.median(steps)) * fps / speed_factor, 2)
    with open(os.path.join(out, "ingest.json"), "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
