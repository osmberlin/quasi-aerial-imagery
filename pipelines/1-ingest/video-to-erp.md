# GoPro MAX `.360` video to equirectangular (ERP) frames with GPS

Date: 2026-10-14. Platform: macOS (arm64), Homebrew ffmpeg 9.0.2 (`--enable-videotoolbox`, no OpenCL), exiftool 13.55.

Test file: `imagery/<capture>/GS017828.360` (1.24 GB, 164.898 s).

## Summary

- **Works and verified on one frame:** trek-view `max2sphere` (built from source with the Homebrew libjpeg). It takes the two per-track JPEG frames and writes a 5376x2688 ERP JPEG. Visually plausible, no black bands, wrap seam within normal range.
- **Does not work as shared:** the widely shared `gopromax_opencl` filter graph. Homebrew ffmpeg 9.0.2 has no `gopromax_opencl` filter and no OpenCL.
- **Ran but not validated:** a naive crop + vstack + `v360=input=eac` reassembly. It produces an image, but it ignores the 64 px overlap blend, so it is not a correct stitch. Not recommended.
- **GPS:** 165 one-second GPMF packets, 2984 per-sample fixes, all with 3D fix (GPSMeasureMode 3). Parsed with a Python script (a one-line exiftool `-p` template gives only 165 rows, see below).
- **Orientation:** the file has CORI/IORI/GRAV telemetry, but the max2sphere output is not levelled. Levelling was not applied or measured.
- **Frame selection:** ~1 candidate per 0.5 s (2 fps), then thinned to >= 1.5 m spacing by GPS. For the full clip this gives an expected ~152 frames. Full-clip run was **not executed**; only the per-frame steps were tested.

## Environment and stream facts (measured)

```
ffprobe -v error -show_entries stream=index,codec_name,codec_type,codec_tag_string,width,height,avg_frame_rate,duration,nb_frames:stream_tags=handler_name FILE
```
Streams:
- 0: hevc 4096x1344, 29.97 fps, 4942 frames, "GoPro H.265"
- 1: aac (audio)
- 2: tmcd
- 3: gpmd (GPMF telemetry, "GoPro MET"), 165 packets
- 4: fdsc ("GoPro SOS")
- 5: hevc 4096x1344, 29.97 fps, 4941 frames

Format duration 164.898067 s.

Filter check:
```
ffmpeg -hide_banner -filters | grep -i -E "gopro|v360|eac"
ffmpeg -hide_banner -h filter=gopromax_opencl
```
Output: only `v360` is present. `gopromax_opencl` gives `Unknown filter 'gopromax_opencl'`. `-buildconf` has no `--enable-opencl`.

## Candidate (a): ffmpeg `gopromax_opencl` (widely shared graph) -- does not work here

Command from the trek-view write-up (not run; the filter does not exist in this ffmpeg):
```
ffmpeg -hwaccel opencl -v verbose -filter_complex \
 '[0:0]format=yuv420p,hwupload[a],[0:5]format=yuv420p,hwupload[b],[a][b]gopromax_opencl,hwdownload,format=yuv420p' \
 -i GS018421.360 -c:v libx264 -pix_fmt yuv420p -map_metadata 0 -map 0:a -map 0:3 GS018421-stitched.mp4
```
Result: cannot run on Homebrew ffmpeg 9.0.2. It needs a custom build with `--enable-opencl`. The trek-view article gives this build line: `./configure --enable-opencl --enable-opengl --enable-sdl2 --enable-libx264 --enable-gpl --disable-x86asm`. The article's output is 5376x2688 with 2:1 aspect. Time Warp files use streams 0:0 and 0:4 (not tested here).

## Candidate (a'): naive crop + vstack + `v360` (ran, not validated)

```
ffmpeg -hide_banner -loglevel error -y -i FILE -ss 30 -frames:v 1 \
 -filter_complex "[0:0]crop=4032:1344:0:0[a];[0:5]crop=4032:1344:0:0[b];[a][b]vstack,v360=input=eac:output=equirect:w=2688:h=1344,format=yuvj420p" \
 test_a_4032.png
```
Result: runs and writes a frame, but this drops the 64 px overlap bands with no blending and no seam check. The 4032 crop is a guess at the stream layout; the proper overlap geometry is in the trek-view/Paul Bourke descriptions. Not a validated stitch, so not used further. (This approach would need the exact overlap blend to be correct.)

## Candidate (b): trek-view `max2sphere` -- works

