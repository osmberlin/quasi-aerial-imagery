# Failed explorations

Approaches that did not work, or were dropped before they did. They are here so that nobody repeats them. What works
now is in [learnings.md](learnings.md). Evidence is cited as `R-xxx` (runs, [log/RESULTS.md](log/RESULTS.md)) and
`D-xxx` (decisions, [log/DECISIONS.md](log/DECISIONS.md)). The code of these approaches is not in this repository.

## Reconstruction and ground model

**OpenDroneMap / WebODM with 360 degree images.** We ran stock OpenDroneMap with the spherical camera model on sets of
10 to 30 frames. The REST flow worked. A 30-frame set gave an unbounded model of 74,212 m² and an orthophoto that is a
side view of the mesh. Larger sets ran out of memory or crashed in OpenMVS. The orthophoto step treats a spherical
model as a side view, so the project stopped after the elevation model and moved to its own projection (R-004, R-014,
R-015, D-003, D-015, D-016). Read the model bounds in the log before you look at the output.

**Cutting everything above 2 m by a surface model.** The idea was to remove everything more than about 2 m above
ground with a DSM-minus-DTM threshold. This masked 18 to 52 % of the frame with no gain in geometry. On ground-level
360 captures the surface model is dominated by buildings, trees and walls, so the "ground" often lies above the cameras
(R-018 to R-022, R-025, D-006, D-009). A cut is only as good as the ground surface under it.

**Own Rust orthorectifier around ODM.** A command-line tool replaced ODM's orthophoto with a top-down projection from
the camera track (ground height from the nearest camera altitude minus 1.7 m). The first output was called unusable,
yet it was the first view that looked right, and it filled 99 % of the frame. The idea of projecting full-resolution
frames onto a ground surface is what the current renderer keeps. The tool itself was archived because the binary no
longer started (R-024, R-027, R-082, D-007, D-017, D-063).

**Feed-forward reconstruction in chunks (LingBot-Map) and per-chunk GPS alignment.** We ran LingBot-Map on pinhole
views of each frame in chunks of five frames, aligned each chunk to GPS and rasterised the points. A single fit tilted
one chunk by 78.5 deg. Per-chunk fits gave yaw errors near 100 deg and scale errors of about 30 %, and the chunk
residuals of 3.7 to 9.7 m were GPS error, so agreement between chunks said nothing about accuracy. Five frames cannot
fix yaw and scale. Overnight batches over six datasets were degraded by an output path shared between datasets, chunk
timeouts and a seam solver that made seams worse (R-035 to R-041, R-066 to R-070, D-023, D-034, D-046, D-049, D-051).
Stitching chunks first and georeferencing once looked better on one dataset, but it was never wired in (R-060, R-061,
D-049).

**Point-cloud rasterising.** Rasterising the reconstructed point cloud to a top view gave patchy ground. The models
work at about 518 px per view, so the texture is far below what the 5760 px source holds. Coverage was 45.6 % at 5 cm
in the best run and 7 to 30 % in the batch runs (R-041, R-066 to R-070). Projecting the source pixels instead filled 95
to 97 % of the strip (R-085, R-088).

**Monocular depth as ground, and a depth-based orthophoto.** One version used metric monocular depth per view, with no
structure from motion, to build the top view. It had a sign error that sampled the sky in early runs, filled 14.8 % of
the raster and was never judged against a reference (R-032, D-019, D-022). A second version used monocular depth only
as the ground height model for the projection. It did not reduce frame disagreement on any of three scenes, and the
sparse-point scaling variant put the ground 0.3 m too low (R-109, D-090).

**Gaussian splats as the ground image.** We trained a splat from pinhole views cut from the frames and rendered it from
above with a height cut. The ground came out as needles and gaps, with no paving pattern, because frames 5 m apart
under-constrain it. Projection is sharper on the ground (R-089 to R-092, R-101, D-072, D-074, D-075). The splat did give
compact footprints of parked cars, but it is not part of the pipeline.

**OpenMVS dense cloud and textured mesh.** OpenMVS runs on the Mac CPU, but its fusion kept only 1 % of the depths and
the cloud filled 7.9 % of a 16 m crop at 5 cm. The mesh was fine, but texturing gave a mostly black atlas and the
orthographic export crashed. A dense run over a whole scene needs about 21 GB of depth maps and was stopped by the
watchdog (R-124, R-125, R-136, R-137, R-139, D-095). Our own two-view fusion with a terrain from the cloud made cars
compact and in place, but their texture stayed patchy. It lowered the frame-to-frame shift on one street and not on the
other, so the flat ground stayed the default (R-142, R-143, R-145, R-172, R-174, D-101). A plane-sweep height field of
our own did not find cars (R-120, R-121).

