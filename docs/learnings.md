# Learnings

What you need to know to run the pipeline today, and why it is set up the way it is. Dead ends are in
[failed-explorations.md](failed-explorations.md). Steps and commands are in [recipe.md](recipe.md) and
[../pipelines/README.md](../pipelines/README.md).

Every statement cites its evidence as plain IDs. `R-xxx` rows are runs, in [log/RESULTS.md](log/RESULTS.md).
`D-xxx` rows are decisions, in [log/DECISIONS.md](log/DECISIONS.md).

## Capture

- Use time-lapse video, not a photo series. Frames cut from a video every 1 to 1.5 m gave a camera track that fits
  GPS to 1.4 m. Photo sets with a frame every 2 s (5 to 6 m apart) fit to 3.6 to 6.2 m (R-108, D-089).
- A GoPro MAX 2 time-lapse at 8K with one frame per 0.5 s works. On a bike at 3.3 m/s this gave 957 frames at a median
  spacing of 2.13 m over 2.17 km (R-181). The ingest detects a time-lapse `.360` from its GPS clock (D-105).
- The ingest keeps a frame every 1.5 m by default (D-089, `pipelines/1-ingest/ingest_360_video.py`). Denser frames
  (1.0 m) gave only a small gain in the ground texture score, and 3 m spacing a small loss (R-111).
- On foot with a selfie stick, a 1 s time-lapse at 1.4 m/s was enough: 591 frames over 903 m, all registered (R-205,
  R-206).
- Wait for the GPS fix before you start. On the MAX 2, samples before the first 3D fix carry a wrong date and wrong
  positions. They broke the clock fit in the ingest until it was fixed (R-181). Some captures still contain a few
  fixes that are not 3D (R-205).
- Check both lenses before you start. With one lens covered the black half of the image is flat and the automatic
  helmet mask picked it (R-180).
- Measure the camera height above the ground with a ruler and pass it as `CAMERA_HEIGHT`. A 10 cm error scales the
  ground image by about 5 % (D-076). The built-in calibration (`pipelines/4-ground/calibrate_camera_height.py`) is
  a rough check only. It gave 1.975 m, 1.875 m and 1.812 m where 1.90 m was measured, and 1.925 m where 2.01 m was
  measured. Some runs rested on only two clear image patches (R-182, R-189, R-190, R-206).
- Do not assume the image centre points along the direction of travel. Riders look left and right. Orientation comes
  from the images, not from GPS (D-065).
- Nothing may move with the camera. A passenger on the rear seat, straps and a bag are all fixed to the rig, so they
  look the same in every frame and give COLMAP matches without parallax. They have to be masked, and they are hard to
  mask well (see Masks). Keep loose parts off the helmet and keep the stick steady.
- Stops give duplicates and no baseline. Drop frames below a minimum speed (`--min-speed`, R-193, R-205). A scene
  where the camera hardly moves has no initial image pair and cannot be reconstructed (R-095).
- Ride important sections once out and once back. COLMAP merges the two passes into one model (R-212, R-190).
- Wide, uniform streets are the weak spot. On the Obernai route the industrial area had about half the tie points per
  frame (251 against 446) and twice the GPS jitter of the rest, and its frames landed 5.7 m from GPS against 2 to 3 m:
  markings appear twice and neighbouring frames do not line up. Speed mattered less (17 against 14 km/h), and one
  pass alone was not the cause: most of the route was ridden once and came out fine (R-215).
- A short strip is enough. The aim is a strip of 1 to 5 m where you can measure a path width. Stretches that are too
  uniform for COLMAP (wide plain path, open sky, no near structure) can fail with no initial pair, so skip them
  (R-193).

## Poses

- Poses come from COLMAP on the native equirectangular camera, with GPS as a position prior
  (`pipelines/3-poses/colmap_poses.sh`, called by `pipelines/run_scene.sh`). All frames of a 35-frame, a 152-frame and
  a 591-frame scene registered (R-084, R-108, R-206). GPS alone is not used for orientation (D-065).
- Use SIFT with spatial matching. Learned features (ALIKED, LightGlue, LoMa) were too slow or too big on a Mac, and
  SIFT at 1600 px scored the same as at 3200 px (R-114, D-091). COLMAP reads images at most 3200 px wide.
- Set the GPS standard deviation to the real quality. On an open sports ground a std of 3 m left a GPS residual of
  15.6 m, a std of 1 m left 6.8 m (R-182). The long Alsace route and all its sections used 1 m (R-189, R-200).
- Do not use GPS altitude to level a model. The altitude spread of 38 m on a flat street tilted a 238-frame model
  by 45 deg (R-086). The altitude prior has a std of 200 m, and the renderer levels each model with the plane through
  the camera centres (D-071, R-087).
- `FLAT_PRIOR=1` sets every altitude prior to the median GPS altitude. It exists for flat sites where the free
  altitude lets a long model curl up (D-109). The route that curled had a vertical extent of 1086 m (R-189). With
  the sky mask below it was 27 m (R-200).
