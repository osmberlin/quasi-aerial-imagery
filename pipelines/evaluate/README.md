# Evaluate: compare with something outside the pipeline

Internal agreement (frames that agree with each other, small GPS residuals) is not accuracy. Accuracy
needs a reference: a tape measure on site, surveyed lines, or an official orthophoto.
What has been measured so far: [docs/learnings.md#accuracy](../../docs/learnings.md#accuracy).

| Script | Reference | Where |
|---|---|---|
| [`kerb_reference.py`](kerb_reference.py) | kerb and marking lines of Berlin's Straßenbefahrung 2014 (open WFS): absolute distance, distance after a rigid fit, continuity along the kerb | Berlin |
| [`register_to_reference.py`](register_to_reference.py) | one 2D similarity to the same kerb lines, checked on held-out blocks; writes registered copies (geotransform only) | Berlin |
| [`reference_raster.py`](reference_raster.py) | draws reference lines into an RGBA overlay on the grid of an ortho, for the viewer | any GeoJSON |
| [`fetch_ign_ortho.py`](fetch_ign_ortho.py) | IGN orthophoto (Géoplateforme WMS, 20 cm, Etalab 2.0) for the window of a scene, as a visual reference | France |
| [`compare_with_reference.py`](compare_with_reference.py) | any XYZ orthophoto: warps it onto our grid and writes a side-by-side JPEG | anywhere |

`scene_ground.sh` runs the kerb metrics and the registration by itself when the scene is in Berlin
(`FETCH_REFERENCE=1` downloads the lines from the WFS) or when `REFERENCE_DIR` holds
`bd_bordstein.geojson`. `run_scene.sh` runs `compare_with_reference.py` when `REFERENCE_URL_VAR` names a
line in `<workspace>/secrets.env`:

```bash
# <workspace>/secrets.env, outside the repository; the URL may contain a token
REFERENCE_TILES_URL='https://tiles.example.org/{z}/{x}/{y}.jpg'
```

Widths: measure them by hand in the [viewer](../../viewer/README.md) (two clicks), at the spots you
measured on site. Check an orthophoto's date before using it: the IGN image near Obernai was older
than the new cycle path.
