#!/bin/zsh
# Standard ground product for a scene that run_scene.sh has reconstructed (D-099):
#   1. semantic labels per frame (Mask2Former Mapillary Vistas)
#   2. base nearest mosaic with labels (no exclusion; for the car layer and as comparison)
#   3. frame co-registration on the ground (vehicles/persons excluded, ROUNDS rounds)
#   4. ground without cars: nearest (with source map) and two-band blend
#   5. car layer from the base label vote
#   6. if REFERENCE (or Berlin WFS) is available: kerb/marking metrics and one similarity
#      registration; registered copies of the delivered rasters
# Renders run in a grid of square tiles of about TILE_M metres; tiles without track are skipped, the
# rest merged. Every render runs under common/guarded_run.sh; JOBS tiles run side by side.
#
# Usage: scene_ground.sh <scene_dir> <frames_dir> <camera_height_m> [out_dir=<scene_dir>/ground]
# Steps 1 and 3-5 use 2-masks/segment and 4-ground; step 6 uses evaluate/. Details: README.md next to this script.
# Env: GSD (output pixel size, default 0.02 m), RENDER_ARGS (extra renderer options), LABELS (existing label dir), ROUNDS (2), TILE_M (tile size, 50 m), TILE_MARGIN (metres rendered around each tile and dropped, 2), JOBS (tiles rendered side by side, 1),
#      GROUND_MODEL (npz surface, optional),
#      REFERENCE_DIR (folder with bd_bordstein.geojson / be_fahrbahnmarkierunglinie.geojson);
#      FETCH_REFERENCE=1 downloads them from the Berlin Straßenbefahrung WFS for the scene bbox
#      (only when the scene CRS is EPSG:25833 and the scene lies in Berlin; otherwise skipped),
#      ORTHO_CRS (scene CRS, e.g. EPSG:25832; default: UTM zone at the median GPS position, EPSG:25833 in Berlin),
#      RENDER_MASK_DIR (per-frame ERP masks DIR/<frame file name>.png, white = usable, see render_ground_ortho.py
#      --mask-dir; a frame without its file uses the scene's colmap/helmet_mask.png, if there is one),
#      PYTHON (default pipelines/.venv/bin/python), SEGMENT_PYTHON (default 2-masks/segment/.venv/bin/python),
#      MAX_RSS_MB (3500), MIN_FREE_GB (6), MAX_SWAP_GROWTH_MB (2500): watchdog limits per render tile.
case "${1:-}" in -h|--help|"") sed -n '2,/^case /p' $0 | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;; esac
set -eu
SCENE=${1:A}; FRAMES=${2:A}; HEIGHT=$3; OUT=${4:-$SCENE/ground}; OUT=${OUT:A}
HERE=${0:A:h}; P=${PYTHON:-$HERE/.venv/bin/python}; G=$HERE/common/guarded_run.sh; R=$HERE/4-ground/render_ground_ortho.py
ROUNDS=${ROUNDS:-2}; TILE_M=${TILE_M:-50}; TILE_MARGIN=${TILE_MARGIN:-2}; JOBS=${JOBS:-1}
[ -n "${TILES:-}" ] && { echo "TILES was replaced by TILE_M (square tiles, metres)" >&2; exit 2; }
GV=(MAX_RSS_MB=${MAX_RSS_MB:-3500} MIN_FREE_GB=${MIN_FREE_GB:-6} MAX_SWAP_GROWTH_MB=${MAX_SWAP_GROWTH_MB:-2500})
mkdir -p $OUT
LOG=$OUT/scene_ground.log
say() { echo "=== $(date '+%F %T') $*" | tee -a $LOG; }
MODEL=$SCENE/colmap/run/sparse/0
MASK=$(ls $SCENE/colmap/helmet_mask.png 2>/dev/null || true)
BASEARGS=(--model $MODEL --images $FRAMES --camera-height $HEIGHT)
[ -n "$MASK" ] && BASEARGS+=(--mask $MASK)
[ -n "${GROUND_MODEL:-}" ] && BASEARGS+=(--ground-model ${GROUND_MODEL:A})
[ -n "${ORTHO_CRS:-}" ] && BASEARGS+=(--crs $ORTHO_CRS)
[ -n "${RENDER_MASK_DIR:-}" ] && BASEARGS+=(--mask-dir ${RENDER_MASK_DIR:A})
[ -n "${GSD:-}" ] && BASEARGS+=(--gsd $GSD)  # output pixel size in metres (renderer default 0.02)
# extra renderer options for every render, e.g. RENDER_ARGS="--strip 10 --max-range 12" for a wider strip
[ -n "${RENDER_ARGS:-}" ] && BASEARGS+=(${=RENDER_ARGS})

