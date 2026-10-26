# Helmet Mask

Derive a dataset-specific helmet/rig mask from 360° equirectangular (ERP) image sequences.

The helmet is **rigidly attached to the camera**, so across frames helmet pixels are nearly constant while world pixels change. Per-pixel **temporal stability** (MAD of RGB + chroma consistency) identifies the rig; chroma resists glossy specular flicker. Candidate pixels are restricted to the lower ERP band to avoid confusing the sky (also low variance).

Unlike a raw thresholded region (spotty holes, isolated patches, leaks above the band), this package extracts a **single clean boundary curve** per image column — exploiting the shape prior that a helmet below the camera forms one contiguous band across the full width, bounded above by a smooth **upper envelope** with occasional mount bumps ("Kanten").

## Install

```bash
pip install -e helmet-mask/
```

## CLI

```bash
helmet-mask \
  --input /path/to/erp/jpegs \
  --out-mask mask.png \
  --overlay-dir overlays/ \
  --boundary-json boundary.json
```

Optional: `--apply-dir` writes masked JPG copies (helmet blacked out, **GPS EXIF preserved**).

## Python API

```python
from pathlib import Path
from helmet_mask import run_helmet_mask

summary = run_helmet_mask(
    Path("imagery/geo360-sample10/raw"),
    Path("qa/helmet_mask/mask.png"),
    Path("qa/helmet_mask/overlays"),
    boundary_json=Path("qa/helmet_mask/boundary.json"),
)
print(summary["masked_ratio"], summary["leak_score"])
```

## Algorithm

1. **Temporal scoring** — per-pixel MAD + chroma inconsistency across the frame stack.
2. **Hysteresis thresholding** — strict percentile seeds (`--stability-percentile`); relaxed cutoff (`strict × --relaxed-factor`, default 1.8×) grows detection along connected components from seeds. Weakly-scored crest pixels (specular red tops) join the mask when connected to stable body pixels; isolated background noise does not.
3. **Morphology** — closing, opening, small-blob removal, optional dilation.
4. **Per-column top boundary** — topmost detected row per column (upper envelope of detections).
5. **Envelope regularization** (all horizontal ops **wrap around** for ERP continuity):
   - Reject only **narrow** upward spikes (< `--spike-min-run` cols) deviating > k×MAD above local trend
   - Reject only **narrow** downward notches (< `--notch-min-run` cols, default 48) deviating > k×MAD below local trend (bridges narrow helmet seam gaps before the envelope step)
   - Wrapped rolling **minimum** (`--envelope-window`, default 15 cols) — hugs the crest
   - Light wrapped Gaussian smooth (`--gaussian-sigma`, capped to never dip below pre-smooth envelope)
   - **Vector smoothing** (`--simplify-epsilon`, default 24 px): wrapped RDP on a lightly pre-smoothed curve, linear interpolation back to per-column rows, then selective Gaussian on near-collinear segments (corners with turn angle > ~50° guarded). Capped to never leak more than 1 px below the pre-simplify envelope.
   - **Physical shape prior** (wired in): wrapped rolling-min **coarse silhouette** (`--coarse-window`, default 200 cols) limits downward sag to `--max-sag` (default 20 px at working res). **Stitch-seam exceptions** at `--seam-fracs` (default GoPro Max `0.25,0.75`) allow deeper dips up to `--seam-sag` (default 70 px) within ±`--seam-halfwidth` (default 3% of width). ERP **wrap continuity** enforced at column 0 / W−1.
   - Subtract `--margin-px` safety margin (move boundary up)
6. **Final mask** — every pixel from boundary row down to bottom edge, per column.
7. **Leak self-check** — `leak_score` = fraction of relaxed-threshold pixels in a `--leak-band-px` band above the boundary (should be ~0 when the envelope hugs the crest).

## Tunables

| Flag | Default | Purpose |
|------|---------|---------|
| `--region-top-frac` | 0.45 | Ignore sky: only search below this vertical fraction |
| `--downscale` | 1440x720 | Working resolution |
| `--stability-percentile` | 12 | Strict seed threshold (lowest N% stability) |
| `--relaxed-factor` | 1.8 | Hysteresis grow: relaxed percentile = strict × factor |
| `--dilate-px` | 12 | Morphological dilation before boundary extraction |
| `--min-blob-frac` | 0.0005 | Drop tiny connected components |
| `--margin-px` | 4 | Safety margin up at working res (scaled to full) |
| `--coarse-window` | 200 | Coarse silhouette rolling-min window (cols) |
| `--max-sag` | 20 | Max sag below coarse line outside seam zones (working px) |
| `--seam-fracs` | 0.25,0.75 | GoPro Max stitch seam x/W fractions (comma-separated) |
| `--seam-halfwidth` | 0.03 | Half-width of each seam exception zone (fraction of W) |
| `--seam-sag` | 70 | Allowed sag inside seam zones (working px) |
| `--envelope-window` | 15 | Wrapped rolling-minimum window (cols) |
| `--spike-min-run` | 8 | Only reject upward spikes narrower than this (cols) |
| `--notch-min-run` | 48 | Only reject downward notches narrower than this (cols) |
| `--outlier-mad-k` | 4.5 | Spike/notch rejection: k×MAD from local trend |
| `--gaussian-sigma` | 2.0 | Wrapped Gaussian smoothing (capped to envelope) |
| `--simplify-epsilon` | 3.0 | RDP polyline simplification in px (0 disables vector smoothing) |
| `--leak-band-px` | 60 | Band above boundary for leak_score metric |

## Texture method (`--method texture`)

```bash
helmet-mask --method texture --input frames/ --out-mask mask.png --boundary-json boundary.json --run-json run.json
```

