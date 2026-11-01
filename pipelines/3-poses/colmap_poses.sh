#!/bin/zsh
# Camera poses with COLMAP on the native equirectangular camera, GPS positions from the EXIF as priors.
# Called by run_scene.sh (step 2); can also run alone. Every heavy step runs under common/guarded_run.sh.
# Usage: colmap_poses.sh <images_dir> <scene_dir> <exhaustive|spatial> <helmet_mask.png>
# Output: <scene>/colmap/run/sparse/0 (binary) and sparse/0/txt (text); the largest model is always model 0.
# Skips feature extraction and matching when their marker files exist, so a failed mapper can be rerun.
# Environment: GPS_STD (3), GPS_STD_Z (200: altitude nearly free, D-071), FLAT_PRIOR (0), SKY_ELEV (16), MASK_DIR, NO_GPS (0),
#   COLMAP_THREADS (2),
#   PYTHON (default: pipelines/.venv/bin/python). See README.md next to this script for what each one does.
case "${1:-}" in -h|--help|"") sed -n '2,/^case /p' $0 | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;; esac
set -eu
IMAGES=${1:A}; SCENE=${2:A}; MATCHER=$3; MASK=${4:A}
HERE=${0:A:h}; PIPES=${HERE:h}
PY=${PYTHON:-$PIPES/.venv/bin/python}; GUARD=$PIPES/common/guarded_run.sh
SKY_ELEV=${SKY_ELEV:-16}; GPS_STD=${GPS_STD:-3}; GPS_STD_Z=${GPS_STD_Z:-200}; FLAT_PRIOR=${FLAT_PRIOR:-0}; NO_GPS=${NO_GPS:-0}
THREADS=${COLMAP_THREADS:-2}
log() { echo "=== $(date '+%F %T') $*" | tee -a $SCENE/scene.log; }

RUN=$SCENE/colmap/run; MODEL=$RUN/sparse/0
mkdir -p $RUN
FMASK=$MASK
if [ $SKY_ELEV != 0 ]; then FMASK=$SCENE/colmap/feature_mask.png
  $PY -c "import numpy as np,sys;from PIL import Image;m=np.asarray(Image.open(sys.argv[1]).convert('L')).copy();h=m.shape[0];m[:int(round(h/2*(1-float(sys.argv[3])/90)))]=0;Image.fromarray(m).save(sys.argv[2])" $MASK $FMASK $SKY_ELEV
  log "feature mask: sky above $SKY_ELEV deg masked"; fi
MASKARG=(--ImageReader.camera_mask_path $FMASK); [ -n "${MASK_DIR:-}" ] && MASKARG=(--ImageReader.mask_path ${MASK_DIR:A})
if [ ! -f $RUN/.features_done ]; then
  MAX_RSS_MB=5000 $GUARD $RUN/features colmap feature_extractor --database_path $RUN/database.db --image_path $IMAGES \
    --ImageReader.camera_model EQUIRECTANGULAR --ImageReader.single_camera 1 \
    "${MASKARG[@]}" \
    --FeatureExtraction.use_gpu 0 --FeatureExtraction.num_threads $THREADS --FeatureExtraction.max_image_size 3200 --SiftExtraction.first_octave 0
  touch $RUN/.features_done; fi
if [ ! -f $RUN/.matches_done ]; then
  if [ $MATCHER = spatial ]; then
    MAX_RSS_MB=5000 $GUARD $RUN/matching colmap spatial_matcher --database_path $RUN/database.db --FeatureMatching.use_gpu 0 \
      --FeatureMatching.num_threads $THREADS --SpatialMatching.max_distance 45 --SpatialMatching.max_num_neighbors 60
  else
    MAX_RSS_MB=5000 $GUARD $RUN/matching colmap exhaustive_matcher --database_path $RUN/database.db --FeatureMatching.use_gpu 0 --FeatureMatching.num_threads $THREADS
  fi; touch $RUN/.matches_done; fi
# frames without GPS get an empty prior in another coordinate system, which aborts the mapper
sqlite3 $RUN/database.db "delete from pose_priors where (select count(distinct coordinate_system) from pose_priors)=2 and coordinate_system=(select coordinate_system from pose_priors group by 1 order by count(*) asc limit 1);"
if [ $FLAT_PRIOR = 1 ] && [ $NO_GPS = 0 ]; then
  $PY - $RUN/database.db <<'PYEOF'
import sqlite3, struct, sys, statistics
db = sqlite3.connect(sys.argv[1]); rows = db.execute("select pose_prior_id, position from pose_priors").fetchall()
pos = {k: struct.unpack("<3d", b) for k, b in rows if b and len(b) == 24}
alt = statistics.median(p[2] for p in pos.values())
db.executemany("update pose_priors set position=? where pose_prior_id=?", [(struct.pack("<3d", p[0], p[1], alt), k) for k, p in pos.items()])
db.commit(); print(f"flat prior: {len(pos)} altitudes set to {alt:.2f} m")
PYEOF
fi
mkdir -p $RUN/sparse
if [ $NO_GPS = 1 ]; then
  MAX_RSS_MB=5000 $GUARD $RUN/mapper colmap mapper --database_path $RUN/database.db --image_path $IMAGES --output_path $RUN/sparse \
    --Mapper.num_threads $THREADS --Mapper.ba_refine_focal_length 0 --Mapper.ba_refine_extra_params 0
else
  MAX_RSS_MB=5000 $GUARD $RUN/mapper colmap pose_prior_mapper --database_path $RUN/database.db --image_path $IMAGES --output_path $RUN/sparse \
    --Mapper.num_threads $THREADS --Mapper.ba_refine_focal_length 0 --Mapper.ba_refine_extra_params 0 \
    --overwrite_priors_covariance 1 --prior_position_std_x $GPS_STD --prior_position_std_y $GPS_STD --prior_position_std_z $GPS_STD_Z
fi
# the mapper numbers models in the order it found them, so model 0 can be a two-image fragment
# (2026-10-23: 2 images in 0, 197 in 1); move the model with the most images to 0
BIG=$(for m in $RUN/sparse/*/; do echo "$(colmap model_analyzer --path $m 2>&1 | sed -n 's/.*Registered images: //p') $m"; done | sort -rn | head -1 | cut -d' ' -f2)
if [ -n "$BIG" ] && [ "${BIG%/}" != "$MODEL" ]; then
  mv $MODEL $RUN/sparse/swap && mv ${BIG%/} $MODEL && mv $RUN/sparse/swap ${BIG%/}; log "largest model was ${BIG:t}, moved to 0"; fi
mkdir -p $MODEL/txt; colmap model_converter --input_path $MODEL --output_path $MODEL/txt --output_type TXT > /dev/null 2>&1
ls -d $RUN/sparse/*/ | wc -l | xargs -I{} echo "models: {}" | tee -a $SCENE/scene.log
colmap model_analyzer --path $MODEL 2>&1 | grep -E "Registered images|Points|Mean reprojection" | sed 's/.*\] //' | tee -a $SCENE/scene.log