### Build
```
curl -sL -o max2sphere.zip https://github.com/trek-view/max2sphere/archive/refs/heads/master.zip
unzip -q -o max2sphere.zip -d max2sphere_src
cd max2sphere_src/max2sphere-main
make -f Makefile-MacM1 INCLUDES="-I/opt/homebrew/include" LFLAGS="-L/opt/homebrew/lib"
```
The stock Makefile-MacM1 points to `/opt/homebrew/opt/jpeg/include`, which does not exist here. Overriding `INCLUDES`/`LFLAGS` with the Homebrew `jpeg-turbo` paths built cleanly (one harmless unused-variable warning in `bitmaplib.c`). Binary: `max2sphere_src/max2sphere-main/max2sphere`.

### Extract matching tracks (one frame tested, at 30 s)
```
S="<workspace>/imagery/<capture>/GS017828.360"
T=.../scratch/max2sphere_test
mkdir -p "$T/track0" "$T/track5" "$T/STITCHED"
ffmpeg -hide_banner -loglevel error -ss 30 -i "$S" -map 0:0 -frames:v 1 -q:v 1 -y "$T/track0/frame0001.jpg"
ffmpeg -hide_banner -loglevel error -ss 30 -i "$S" -map 0:5 -frames:v 1 -q:v 1 -y "$T/track5/frame0001.jpg"
```

### Run (tested)
```
cd "$T"   # lookup table cache is written to the current directory
/.../max2sphere_src/max2sphere-main/max2sphere -w 5376 -n 1 -m 1 -o "$T/STITCHED/frame%04d.jpg" "$T/track%d/frame%04d.jpg"
```
Measured: output `5376,2688` (ffprobe), 6.8 MB JPEG. Total wall time 2.0 s for the frame, including first-run lookup table generation (`0_5376_2688_2.data`, 693 MB, cached for later runs in the same directory).

The max2sphere usage (from its README and `max2sphere.c`): `max2sphere [-w n] [-a n] [-o pattern] [-n start] [-m end] track%d/frame%04d.jpg`. Template `%d` #1 is substituted with 0 and 5 (track 0 and track 5), `%d` #2 with the frame number.

### Seam and black-band checks (numbers)
Measured with PIL/numpy on `max2sphere_test/STITCHED/frame0001.jpg` (5376x2688):
- Median absolute difference between adjacent columns: 4.31. 99th percentile: 8.4.
- Wrap seam (column 0 vs column 5375, the 180 degree line in ERP): mean absolute difference 6.36. This is inside the normal range (near median/p99), so no seam is visible at the wrap.
- Centre column 2688 (lon 0, the camera front): adjacent diff 7.7. Inside the normal range.
- Largest discontinuity near x=2662 to 2666 (diff up to 14.7), i.e. about 25 px left of centre. Looks like scene content (high-contrast edge), not checked further.
- Black bands: columns with mean < 10: 0. Rows with mean < 10: 0. Top rows mean 136, bottom rows mean 45, so no black bands.
- Visually (one image viewed): sky at top, ground at bottom, helmet visible in the lower band (expected for a helmet mount). No obvious stitching artefact. Not checked at full resolution.

## Candidate (c): other tools
- `gopro-max-to-equirectangular`, `spatialmedia`, `GoPro Player` CLI were not tested. max2sphere and the ffmpeg route are enough for this pipeline.

## Orientation (CORI/IORI/GRAV)

```
exiftool -ee -G3 -s FILE | grep -i -E "orient|quat|cori|iori|grav"
```
Tags present in this file (counts = packets, i.e. 165 is one per second):
- `CameraOrientation` (CORI): 165 packets, binary (2230 bytes in packet 1). Per-sample quaternion; this is what horizon-levelling uses.
- `ImageOrientation` (IORI): 165 packets, binary (239 bytes). Quaternion for image orientation.
- `GravityVector` (GRAV): 165 packets, binary.
- `InputOrientation` / `OutputOrientation` (ORIN / ORIO): 495 entries total (3 per packet), values `XzY` and `ZXY`. These are axis-order strings, not quaternions.

exiftool prints CORI/IORI/GRAV as binary; `exiftool -b` extracts them. I did not decode the quaternions here, so no roll/pitch numbers are reported.

Levelling: neither ffmpeg `v360` nor max2sphere applies CORI/IORI. The output is therefore unlevelled. The horizon in the test frame looks roughly level by eye, but the tilt was not measured. For this pipeline that is acceptable, since COLMAP estimates orientation. If levelling is wanted later, decode CORI (quaternion per sample) and apply it per frame before or after ERP; that is an open item.

## GPS per frame