# 1. labels
LABELS=${LABELS:-$OUT/labels}; LABELS=${LABELS:A}
NFR=$(ls $FRAMES/*.(jpg|JPG|jpeg) | wc -l | tr -d ' ')
if [ ! -f $LABELS/labels.json ] || [ $(ls $LABELS/*.png 2>/dev/null | wc -l | tr -d ' ') -lt $NFR ]; then
  say "segmentation of $NFR frames"
  env $GV MAX_RSS_MB=7000 $G $OUT/run-seg ${SEGMENT_PYTHON:-$HERE/2-masks/segment/.venv/bin/python} $HERE/2-masks/segment/segment_frames.py $FRAMES $LABELS > /dev/null
fi

EXTOUT=$($P $R "${BASEARGS[@]}" --output $OUT/unused.tif --print-extent --print-tiles $TILE_M)
EXT=($(echo $EXTOUT | grep EXTENT | cut -d' ' -f2-))
CRS=$(echo $EXTOUT | grep '^CRS ' | cut -d' ' -f2)
TILEBOX=("${(@f)$(echo $EXTOUT | grep '^TILE ' | cut -d' ' -f2-)}")
say "extent ${EXT[*]} ($CRS), ${#TILEBOX} tiles of about $TILE_M m with track"
render_one() {  # render_one <name> <tile number> <args...>; a tile with its .json is finished and kept
  local name=$1 t=$2; shift 2
  local tile=$OUT/tiles-$name/$t extra=()
  [ -n "${ALSO_TWOBAND:-}" ] && extra=(--twoband-output $tile.twoband.tif)
  [ -f $tile.json ] && return 0
  env $GV $G $OUT/run-$name-$t $P $R "${BASEARGS[@]}" "$@" "${extra[@]}" --bbox ${=TILEBOX[$t]} --margin $TILE_MARGIN --output $tile.tif > /dev/null
}
render_tiled() {  # render_tiled <name> <args...>: every tile (JOBS lanes side by side), then merge; ALSO_TWOBAND=<name2> also merges the two-band blend
  local name=$1; shift
  [ -f $OUT/$name.tif ] && { [ -z "${ALSO_TWOBAND:-}" ] || [ -f $OUT/$ALSO_TWOBAND.tif ]; } && { say "$name exists"; return 0; }
  local lanes=() j k p fail=0
  for ((j=1; j<=JOBS; j++)); do
    ( for ((k=j; k<=${#TILEBOX}; k+=JOBS)); do render_one $name $k "$@" || exit 1; done ) &
    lanes+=($!)
  done
  for p in $lanes; do wait $p || fail=1; done
  [ $fail = 0 ] || { say "$name: a tile failed (see $OUT/run-$name-*/monitor.log)"; return 1; }
  $P $HERE/4-ground/merge_tiles.py $OUT/$name.tif $OUT/tiles-$name/{1..${#TILEBOX}}.tif > /dev/null
  [ -n "${ALSO_TWOBAND:-}" ] && $P $HERE/4-ground/merge_tiles.py $OUT/$ALSO_TWOBAND.tif $OUT/tiles-$name/{1..${#TILEBOX}}.twoband.tif > /dev/null
  say "$name done"
}

# 2. base mosaic with labels (no exclusion)
say "base"
render_tiled base --labels $LABELS --gain-compensation local --blend nearest --median-k 5 --write-source --no-disagreement

# 3. co-registration
EX=(--labels $LABELS --exclude-groups vehicle person)
if [ ! -f $OUT/offsets.json ]; then
  echo '{}' > $OUT/offsets.tmp.json; OFF=()
  for ((k=1; k<=ROUNDS; k++)); do
    say "co-registration round $k"
    rm -rf $OUT/patches
    env $GV $G $OUT/run-export$k $P $R "${BASEARGS[@]}" "${EX[@]}" "${OFF[@]}" --export-patches $OUT/patches --output $OUT/unused.tif > /dev/null
    env $GV $G $OUT/run-coreg$k $P $HERE/4-ground/coregister_frames.py $OUT/patches $OUT/offsets_delta$k.json > /dev/null
    $P - $OUT/offsets.tmp.json $OUT/offsets_delta$k.json <<'PYEOF'
import json, sys
tot = json.load(open(sys.argv[1])); d = json.load(open(sys.argv[2]))
json.dump({k: [round(tot.get(k, [0, 0])[0] + v[0], 4), round(tot.get(k, [0, 0])[1] + v[1], 4)] for k, v in d.items()}, open(sys.argv[1], "w"))
PYEOF
    OFF=(--frame-offsets $OUT/offsets.tmp.json)
    rm -rf $OUT/patches
  done
  mv $OUT/offsets.tmp.json $OUT/offsets.json
fi
OFF=(--frame-offsets $OUT/offsets.json)

# 4. ground without cars
say "ground nearest and two-band"
ALSO_TWOBAND=ground_twoband render_tiled ground_nearest "${EX[@]}" "${OFF[@]}" --gain-compensation local --blend nearest --median-k 5 --write-source --no-disagreement

# 5. car layer
[ -f $OUT/cars.tif ] || $P $HERE/4-ground/car_layer.py $OUT/base.labels.tif --colour $OUT/base.tif --output $OUT/cars > /dev/null

# 6. reference
REF=${REFERENCE_DIR:-$OUT/reference}
IN_BERLIN=0  # rough check: scene CRS EPSG:25833 and the scene centre inside Berlin (13.0-13.8 E, 52.3-52.7 N)
if [ "${CRS:-}" = EPSG:25833 ]; then
  IN_BERLIN=$($P -c "import rasterio;from pyproj import Transformer;b=rasterio.open('$OUT/base.tif').bounds;lon,lat=Transformer.from_crs('EPSG:25833','EPSG:4326',always_xy=True).transform((b.left+b.right)/2,(b.bottom+b.top)/2);print(int(13.0<=lon<=13.8 and 52.3<=lat<=52.7))")
fi
if [ "${FETCH_REFERENCE:-0}" = 1 ] && [ $IN_BERLIN = 0 ] && [ ! -f $REF/bd_bordstein.geojson ]; then
  say "no Berlin reference for this scene (CRS ${CRS:-unknown}, outside Berlin)"
fi
if [ "${FETCH_REFERENCE:-0}" = 1 ] && [ $IN_BERLIN = 1 ] && [ ! -f $REF/bd_bordstein.geojson ]; then
  mkdir -p $REF
  BB=$($P -c "import rasterio;b=rasterio.open('$OUT/base.tif').bounds;print(f'{b.left:.0f},{b.bottom:.0f},{b.right:.0f},{b.top:.0f}')")
  for L in bd_bordstein be_fahrbahnmarkierunglinie; do
    curl -s -o $REF/$L.geojson "https://gdi.berlin.de/services/wfs/strassenbefahrung?service=WFS&version=2.0.0&request=GetFeature&typeNames=strassenbefahrung:$L&outputFormat=application/json&bbox=$BB,urn:ogc:def:crs:EPSG::25833"
  done
  say "reference fetched for $BB"
fi
if [ -f $REF/bd_bordstein.geojson ]; then
  for V in base ground_nearest; do
    for M in bd_bordstein:curb be_fahrbahnmarkierunglinie:marking; do
      [ -f $REF/${M%%:*}.geojson ] || continue
      $P $HERE/evaluate/kerb_reference.py $OUT/$V.labels.tif $REF/${M%%:*}.geojson --group ${M##*:} --source $OUT/$V.source.tif \
        --smooth 1 --output $OUT/metrics_${V}_${M##*:} > /dev/null || say "metric $V ${M##*:} failed"
    done
  done
  $P $HERE/evaluate/register_to_reference.py $OUT/ground_nearest.labels.tif $REF/bd_bordstein.geojson --output $OUT/register --no-scale \
    --apply $OUT/ground_nearest.tif $OUT/ground_twoband.tif $OUT/cars.tif > /dev/null || say "registration failed"
fi

$P - $OUT <<'PYEOF'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1]); s = {}
for k in sorted(out.glob("offsets_delta*.info.json")):
    s[k.stem] = json.loads(k.read_text())
for k in sorted(out.glob("metrics_*.json")):
    d = json.loads(k.read_text()); c = d.get("continuity", {})
    s[k.stem] = {"length_m": c.get("measured_kerb_length_m"), "wobble_m_rms_p90": c.get("wobble_m_rms_p90"),
                 "share_within": d.get("registered_share_of_near_cells_within")}
r = out / "register.json"
if r.exists():
    d = json.loads(r.read_text()); s["register"] = {k: d[k] for k in ("similarity", "before", "fitted_all")}
c = out / "cars.json"
if c.exists():
    s["cars"] = json.loads(c.read_text())
(out / "summary.json").write_text(json.dumps(s, indent=1))
print(json.dumps(s))
PYEOF
say "done"