- Mask the sky above 16 deg for COLMAP features (`SKY_ELEV=16`, default since D-116). On a new, smooth, uniform path the
  textured clouds dominate the matches and the reconstruction breaks. With the sky masked, a section that failed
  with no initial pair registered 260 of 291 frames at 2.69 m GPS residual (R-195). All five sections and the whole
  route then reconstructed at GPS level (R-200). It did no harm on an overcast Berlin scene (R-203).
- Use a plain geometric sky cut, not a Vistas sky mask, for COLMAP. The Vistas sky mask made the model collapse
  (R-201). The segmentation labels only reach the ERP from about 9.8 deg above the horizon down (R-204).
- A model that registers every frame can still be wrong. A run with all 291 frames registered had a 63 m GPS residual
  because the first 60 % of the track collapsed to a few positions (R-194). Check the GPS residual and the path
  length ratio, not only the registration count.
- Out-and-back passes merge even when GPS puts them apart. On a narrow path GPS had two passes 5.2 to 5.4 m apart. In
  the COLMAP model they were 0.7 to 0.8 m apart, which is correct for a 2 m path (R-212). Raising the GPS std to 5 m
  did not change the result (R-212).
- Accuracy of the poses is GPS level. The model is scaled and placed by a similarity to GPS (scale 0.998 on the
  foot capture, R-206). The whole Alsace route has a mean GPS residual of 3.19 m, its sections 0.50 to 2.24 m, and a
  144-frame section 0.81 m (R-200, R-190). Place a result more accurately only with a reference (see Accuracy).
- Memory is the limit. COLMAP at full settings reached 15.5 GB and rebooted the machine (R-083). With 2 threads and
  `max_image_size` 3200 the same 35 frames registered in 58 s at a peak of 0.99 GB (R-084, D-068). A 957-frame route
  took about 2 h: features 17 min, matching 28 min, mapper about 70 min (R-189).

## Masks

- The helmet is found by its image structure, not by its colour (`helmet-mask --method texture`, D-107, D-108,
  `pipelines/2-masks/helmet-mask`). The old colour method masked 38 % of an Alsace frame because the grey path stays
  at the same image position and looks static. Against hand-drawn masks the texture method scores IoU 0.900 there and
  0.935 on a Berlin scene (R-187).
- The helmet does not move in the image. Its vertical shift has a std of 0.21 px (Alsace) and 0.96 px (Berlin), so one
  static mask is enough (R-185). The per-frame offsets of 35 to 45 px that the old tool wrote were not helmet motion (R-185).
- The static mask misses the moving parts of the rig: 2 to 4 % of the rig pixels (passenger, strap) (R-187, R-192).
  Temporal stability cannot tell them from the world, because path edges and paving also look stable at small shifts
  (R-194, D-112).
- Parts that move with the camera are masked by object class, only for the renderer
  (`pipelines/2-masks/segment/rig_zone_mask.py`, D-113, D-114). It takes Vistas rig-class pixels (person-like classes,
  ego vehicle, and the car, boat and mount labels the strap receives) in a 450 px zone above the static mask. It
  never masks road, sidewalk, curb or marking pixels. IoU against hand masks rose to 0.93 to 0.94 (R-196).
  Components larger than 250000 px are dropped so that a passing car is not masked (D-114, R-197).
- Do not give these masks to COLMAP. With them, one 291-frame section registered only 199 frames (geometric sky cut)
  or collapsed (Vistas sky mask). The default stays the static mask plus the sky cut (R-201, D-116).
- Person masks for a walked capture come from `pipelines/2-masks/segment/person_mask.py` (D-117). Run
  `segment_frames.py` at pitch 35 and at pitch 70 so that the area below the camera is labelled. The photographer
  below a selfie stick is not caught by the static mask, which covered only 2.8 % of those frames (R-206, R-207).
- Segmentation is Mask2Former trained on Mapillary Vistas, run on eight pinhole views per frame (D-096). It takes about
  2.6 to 3.3 s per frame on Apple Silicon and under 1.4 GB (R-123, R-197, R-199).
- Vehicles and persons are excluded from the ground image by label, per frame. Car fragments vanish, and the footprints
  stay as holes (R-130, D-099). A separate layer keeps the parked cars (`pipelines/4-ground/car_layer.py`, R-131).

## Ground rendering

- The renderer projects full-resolution frames onto a flat ground one camera height below each camera
  (`pipelines/4-ground/render_ground_ortho.py`). The nearest frame wins, so the centre of the strip stays sharp. It
  filled 95 to 97 % of a 4 m strip at 2 cm (R-085, R-088, D-064).
- A median over many frames is blurry. It is clean only with about six frames per pixel, and two frames give ghosts
  (R-092, R-099). Far frames see the ground flat and coarse. Using frames between 1.5 m and 4 m from a ground cell,
  and the nearest frame always, kept 65 % instead of 40 % of the fine texture (R-115, R-118, D-093). The lower
  limit drops the photographer's own shadow (R-118).