### Sample count and times (measured)
- `exiftool -ee -G3 -s -GPSLatitude FILE` contains 2985 `GPSLatitude` lines. My parser maps 2984 of them to samples with lat/lon (one line not parsed, cause not found).
- Packets: 165 (ffprobe `-select_streams 3`, pts 0.000000, 1.001000, 2.002000, ... 164.164000).
- Per-packet fields (one per packet, not per sample): `GPSMeasureMode` (3 = 3-D fix in all 165), `GPSHPositioningError` (1.37 to 2.48 m), `GPSDateTime`.
- Per-sample fields: `GPSLatitude`, `GPSLongitude`, `GPSAltitude`, `GPSSpeed`, `GPSSpeed3D`.
- Sample j of packet k is at `t = pts_k + j * 1.001 / n_k` (n_k samples per packet, about 18). Last fix at t = 165.09 s, which is past the video end (164.898 s).

Note on the brief: `exiftool -ee -n -p '$SampleTime $GPSLatitude $GPSLongitude $GPSAltitude' FILE` gives only 165 rows (one per packet, showing the last sample of each packet, `SampleTime` 0, 1.001, 2.002, ...). It does not give the 2985 per-sample rows. Use the parser below for per-sample data.

### Parser (tested)
```
exiftool -ee -n -G3 -s -GPSLatitude -GPSLongitude -GPSAltitude -GPSHPositioningError -GPSMeasureMode -GPSSpeed -GPSDateTime "$S" > gpmf_gps_raw.txt
ffprobe -v error -select_streams 3 -show_entries packet=pts_time -of csv=p=0 "$S" > gpmf_pts.txt
python3 gps_frames.py    # -> gps_samples.csv (t_s, doc, idx, lat, lon, alt_m, hpe_m, measure_mode, speed_ms, gps_datetime)
```
Output: 165 packets, 2984 GPS5 samples; path length over the 1 Hz means = 320.6 m; mean step 1.955 m; max step 3.12 m.

### Time mapping and drift
- Telemetry packets are on a 1.001 s grid, so telemetry time runs about 0.16% slower than the 164.898 s video. Error grows to ~0.3 s at the end of the clip (~0.6 m at 1.96 m/s).
- `GPSDateTime` is whole-second: 14:57:09.524 (packet 1) to 14:59:54.524 (packet 165), i.e. 165 s over 164 packets. The 1 s grid cannot resolve the 0.16% scale better than that. The uncertainty is stated, not removed.

### Interpolation and test (tested)
```
python3 gps_at.py 0 30 31.5 164.0
```
- t=0: lat 52.4645489, lon 13.4102936, alt 48.60, hpe 2.12
- t=30: lat 52.4648194, lon 13.4103339, alt 49.18, hpe 1.68
- t=31.5: lat 52.4648501, lon 13.4103153, alt 49.18
- t=164.0: lat 52.4645640, lon 13.4108953, alt 45.94, hpe 2.48

The first sample (52.4645489) matches the brief.

### Writing GPS EXIF into a JPEG (tested on a copy)
```
exiftool -overwrite_original -q \
  -GPSLatitude=52.4648194 -GPSLatitudeRef=N \
  -GPSLongitude=13.4103339 -GPSLongitudeRef=E \
  -GPSAltitude=49.18 -GPSAltitudeRef=0 \
  -GPSHPositioningError=1.68  geotag_test/frame0001.jpg
exiftool -n -GPSPosition -GPSAltitude -GPSHPositioningError geotag_test/frame0001.jpg
```
Read back: `GPS Position 52.4648194000722 13.4103339000056`, `GPS Altitude 49.18`, `GPS Horizontal Positioning Error 1.68`. The original test frame in `max2sphere_test/` was not modified.

## Frame selection

Target spacing 1-2 m. Mean GPS step at 1 Hz is 1.96 m, so 1 fps alone gives 1.5-3 m gaps. Recipe: candidates at 2 fps (every 0.5 s), then keep a frame only if it is >= 1.5 m from the last kept frame.

```
python3 select_frames.py 2 1.5
```
Measured (script run):
- 2 fps: 329 candidates, 152 kept (>= 1.5 m thinning). Expected count for the 165 s clip: ~152.
- 1 fps, same thinning: 164 candidates, 144 kept.

The script writes `selected_frames.csv` with `ffmpeg_index` (1-based, matches ffmpeg `frame%04d.jpg`), `t_s`, `lat`, `lon`, `alt_m`.

## Recipe (full clip)

Per-frame steps are tested (frame at 30 s). The full-clip run is **not executed**. Paths below assume scratch as the working directory.

