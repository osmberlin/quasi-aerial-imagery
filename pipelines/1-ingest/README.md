# 1. Ingest: 360° video or photos to ERP frames with GPS (GoPro MAX and MAX 2)

Method, measurements and open items: [video-to-erp.md](video-to-erp.md). Capture advice: [docs/recipe.md](../../docs/recipe.md).

## Tool: `ingest_360_video.py`

One command turns a `.360` file (MAX or MAX 2, normal video or time-lapse) into GPS-tagged
equirectangular (ERP) frames:

```
pipelines/.venv/bin/python pipelines/1-ingest/ingest_360_video.py SRC.360 OUT_DIR \
  --spacing 1.5 [--fps F] [--timelapse auto|yes|no] [--start S] [--end S] [--width W] \
  [--blend PX] [--max2sphere PATH] [--keep-candidates]
```

Run it under the watchdog for large jobs (it stops the run when memory or disk limits are crossed):

```
MAX_RSS_MB=8000 MIN_FREE_GB=12 pipelines/common/guarded_run.sh OUT_DIR/guard_log \
  pipelines/.venv/bin/python pipelines/1-ingest/ingest_360_video.py SRC.360 OUT_DIR --spacing 1.5
```

Steps:

1. GPS track from the GPMF stream: `exiftool -ee -n` gives the per-sample fixes (MAX: about 18,
   MAX 2: about 10 per one-second packet). `ffprobe` gives the packet times. Written to `OUT_DIR/gps_samples.csv`.
2. Candidates every `1/FPS` s (default 2 fps). GPS by linear interpolation. Greedy thinning keeps a
   candidate only if it is at least `--spacing` metres from the last kept one. Written to
   `OUT_DIR/selected_frames.csv`.
3. ffmpeg decodes both EAC tracks in one pass and writes only the kept candidates. The tracks are
   the two video streams, found with ffprobe (MAX: `0:0` and `0:5`; MAX 2: `0:0` and `0:4`, or `0:0`
   and `0:3` for a time-lapse). `max2sphere` stitches each pair to an ERP JPEG (default 5376x2688).
   Its lookup table (700 MB at 5376, 1.4 GB at 7680) is written to `OUT_DIR/.cache`, which is
   deleted at the end.
4. One batched exiftool call writes GPSLatitude/Ref, GPSLongitude/Ref (signs as N/S/E/W),
   GPSAltitude/Ref, and DateTimeOriginal with SubSecTimeOriginal (UTC, `OffsetTimeOriginal +00:00`,
   from the GPMF GPSDateTime).
5. Per-track candidate JPEGs are deleted unless `--keep-candidates`. A summary (frame count,
   GPS spacing median/min/max, path length, duration, output size) goes to stdout and to
   `OUT_DIR/ingest.json`. `max2sphere.log` keeps the stitcher output.

Output: `OUT_DIR/frames/frameNNNN.jpg`, with `NNNN = 1 + k` for candidate `k`, so `frame0001` is
t = 0 and `frame0008` is t = 3.5 s at 2 fps. This is the numbering of the earlier 2 fps frame sets.
The tool refuses to run if `frames/`, `candidates/` or `.cache/` already contain files.

Requirements: `ffmpeg`, `ffprobe`, `exiftool` on `PATH`; the built `max2sphere` binary
(default `third-party/max2sphere/max2sphere`, built with `pipelines/1-ingest/max2sphere`, which adds the MAX 2
frame sizes); numpy (in `pipelines/.venv`).

Spacing note: at 2 fps and ~2 m/s walking speed the candidates are about 1 m apart. Spacing below
about 1 m therefore keeps nearly every candidate. Raising the fps does not help much, since the
greedy thinning keeps about one frame per 1.3 to 1.4 m (measured on the Atelier Gardens clip).

## MAX 2

The ingest detects the camera and the track layout (`ingest.json`: `camera_model`, `track_layout`,
`video_streams`). Measured on the MAX 2 sample of 2026-10-21 (method doc, section "MAX 2"):

