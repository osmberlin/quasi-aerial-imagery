#!/bin/zsh
# One scene from ERP frames to camera poses and a first ground image. Every heavy step runs under common/guarded_run.sh.
# Usage: run_scene.sh <images_dir> <scene_dir> <exhaustive|spatial>
# Steps (each skipped when its output exists):
#   1 helmet mask            <scene>/colmap/helmet_mask.png             (2-masks/helmet-mask)
#   2 COLMAP poses           <scene>/colmap/run/sparse/0                (3-poses/colmap_poses.sh)
#   2b camera height         <scene>/ortho/camera_height.json           (4-ground/calibrate_camera_height.py)
#   3 ground projection      <scene>/ortho/ground_nearest.tif, ground_median.tif (4-ground/render_ground_ortho.py)
#   4 comparison with a reference orthophoto, only if <workspace>/secrets.env names one (evaluate/compare_with_reference.py)
# The standard ground product (vehicles and people excluded, frames co-registered, tiles) is scene_ground.sh, run afterwards.
# Environment: GPS_STD (3), GPS_STD_Z (200), FLAT_PRIOR (0), SKY_ELEV (16), MASK_DIR, NO_GPS (0): see 3-poses/README.md.
#   CAMERA_HEIGHT (2.0, used when the calibration is inconclusive), MEDIAN_RANGE (10), MEDIAN_NEAR (4.0: only frames this
#   close enter the median), MEDIAN_MIN (1.5), MEDIAN_K (7), MEDIAN_MAX_RSS_MB (9000), HELMET_MASK_METHOD (texture, D-108),
#   ORTHO_CRS (scene CRS, e.g. EPSG:25832; default: UTM zone at the median GPS position), PYTHON (default pipelines/.venv/bin/python),
#   HELMET_MASK_DIR (default 2-masks/helmet-mask).
case "${1:-}" in -h|--help|"") sed -n '2,/^case /p' $0 | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;; esac
set -eu
IMAGES=${1:A}; SCENE=${2:A}; MATCHER=$3
HERE=${0:A:h}
PY=${PYTHON:-$HERE/.venv/bin/python}; GUARD=$HERE/common/guarded_run.sh
HELMET_MASK_DIR=${HELMET_MASK_DIR:-$HERE/2-masks/helmet-mask}
CAMERA_HEIGHT=${CAMERA_HEIGHT:-2.0}; MEDIAN_RANGE=${MEDIAN_RANGE:-10}; MEDIAN_K=${MEDIAN_K:-7}
# texture = static image structure (D-107, default since D-108); stability = colour stability (D-037)
HELMET_MASK_METHOD=${HELMET_MASK_METHOD:-texture}
NO_GPS=${NO_GPS:-0}
GPSFLAG=(); [ $NO_GPS = 1 ] && GPSFLAG=(--no-gps)
mkdir -p $SCENE/colmap $SCENE/ortho
log() { echo "=== $(date '+%F %T') $*" | tee -a $SCENE/scene.log; }

MASK=$SCENE/colmap/helmet_mask.png
if [ ! -f $MASK ]; then log "1 helmet mask"
  # helmet-mask loads every frame into memory (273 frames: > 6.7 GB, R-094), so give it at most 40, evenly spread
  SUB=$SCENE/colmap/mask-frames; rm -rf $SUB; mkdir -p $SUB
  FILES=($IMAGES/*.(JPG|jpg)); STEP=$(( (${#FILES} + 39) / 40 ))
  for ((k=1; k<=${#FILES}; k+=STEP)); do ln -s ${FILES[k]:A} $SUB/${FILES[k]:t}; done
  MAX_RSS_MB=6000 $GUARD $SCENE/colmap/mask-run $HELMET_MASK_DIR/.venv/bin/helmet-mask --input $SUB --out-mask $MASK --method $HELMET_MASK_METHOD
  tail -1 $SCENE/colmap/mask-run/stdout.log | tee -a $SCENE/scene.log; fi

MODEL=$SCENE/colmap/run/sparse/0
if [ ! -f $MODEL/txt/images.txt ]; then log "2 colmap ($MATCHER)"
  PYTHON=$PY $HERE/3-poses/colmap_poses.sh $IMAGES $SCENE $MATCHER $MASK; fi

# Camera height from photo-consistency between neighbouring frames (GPS scenes only: without GPS the
# camera height is what defines the scale). Falls back to CAMERA_HEIGHT when no patch gives a clear peak.
if [ $NO_GPS = 0 ] && [ ! -f $SCENE/ortho/camera_height.json ]; then log "2b camera height calibration"
  $PY $HERE/4-ground/calibrate_camera_height.py --model $MODEL --images $IMAGES --mask $MASK --output $SCENE/ortho/camera_height.json > /dev/null 2>&1 || true; fi
if [ -f $SCENE/ortho/camera_height.json ]; then
  H=$($PY -c "import json,math;r=json.load(open('$SCENE/ortho/camera_height.json'));h=r['per_pair_best_median_m'];print(h if r['patches_with_clear_peak']>=2 and not math.isnan(h) else '')")
  [ -n "$H" ] && { CAMERA_HEIGHT=$H; log "camera height from calibration: $H m"; } || log "camera height calibration inconclusive, using $CAMERA_HEIGHT m"; fi

if [ ! -f $SCENE/ortho/ground_median.tif ]; then log "3 ground projection"
  $PY $HERE/4-ground/render_ground_ortho.py --model $MODEL --images $IMAGES --mask $MASK --camera-height $CAMERA_HEIGHT \
    --output $SCENE/ortho/ground_nearest.tif --blend nearest --gain-compensation local --median-k $MEDIAN_K "${GPSFLAG[@]}" | grep -E "levelled|target XY|georeference|output|coverage" | tee -a $SCENE/scene.log
  MAX_RSS_MB=${MEDIAN_MAX_RSS_MB:-9000} $GUARD $SCENE/ortho/median-run $PY $HERE/4-ground/render_ground_ortho.py --model $MODEL --images $IMAGES --mask $MASK \
    --camera-height $CAMERA_HEIGHT --output $SCENE/ortho/ground_median.tif --blend median --median-k $MEDIAN_K --max-range $MEDIAN_RANGE --median-range ${MEDIAN_NEAR:-4.0} --min-range ${MEDIAN_MIN:-1.5} --gain-compensation local --no-disagreement "${GPSFLAG[@]}"
  grep -E "coverage|mean_frames" $SCENE/ortho/median-run/stdout.log | tee -a $SCENE/scene.log; fi

[ $NO_GPS = 1 ] && { log "done (no GPS: no reference comparison)"; exit 0; }
# optional: a reference orthophoto as XYZ tiles, URL template in <workspace>/secrets.env (see evaluate/README.md)
if [ -n "${REFERENCE_URL_VAR:-}" ]; then log "4 reference comparison"
  for t in $SCENE/ortho/ground_nearest.tif $SCENE/ortho/ground_median.tif; do
    [ -f $t ] && $PY $HERE/evaluate/compare_with_reference.py $t --url-var $REFERENCE_URL_VAR --scale ${COMPARE_SCALE:-0.25} | tail -1 | tee -a $SCENE/scene.log; done
fi
log "done"