```bash
SCR="<workspace>/imagery/<capture>/scratch"
SRC="<workspace>/imagery/<capture>/GS017828.360"
MS2S="$SCR/max2sphere_src/max2sphere-main/max2sphere"
RUN="$SCR/run_GS017828"
mkdir -p "$RUN/track0" "$RUN/track5" "$RUN/STITCHED" "$RUN/selected"

# 1. candidate tracks at 2 fps, JPEG q=1 (per-output -vf so both tracks run in one ffmpeg call)
ffmpeg -hide_banner -loglevel error -i "$SRC" \
  -map 0:0 -vf fps=2 -q:v 1 "$RUN/track0/frame%04d.jpg" \
  -map 0:5 -vf fps=2 -q:v 1 "$RUN/track5/frame%04d.jpg"

# 2. ERP for all candidates (run from the directory that holds the lookup-table cache)
N=$(ls "$RUN/track0" | wc -l)
cd "$SCR/max2sphere_test"
"$MS2S" -w 5376 -n 1 -m "$N" -o "$RUN/STITCHED/frame%04d.jpg" "$RUN/track%d/frame%04d.jpg"

# 3. GPS (after gps_samples.csv exists; see parser above)
cd "$SCR" && python3 select_frames.py 2 1.5

# 4. copy the selected ERP frames and write GPS EXIF (N/E refs: valid for this site, all positive)
tail -n +2 "$SCR/selected_frames.csv" | while IFS=, read idx t lat lon alt; do
  f=$(printf "frame%04d.jpg" "$idx")
  cp "$RUN/STITCHED/$f" "$RUN/selected/$f"
  exiftool -overwrite_original -q \
    -GPSLatitude="$lat" -GPSLatitudeRef=N \
    -GPSLongitude="$lon" -GPSLongitudeRef=E \
    -GPSAltitude="$alt" -GPSAltitudeRef=0 \
    "$RUN/selected/$f"
done
```

Expected: ~152 ERP JPEGs at 5376x2688 (~6.8 MB each, ~1 GB). Candidate ERP generation: ~329 frames at ~1-2 s each (estimate from the single-frame timing, not measured over many frames).

## MAX 2 (added 2026-10-21)

Sample: SD card `DCIM/100GOPRO` of a GoPro MAX 2 (model MAX2, firmware H24.02.01.30.00), captured with
one lens covered, so half of each image is black. Facts measured with ffprobe, exiftool and ffmpeg:

| File | Kind | Video streams | Track size | GPMF | Notes |
|---|---|---|---|---|---|
| GS010003.360 | 360 time-lapse | 0:0 and 0:3, hvc1 | 5888x1920 | 0:2, 363 packets of 33 ms | 362 frames, 29.97 fps, 12.08 s, no audio |
| GS010002.360 | normal 360 video | 0:0 and 0:4 | 3008x960 | 0:3, 237 packets (1.0 s) | 237 s, audio at 0:1 and 0:5 |
| GS010001.360 | normal 360 video, black | 0:0 and 0:4 | 4096x1344 | 0:3 | indoors, no 3D fix |
| GSAA0004..0204.36P | 360 photos | - | 7680x3840 ERP JPEG | EXIF | 201 files, 2 s apart |

MAX 1 files have the tracks at 0:0 and 0:5, so the stream indices are not fixed. The ingest takes the
two video streams from ffprobe.