| Mode | Track size | Layout | Default ERP width |
|---|---|---|---|
| 8K 360 (also 8K time-lapse) | 5888x1920 | 3 faces of 1920 px, side strips 1984 px | 5376 (native 7680) |
| 5.6K or 5.7K 360 | 4096x1344 | same as MAX | 5376 |
| 3K 360 | 3008x960 | 3 faces of 960 px, side strips 1024 px | 3840 (native) |

Width: 5376 keeps the MAX 1 budget (lookup table 0.7 GB, 4 to 7 MB per frame, COLMAP reads at most
3200 px anyway). `--width 7680` is native for 8K: lookup table 1.4 GB, max2sphere 1.6 GB RSS,
about 1.7 times the JPEG size and a 6 s first frame. All frames of one scene must have the same
size (`--ImageReader.single_camera 1`), so do not mix widths in one scene.

Time-lapse: video time is not capture time (each frame is one capture, 0.5 s apart; 29.97 fps
playback). The GPMF stream then holds one packet and one GPS fix per video frame, with
`GPSDateTime` stepping 0.5 s (jitter about 0.1 s). The tool compares the GPS clock with the video
clock (a least-squares line); more than 1.5 capture seconds per video second means time-lapse. Then
every video frame is a candidate (`--fps` defaults to the native rate), positions are interpolated
in video time (correct, because packets sit on the frames), the capture time in the EXIF comes
from the fitted clock, and `ingest.json` gets `capture_interval_s`, `capture_duration_s`,
`median_speed_ms`. Force with `--timelapse yes|no`. `--start/--end` (video seconds) restrict the
candidates, for tests and for cutting a long file.

Memory: ffmpeg decoding of the 8K tracks peaked at 5.0 GB RSS (14 outputs), so give the
watchdog `MAX_RSS_MB=8000`.

Overlap blend (open item): the two halves of every side strip overlap, and max2sphere blends them
at an offset it takes from its frame template. On MAX tracks (4096x1344) this offset was measured
as 63 px (template: 2 x 32). For the MAX 2 sizes only the sample with one lens covered exists, so the
template values (blend 46 at 8K, 23 at 3K, scaled from the MAX) are estimates. On the first capture
with both lenses open run

```
pipelines/.venv/bin/python pipelines/1-ingest/check_track_overlap.py SRC.360 --time 30
```

It prints the measured offset and the `--blend` to use. A wrong blend shifts the content of the side
halves by at most about 1 degree and moves the blend band; it does not break the layout.

## Photos (`ingest_360_photos.py`)

MAX 2 360 photos (`.36P`) are plain JPEGs, already equirectangular (7680x3840) with GPS and
DateTimeOriginal in the EXIF. No stitching:

```
pipelines/.venv/bin/python pipelines/1-ingest/ingest_360_photos.py SRC_DIR OUT_DIR \
  [--width 5376] [--copy] [--spacing M]
```

`OUT_DIR/frames/GSAAnnnn.jpg` are symlinks to the `.36P` files (the source stays read only; they
break when the card is ejected, so use `--copy` or `--width` for lasting frames). `--width W`
writes downscaled JPEG copies with the EXIF kept. `photos.csv` and `ingest.json` hold the GPS gap
to the previous photo, the time interval and the speed. Check these numbers before running a scene:
photos 2 s apart at cycling speed are 8 to 12 m apart (sample of 2026-10-21: median 9.0 m, 4.5 m/s),
far wider than the 1.5 m video spacing, which matters for the median blend of the ground renderer.

## Frames for Panoramax

The same tool makes a set of street-level images for [Panoramax](https://panoramax.fr/): run it a second
time into another folder with a wider spacing, e.g. `--spacing 3`, and upload `frames/`. Anonymise them
first (`../2-masks/anonymise/`). Mapillary takes the `.360` video directly with its own uploader.

## Next step

Masks and poses: [`../run_scene.sh`](../run_scene.sh), see [`../README.md`](../README.md).
