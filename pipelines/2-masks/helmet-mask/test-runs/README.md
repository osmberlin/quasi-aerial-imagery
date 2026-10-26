# Helmet-mask test runs

Reference outputs from `python helmet-mask/examples/run_examples.py` (optionally `--case NAME`).

## Layout

Each case lives under `test-runs/<case>/`:

| Path | Description |
|------|-------------|
| `sources/` | Symlinks to the exact input ERP JPGs (never copies) |
| `mask.png` | Derived keep mask (white=keep, black=masked) |
| `masked/` | Mask-applied JPGs (helmet blacked; GPS EXIF preserved) |
| `overlays/` | Magenta boundary overlay for **every** source frame |
| `boundary.json` | Per-column boundary row indices (+ leak metadata) |
| `offsets.json` | Per-frame vertical offsets, NCC confidence, per-frame leak scores |
| `run.json` | Manifest: ordered inputs, parameters, masked %, leak_score, runtime |

## Cases

| Case | Source imagery |
|------|----------------|
| `sample10-red` | `imagery/geo360-sample10/raw` |
| `mid30-red` | `imagery/geo360-mid30/raw` |
| `lpg2024-red` | `imagery/geo360-lpg-2024-mid20-raw` |
| `admiral-forward30-blue` | `imagery/geo360-admiral-nord-20260418-forward30-raw` |

## Regenerate

```bash
python helmet-mask/examples/run_examples.py              # all cases
python helmet-mask/examples/run_examples.py --case sample10-red
```