**Track layout is the MAX layout, scaled.** Each track is `[side strip | centre face | side strip]`.
Measured on the sample: in a track, the pixels of one lens are black between columns 992 and 4896
(8K) for one track and outside them for the other, the strip halves are cut at exactly half the strip
width (992 of 1984; 688 of 1376 on MAX). The 8K strip is the face plus 64 px (1984 = 1920 + 64);
5888 = 3 x 1920 + 128. The MAX layout's face positions come out right in the stitched image: the
centre of the ERP (longitude 0) is the face of the covered lens (black), the helmet is at the bottom
of the back lens, the wrap seam at the image edge and the face edges at longitudes +-135 degrees show
continuous roof lines, poles and paths (zoom crops in the scratch folder).
`max2sphere` has frame templates only for 4096x1344 and 2272x736. It rejected 5888x1920 and
3008x960 ("No recognised frame template"). Two entries were added (`pipelines/1-ingest/max2sphere`): the same
layout code with the 8K and 3K sizes. 4096x1344 tracks of the MAX 2 use the existing entry. The
ffmpeg `v360=input=eac` route was not needed (it ignores the overlap blend, see candidate (a')).

**Overlap blend.** Both halves of a side strip contain the same scene 64 px apart on MAX 5.6K tracks
(measured with `check_track_overlap.py` on GS017828.360 at 30 s and 60 s: 63 px, 19 to 24 matching
column patches per strip, template value 2 x 32). For MAX 2 the two halves of a strip are from
different lenses, so with one lens covered the offset cannot be measured. The templates use 46 px
(8K) and 23 px (3K), the MAX value scaled by the face size. This is an assumption. A different true
value moves the content of the side halves by about 1 degree at most (blend 32 to 64 changes the
strip scale by 3 %). Verify on the first two-lens capture with `check_track_overlap.py`, then pass
`--blend`.

**Time-lapse GPS.** In GS010003 the GPMF stream has one packet per video frame (363 packets, pts
0, 0.033, ...), each with one GPS fix and its own `GPSDateTime`. The clock steps 0.5 s per packet
(14:46:12.099 to 14:49:12.599 UTC, 180.5 s over 12.08 s of video, factor 14.98 = 0.5 s x 29.97 fps),
with +-0.1 s jitter. So packets sit on frames and interpolating in video time is right; what has to
change is the candidate rate (every frame, not 2 per video second, which would be one frame per 7.5 s
of capture) and the capture time in the EXIF (fitted clock). Normal videos have a factor of 1.0
(GPS5 about 10 per 1.001 s packet on MAX 2, 2368 fixes in 237 s). Check against the positions: the
fixes of the time-lapse are 1.68 m apart (median, per 0.5 s), i.e. 3.35 m/s; the GPMF `GPSSpeed` of
the same file has median 11.87 km/h = 3.30 m/s. With the video clock (1/29.97 s per frame) the same
steps would be 50 m/s. The exiftool value `GPSSpeed` is km/h (normal video, 60 to 75 s: 21.5 km/h
against 6.05 m/s from positions); the column in `gps_samples.csv` is named `speed_kmh` since this
change (it was `speed_ms`).

**Photos.** `.36P` is a JPEG, already ERP 7680x3840 (software H24.02.01.30.00, ProjectionType
equirectangular), EXIF GPS and DateTimeOriginal with +02:00 offset. The `GPSDateTime` tag lags
`DateTimeOriginal` by 23 s in the first photo, so use `DateTimeOriginal`. The photos continue where
GS010003 ends (video end 48.45687 N 7.48877 E; first photo 48.45685 N 7.48879 E) and end at
48.45448 N 7.48484 E.

Results (run on the sample, scratch folder `max2-ingest`): see RESULTS.md R-177 to R-179.

## Not verified
- Full-clip run of the recipe (only one frame's ERP and GPS geotag were run).
- The 1-line unparsed GPS sample (2985 lines vs 2984 samples).
- Levelling: CORI/IORI quaternions not decoded; tilt of the output frames not measured.
- Overlap blend quality beyond the statistics above (no visual check at full resolution, no comparison with GoPro Player output).
- Time Warp files (streams 0:0 and 0:4) not tested.
- Position accuracy: HPE is 1.4-2.5 m (receiver-reported). Telemetry drift ~0.3 s at clip end.

## Scratch artefacts (kept)
- `max2sphere_test/track0/frame0001.jpg`, `track5/frame0001.jpg`: input pair at 30 s.
- `max2sphere_test/STITCHED/frame0001.jpg`: ERP test frame (5376x2688).
- `geotag_test/frame0001.jpg`: same ERP with GPS EXIF written to the copy.
- `max2sphere_test/0_5376_2688_2.data`: max2sphere lookup table cache (693 MB; delete if space is needed, it is regenerated).
- `max2sphere_src/max2sphere-main/max2sphere`: built binary.
- `gps_samples.csv`, `selected_frames.csv`, `gps_frames.py`, `gps_at.py`, `select_frames.py`, `gpmf_gps_raw.txt`, `gpmf_pts.txt`.

Removed afterwards: raw 4096x1344 PNG extracts, the failed naive-v360 PNG outputs, and the downloaded max2sphere zip.

## Sources
- trek-view, "Using FFmpeg to process GoPro MAX 360 video": https://www.trekview.org/blog/using-ffmpeg-process-gopro-max-360/
- trek-view/max2sphere (README, usage, build): https://github.com/trek-view/max2sphere
- Paul Bourke, GoPro MAX .360 layout: https://paulbourke.net/panorama/gopromax2sphere
- FFmpeg-devel patch for `gopromax_opencl`: https://ffmpeg.org/pipermail/ffmpeg-devel/2024-July/331814.html (search result summary; not fetched in full)
