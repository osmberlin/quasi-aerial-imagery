# 3. Poses: where each frame was taken

[`colmap_poses.sh`](colmap_poses.sh) runs [COLMAP](https://colmap.github.io/) 4.x on the CPU with its
native `EQUIRECTANGULAR` camera, so the full 360° frame is used (houses and trees beside the path help),
and `pose_prior_mapper` with the GPS positions from the EXIF as priors, which gives the model its scale
and position. `run_scene.sh` calls it as step 2; it can also run alone:

```bash
pipelines/3-poses/colmap_poses.sh <frames> <scene> spatial <scene>/colmap/helmet_mask.png
```

Matcher: `spatial` (GPS neighbours within 45 m) for routes, `exhaustive` for short sets of a few dozen frames.

Output: `<scene>/colmap/run/sparse/0` and its text copy `sparse/0/txt`; the model with the most
registered frames is always moved to `0`. `<scene>/scene.log` gets the number of models, registered
frames, points and the mean reprojection error. Feature extraction and matching leave marker files
(`.features_done`, `.matches_done`), so a rerun with other mapper settings starts at the mapper.

## Settings (environment)

| Variable | Default | What |
|---|---|---|
| `GPS_STD` | 3 | horizontal GPS standard deviation in metres. 1 for a good MAX 2 track (Obernai, R-200) |
| `GPS_STD_Z` | 200 | altitude prior; nearly free, because GPS altitude is poor (D-071) |
| `FLAT_PRIOR` | 0 | 1 sets every altitude prior to the median GPS altitude, for flat sites where the free altitude lets a long model curl up; use with `GPS_STD_Z=5` |
| `SKY_ELEV` | 16 | mask everything more than this many degrees above the horizon for the features; 0 = off. Textured clouds otherwise give many matches at infinity and no initial pair (R-195, D-116) |
| `MASK_DIR` | – | per-frame masks `<frame file name>.png` (white = keep) instead of the static mask. Not recommended, see [failed explorations](../../docs/failed-explorations.md) |
| `NO_GPS` | 0 | 1 for frames without GPS: plain `mapper`, scale from the camera height, local metres |

COLMAP runs with 2 threads and at most 3200 px per image to stay within the memory of a 24 GB laptop;
each step runs under the watchdog [`../common/guarded_run.sh`](../common/guarded_run.sh).

## Checking the result

- All frames in one model? More than one model, or far fewer registered frames than frames, means
  the track broke (usually a uniform stretch, or the sky was not masked).
- The track should fit the GPS to about the GPS quality (a few metres; 0.5–2.2 m per section on the
  Obernai route, R-200). Absolute position is GPS-level, never better.
- The camera height that `run_scene.sh` then calibrates (`<scene>/ortho/camera_height.json`) should be
  near the measured height of the lens.

More: [docs/learnings.md#poses](../../docs/learnings.md#poses).
