# max2sphere patch (GoPro MAX 2 frame sizes)

[trek-view/max2sphere](https://github.com/trek-view/max2sphere) stitches the two EAC tracks of a GoPro `.360`
file into equirectangular frames. Download it (we used the master zip of 2026-10-14) to
`<workspace>/third-party/max2sphere/` next to this repository, or anywhere and set `MAX2SPHERE` to the
binary (`ingest_360_video.py --max2sphere` also works).
`max2-templates.patch` adds two frame templates and an environment override:

- 5888x1920 tracks (MAX 2 8K, ERP 7680) and 3008x960 tracks (MAX 2 3K, ERP 3840);
- `MAX2SPHERE_BLEND=<px>` overrides the blend of the detected template (`ingest_360_video.py --blend`).

MAX 1 output is unchanged (byte-identical JPEG for one 4096x1344 frame, checked 2026-10-21).
The blend values of the new templates (46 and 23) are estimates, see
`pipelines/1-ingest/README.md` section "MAX 2".

Rebuild after applying (`patch -p1 < max2-templates.patch` inside `third-party/max2sphere`, with
`a/` and `b/` stripped by `-p1`; or edit the `template[]` table by hand):

```
make -f Makefile-MacM1 INCLUDES="-I/opt/homebrew/include" LFLAGS="-L/opt/homebrew/lib"
```

