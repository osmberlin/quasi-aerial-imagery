#!/bin/zsh
# Upload file for OpenAerialMap from a ground image of scene_ground.sh (RGBA GeoTIFF, any CRS):
#   <out>.cog.tif      Cloud Optimized GeoTIFF in EPSG:3857, JPEG quality 90, transparency as a mask
#   <out>.preview.jpg  small JPEG (PREVIEW_PX on the longer side) to look through before the upload
# Usage: export_oam.sh <ground.tif> <out_prefix>
# Environment: QUALITY (90), RESAMPLING (bilinear), PREVIEW_PX (2000). Needs gdalwarp and gdal_translate (GDAL 3.1+).
# Streams through a VRT, so a 30,000 px route does not need to fit in memory.
# Check the result for people and number plates at full zoom before uploading it.
case "${1:-}" in -h|--help|"") sed -n '2,/^case /p' $0 | sed '$d' | sed 's/^# \{0,1\}//'; exit 0;; esac
set -eu
SRC=${1:A}; OUT=${2:A}
QUALITY=${QUALITY:-90}; RESAMPLING=${RESAMPLING:-bilinear}; PREVIEW_PX=${PREVIEW_PX:-2000}
mkdir -p ${OUT:h}
VRT=$OUT.3857.vrt
gdalwarp -q -overwrite -t_srs EPSG:3857 -r $RESAMPLING -of VRT $SRC $VRT
gdal_translate -q -of COG -b 1 -b 2 -b 3 -mask 4 -co COMPRESS=JPEG -co QUALITY=$QUALITY \
  -co OVERVIEW_RESAMPLING=AVERAGE -co BIGTIFF=IF_SAFER --config GDAL_TIFF_INTERNAL_MASK YES $VRT $OUT.cog.tif
gdal_translate -q -of JPEG -b 1 -b 2 -b 3 -outsize $PREVIEW_PX 0 $OUT.cog.tif $OUT.preview.jpg
# a portrait image gets PREVIEW_PX as height instead
W=$(gdalinfo $OUT.cog.tif | sed -n 's/^Size is \([0-9]*\), \([0-9]*\)/\1 \2/p')
[ ${W% *} -lt ${W#* } ] && gdal_translate -q -of JPEG -b 1 -b 2 -b 3 -outsize 0 $PREVIEW_PX $OUT.cog.tif $OUT.preview.jpg
rm -f $VRT $OUT.preview.jpg.aux.xml
echo "$OUT.cog.tif ($(du -h $OUT.cog.tif | cut -f1), size $W px, EPSG:3857); preview $OUT.preview.jpg"