- Use a flat ground, not an external terrain model. IGN LiDAR terrain made neighbouring frames agree 15 to 20 % better
  by the numbers, but renders looked blurrier and the method needs country-specific data. On a cliff path the render
  was unusable (R-208, R-211, D-119). Monocular depth as a ground model changed nothing (R-109, D-090).
- Range limits fill gaps without making rays. A wide range (15 m) fills open ground and turns walls into radial rays.
  `--wall-range 6` limits pixels that are not ground to 6 m from the camera. It removed the rays and kept the
  area (6662 m² against 4423 m² near and 11129 m² wide) (R-213, D-120). The Alsace route used
  `--strip 10 --max-range 12 --wall-range 6` (R-214).
- Frames are co-registered to each other before blending (`pipelines/4-ground/coregister_frames.py`). Optical flow on the
  ground gives a shift per frame, two rounds. Neighbouring frames were 12.2 cm apart and kerb jumps fell from
  5.07 to 2.52 per 10 m (R-146, R-147, R-159, D-098). On the long route the pair shift fell from 6.8 to 4.7 cm
  over about 3700 pairs (R-214).
- Shift only, not rotation. A rotation per frame made kerb jumps worse (5.26 per 10 m) (R-149). The shifts are not
  written back into the camera poses, because corrected poses fit the image features worse (R-164, D-098).
- Blend in two bands. Low frequencies come from several frames, detail from the nearest. Brightness patches at frame
  seams get softer and geometry does not change (R-163, D-099). A smooth gain field per frame already removed most
  patches (R-106, D-088).
- The standard product is `pipelines/scene_ground.sh`: segmentation labels, vehicles and persons excluded, two
  co-registration rounds, two-band blend, tiles, merge (D-099).
- Judge a render by the disagreement map and the alignment score, not by one number (D-088, D-092). Both only measure
  how well frames agree with each other.

## Memory and disk

- Every heavy command runs under `pipelines/common/guarded_run.sh`. It watches process memory, reclaimable system
  memory, swap growth and free disk every 5 s, and keeps the Mac awake (D-068, D-073). A per-process limit is not
  enough: a 15.5 GB COLMAP run rebooted the machine (R-083).
- Tile memory scales with tile area, not frame count. `scene_ground.sh` cuts tiles along the longer side only. On a
  630 x 690 m route 16 tiles of 39 x 689 m peaked at 8 GB and were killed after 11 min. 40 tiles stayed under 7 GB
  (R-214).
- Merging tiles must stream. At 2 cm one raster would need over 4 GB in memory. `merge_tiles.py` uses
  `gdalbuildvrt` and `gdal_translate` for large mosaics (R-214).
- The whole 2.17 km route as a 2 cm ground image took 4 h 40 min on a 24 GB Apple Silicon Mac. The COG is
  32,070 x 35,090 px, 38 MB as JPEG (R-214).
- Large outputs go to external storage, with a symlink from the scene folder. The Mac had 12 to 15 GB free (R-214).
  The guard stops a run when free disk falls below its limit: 13 GB free stopped a segmentation run at a 15 GB limit
  (R-188).
- Other apps take memory too. Browsers, Docker and dev servers pushed swap use to 10.7 of 11.3 GB and stopped runs
  that held under 3 GB themselves (R-144, R-161).
- Ingest: ffmpeg with default threads peaked at 9 GB, and at 1.5 GB after the thread count was reduced (R-181). The ingest writes ERP frames
  at 5376 px width by default (D-106).

## Accuracy

- Width against a tape. On a 3.95 to 3.98 m path the ground image measured 3.82 m by hand in the viewer. That is 13 to
  16 cm, or 3 to 4 % short (R-191, R-214). The reference is a phone measure app, not a steel tape (R-191).
- An earlier hand measurement over a clean 10 m stretch gave a median of 3.84 m (R-191). An automatic width from a
  colour classifier gave 3.59 m and 3.48 m. Treat the automatic value as crude (R-202, R-214).
- Seams shift a path by about 1 m between two passes where the poses disagree, and a passenger's helmet left red
  blobs before it was masked for the renderer. Those places gave widths of 1.3 to 3.5 m (R-191, D-115).
- Absolute position is GPS level. The reconstruction is placed by a similarity to GPS, whose residual is 0.5 to
  3.2 m on the Alsace data (R-200). The result is not surveyed.
- Where a reference exists, you can register to it. In Berlin the kerb lines of the 2014 street survey served as
  reference (`pipelines/evaluate/kerb_reference.py`, `register_to_reference.py`). One similarity fitted to the kerbs
  was a scale of 0.99617, a rotation of 0.216 deg and a shift of (0.54, -0.37) m. In held-out 30 m blocks the kerbs
  then lay at a median of 3.2 to 19.4 cm (R-151). The survey covers only the public street (R-132).
- Internal measures are not accuracy. Frame agreement, kerb wobble and GPS residual agreed with each other while an
  output was still unmeasured (D-046). Measure a known width or distance on every new kind of capture.
- No result has a surveyed reference. There is one width comparison against a phone-measured distance, and one
  registration against kerb lines in Berlin. Nothing else has been checked against an outside reference.
