"""Detect helmet-boundary knob positions (GoPro Max stitch-seam hypothesis)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from helmet_mask.boundary import gaussian_smooth_wrapped


def load_boundary_rows(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text())
    return np.asarray(payload["boundary_rows"], dtype=np.float64)


def detect_knob_fractions(
    boundary: np.ndarray,
    *,
    smooth_sigma: float = 24.0,
    deviation_threshold_px: float = 3.0,
    min_run_cols: int = 6,
) -> list[float]:
    """Return knob center columns as fractions of image width in [0, 1).

  Knobs are contiguous runs where the boundary sits above a heavily-smoothed
  baseline (smaller row index = visually higher).
  """
    width = boundary.shape[0]
    if width == 0:
        return []

    baseline = gaussian_smooth_wrapped(boundary, smooth_sigma)
    upward = baseline - boundary
    active = upward >= deviation_threshold_px

    knobs: list[float] = []
    start: int | None = None
    for index in range(width + 1):
        on = index < width and active[index]
        if on and start is None:
            start = index
        elif not on and start is not None:
            run_len = index - start
            if run_len >= min_run_cols:
                center_col = start + (run_len - 1) / 2.0
                knobs.append(center_col / width)
            start = None

    return knobs


def detect_knobs_from_json(path: Path, **kwargs: object) -> list[float]:
    boundary = load_boundary_rows(path)
    return detect_knob_fractions(boundary, **kwargs)  # type: ignore[arg-type]


def compare_knob_clusters(
  case_knobs: dict[str, list[float]],
  cluster_tolerance: float = 0.02,
) -> dict[str, object]:
    """Summarize whether knob fractions cluster across cases."""
    all_fractions = [f for knobs in case_knobs.values() for f in knobs]
    clusters: list[dict[str, object]] = []
    for fraction in sorted(all_fractions):
        if any(abs(fraction - float(c["center"])) <= cluster_tolerance for c in clusters):
            continue
        members = [
            name
            for name, knobs in case_knobs.items()
            if any(abs(fraction - k) <= cluster_tolerance for k in knobs)
        ]
        if members:
            clusters.append({"center": fraction, "cases": members})

    seam_targets = [0.25, 0.75]
    aligned: list[dict[str, object]] = []
    for target in seam_targets:
        hits = [
            name
            for name, knobs in case_knobs.items()
            if any(abs(k - target) <= cluster_tolerance for k in knobs)
        ]
        aligned.append({"seam_fraction": target, "cases": hits})

    return {
        "case_knobs": case_knobs,
        "clusters": clusters,
        "seam_alignment": aligned,
        "cluster_tolerance": cluster_tolerance,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect helmet-boundary knob positions from boundary.json",
    )
    parser.add_argument(
        "--boundary-json",
        type=Path,
        required=True,
        help="Per-column boundary JSON from helmet-mask",
    )
    parser.add_argument(
        "--smooth-sigma",
        type=float,
        default=24.0,
        help="Heavy Gaussian sigma for baseline curve (columns)",
    )
    parser.add_argument(
        "--deviation-threshold-px",
        type=float,
        default=3.0,
        help="Minimum upward deviation (px) to count as knob",
    )
    parser.add_argument(
        "--min-run-cols",
        type=int,
        default=6,
        help="Minimum contiguous columns for a knob run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    knobs = detect_knobs_from_json(
        args.boundary_json,
        smooth_sigma=args.smooth_sigma,
        deviation_threshold_px=args.deviation_threshold_px,
        min_run_cols=args.min_run_cols,
    )
    for fraction in knobs:
        print(f"{fraction:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