**What.** The default method (above) flags every pixel that keeps its *colour*, so uniform world regions that never change (a grey path, clear sky, a wall) can be masked together with the helmet. The texture method only lets pixels with image *structure* vote: structure identical in every frame (gradient-vector coherence plus local NCC against the temporal median, `S = (R + NCC) / 2`) is rig, structure that changes is world, flat pixels abstain. Per column the boundary is the lowest row that maximises the bottom-up vote sum, so flat stretches above the helmet are never claimed and flat holes inside it (glossy or plain interior) are filled. It is then regularised (wrapped median, closing of 18% of the width, rolling minimum, Gaussian, `--texture-buffer-px`). Implementation: `helmet_mask/texture.py` (scipy only); all lengths are working pixels at `--downscale` (default 1440x720).

**When.** Footage where the world has large uniform areas next to the helmet, or where the stability method leaks. It needs some texture on the helmet rim and a moving world, like the default method.

**Static mask.** Measured: the helmet is fixed in image space (vertical shift between frames: std 0.2 px on the Elsass set, about 1 px on Atelier). Per-frame offsets would only add noise, so with `--method texture` they are switched off (`--per-frame-offsets` is ignored, no `offsets.json`) and the single static mask is used for everything: `--out-mask`, overlays, `--apply-dir`. Outputs keep their format (mask PNG white=keep, overlays, `--boundary-json` without `leak_score`); `--run-json` and the returned summary record `method` and all `texture_params`.

| Flag | Default | Purpose |
|------|---------|---------|
| `--method` | stability | `stability` or `texture` |
| `--texture-frames` | 40 | Uniformly sampled frames (more is slower, rarely better) |
| `--texture-buffer-px` | 6 | Safety margin moving the boundary up (working px) |
| `--texture-static-thr` | 0.6 | Score at or above which textured pixels vote "rig"; raise if the world leaks in, lower if the helmet is cut |
| `--texture-moving-thr` | 0.35 | Score at or below which textured pixels vote "world" |
| `--region-top-frac` | 0.45 | Shared with the default method: nothing above is masked |

Other texture parameters are constants in `TextureParams` (`helmet_mask/texture.py`); the Python API accepts them via `run_helmet_mask(..., method="texture", texture_params={...})` or `derive_mask_texture(stack, **params)`.

## Limitations

- Needs **≥5 frames** from the same rig (≥3 minimum; more is better).
- Assumes a **moving world / static rig** — works for walk/drive captures, not spinning-camera setups.
- Very dark or featureless helmets may need tuning `--stability-percentile` and `--margin-px`.
- **Semi-rigid rig parts** (e.g. handlebar bags that shift slightly relative to the camera between frames) are only partially detected by temporal stability. Expect incomplete masks for those elements; manual mask touch-up is recommended for rigs with moving accessories.

## GoPro Max shape prior (wired into masking)

Helmet-boundary **knobs** (“Kanten”) — local upward bumps — may correspond to mount hardware and/or GoPro Max stitch seams. The masking pipeline now enforces a **physical sag prior**: the boundary may not dip more than `--max-sag` below a coarse wrapped rolling-min silhouette, except at tunable stitch-seam bands (`--seam-fracs`, default **0.25** and **0.75** for GoPro Max) where `--seam-sag` allows the genuine merged-lens notch (extra ground visible).

Knob detection for exploratory analysis (`helmet_mask/knobs.py`): compare the final boundary to a heavily Gaussian-smoothed baseline; contiguous runs where the boundary sits ≥ 3 px above the baseline yield knob center columns as fractions of width.

```bash
helmet-mask-knobs --boundary-json helmet-mask/test-runs/sample10-red/boundary.json
```

### Knob-center fractions from four reference datasets

| Case | Source imagery | Knob centers (x/W) |
|------|----------------|--------------------|
| `sample10-red` | `geo360-sample10/raw` (10 frames) | 0.041, 0.154, 0.227, 0.386, 0.438, 0.473, 0.536, 0.609, 0.660, 0.673, 0.723, 0.757, 0.820, 0.909, 0.930, 0.960 |
| `mid30-red` | `geo360-mid30/raw` (30 frames) | 0.098, 0.225, 0.311, 0.469, 0.556, 0.630, 0.681, 0.739, 0.925, 0.985 |
| `lpg2024-red` | `geo360-lpg-2024-mid20-raw` (20 frames) | 0.032, 0.245, 0.352, 0.444, 0.496, 0.568, 0.645, 0.718, 0.924 |
| `admiral-forward30-blue` | `geo360-admiral-nord-20260418-forward30-raw` (30 frames) | 0.045, 0.086, 0.151, 0.216, 0.276, 0.338, 0.368, 0.412, 0.449, 0.522, 0.539, 0.557, 0.631, 0.789, 0.834, 0.918, 0.989 |

### Seam-analysis verdict (four clean reference runs)

| Location (x/W) | Cases | Likely meaning |
|----------------|-------|----------------|
| **~0.24–0.25** | **3 of 4** | Left stitch seam (candidate) |
| **~0.5** | **3 of 4** | Camera rear (candidate) |
| **~0.75** | **1 of 4** | Symmetric right stitch — weak |

**Not reliable enough for a universal GoPro Max prior.** `mid30-red` uses the **same red helmet** as `sample10-red` but **lacks** the ~0.25 knob — session-specific geometry and detection dominate. Each case also shows many additional knobs from helmet shape, mount hardware, and detection noise.

**Policy:** Stitch positions are **wired in** via `--seam-fracs` / `--seam-sag` (defaults match GoPro Max). Run `helmet-mask-knobs` on new footage as a **soft sanity check** when tuning seam bands.

Regenerate reference boundaries: `python helmet-mask/examples/run_examples.py [--case NAME]` (see [test-runs/README.md](test-runs/README.md)).

## License

Same as parent project.