**External IGN LiDAR terrain as ground.** For a walked hillside capture we used the national LiDAR terrain model as the
ground and levelled the track to it. Neighbouring frames agreed 15 to 20 % better by the metric. In a visual comparison in the viewer the renders were blurrier, and on a cliff path they were
unusable. A mix of surface and terrain model was worse (R-208, R-209, R-211, D-118, D-119). The method also needs
country-specific data. A better number did not make a better image.

## Poses and masks

**Learned feature matchers on this Mac.** We tried ALIKED, LightGlue and LoMa through COLMAP. At full size the
watchdog killed every run (9.9 to 13.2 GB), CoreML could not run the models, and at 1600 px ALIKED with LightGlue took
about 135 s per image. SIFT at 1600 px scored the same as at 3200 px, so SIFT stayed (R-114, D-091).

**GPS altitude for levelling.** An altitude prior with a 6 m std tilted a 238-frame model by 45.5 deg, because consumer
GPS altitude varied by 38 m on a flat street. The cameras themselves lay in one plane to 0.10 m. Levelling now comes
from the camera centres (R-086, R-087, D-071).

**Per-frame "moves with the camera" stability masks.** A passenger on the rear seat, a bag and the helmet straps move
with the camera. We masked, per frame, whatever matched its neighbour frames near the helmet mask. Against hand masks
the missed rig fell from 2 to 4 % to under 0.1 %. For COLMAP the masks covered the near path edges, kerbs and periodic
paving instead, which look stable at small shifts and carry the parallax that structure from motion needs. A section
registered 291 of 291 frames but with a 63 m GPS residual and the first 60 % of the track collapsed. The same section
with the static mask and a sky cut gave 2.69 m. The passenger and the bag were still often left open (R-192 to R-195,
D-110, D-112). Judge a mask for COLMAP by a COLMAP run, not by overlap with hand masks. Masks by object class limited
to a narrow zone work for the renderer. Given to COLMAP, the Vistas sky mask collapsed a model and the zone masks registered 199 of
291 frames (R-201, R-204, D-113, D-116).

**Colour-stability and per-frame offset helmet masks.** Early helmet masks used a dome, a temporal colour mask and
per-frame vertical offsets. They removed street pixels or leaked, and a grey path that stays at the same image position
counted as helmet. Mean offsets of 36 to 66 px were computed for a helmet whose vertical shift has a std under 1 px. The leak score was computed on
the boundary the method produced, so it could not judge it (R-043 to R-058, R-185, R-187, D-036 to D-044, D-107).

**Rotations per frame in co-registration.** Shifting each frame by its optical-flow offset on the ground cut kerb jumps
from 5.07 to 2.52 per 10 m. Adding a rotation per frame made them worse (5.26 per 10 m), with rotations up to 5.9 deg.
Writing the shifts back into the camera poses also made the model fit the image features worse (reprojection 1.29 px to
1.45 px, 15 % fewer observations). The shifts stay a render step (R-147, R-149, R-159, R-164, D-098).

## Capture and calibration

**Photo series every 2 s.** Photos taken every 2 s were 5 to 6 m apart and fit GPS to 3.6 to 6.2 m. A video with a frame
every 1.5 m fit to 1.4 m. Video is the capture mode (R-107, R-108, D-089).

**A median over many far frames.** Taking the median of up to 7 frames within 8 to 10 m looked clean but was blurred,
because far frames see the ground flat and coarse. The median uses frames 1.5 to 4 m from the cell, and the nearest
frame always counts (R-115, R-119, D-091, D-093).

**An assumed camera height.** The default heights of 1.7 m and 2.0 m were never checked against images. A naive
sparse-point scale without GPS was 18 % too high. Measure the height on site (R-093, R-096, R-102, D-064, D-076).

**COLMAP at full settings.** The first COLMAP run reached 15.5 GB resident and rebooted the Mac. A watchdog on process
memory alone never fired. Limits must look at system memory and swap, and the reduced settings registered the same
frames in 58 s (R-083, R-084, D-068, D-073).

**A hand-held scene with no baseline.** A scene under a station roof, with the camera barely moving among standing
people and a GPS track that was jitter, had no initial image pair. A mask covering 51 % of the frame was a warning
sign. Check parallax and GPS before you run SfM (R-095).

**Internal metrics instead of a measurement.** For months, seam, leak and residual numbers were tuned while no result
had been checked against a tape measure. Internal consistency was not accuracy (D-046). Compare against something
outside the pipeline early.
