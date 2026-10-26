#!/usr/bin/env python3
"""Run helmet-mask test cases and write helmet-mask/test-runs/<case>/ outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "helmet-mask") not in sys.path:
    sys.path.insert(0, str(REPO / "helmet-mask"))

from helmet_mask.boundary import boundary_total_variation  # noqa: E402
from helmet_mask.core import derive_mask_from_frames  # noqa: E402
from helmet_mask.io import collect_jpegs, load_frame_stack, run_helmet_mask  # noqa: E402
from helmet_mask.offsets import DEFAULT_FRAME_LEAK_TARGET  # noqa: E402

CASES = [
    {
        "case": "sample10-red",
        "input": REPO / "imagery/geo360-sample10/raw",
    },
    {
        "case": "mid30-red",
        "input": REPO / "imagery/geo360-mid30/raw",
    },
    {
        "case": "lpg2024-red",
        "input": REPO / "imagery/geo360-lpg-2024-mid20-raw",
    },
    {
        "case": "admiral-forward30-blue",
        "input": REPO / "imagery/geo360-admiral-nord-20260418-forward30-raw",
    },
]

TEST_RUNS_ROOT = REPO / "helmet-mask/test-runs"


def resolve_input_dir(path: Path) -> Path:
    if path.is_dir() and (any(path.glob("*.JPG")) or any(path.glob("*.jpg"))):
        return path
    raw = path / "raw"
    if raw.is_dir():
        return raw
    return path


def symlink_sources(source_paths: list[Path], sources_dir: Path) -> None:
    sources_dir.mkdir(parents=True, exist_ok=True)
    for path in source_paths:
        link = sources_dir / path.name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(path.resolve())


def write_run_json(case_dir: Path, summary: dict[str, object]) -> None:
    inputs = [
        {"filename": name, "absolute_path": path}
        for name, path in zip(
            summary["input_filenames"],
            summary["input_paths"],
            strict=True,
        )
    ]
    payload = {
        "inputs": inputs,
        "parameters": {
            "region_top_frac": summary["region_top_frac"],
            "downscale": list(summary["downscale"]),
            "stability_percentile": summary["stability_percentile"],
            "relaxed_factor": summary["relaxed_factor"],
            "dilate_px": summary["dilate_px"],
            "min_blob_frac": summary["min_blob_frac"],
            "margin_px": summary["margin_px_work"],
            "coarse_window": summary["coarse_window"],
            "max_sag": summary["max_sag"],
            "seam_fracs": summary["seam_fracs"],
            "seam_halfwidth": summary["seam_halfwidth"],
            "seam_sag": summary["seam_sag"],
            "envelope_window": summary["envelope_window"],
            "spike_min_run": summary["spike_min_run"],
            "notch_min_run": summary["notch_min_run"],
            "outlier_mad_k": summary["outlier_mad_k"],
            "gaussian_sigma": summary["gaussian_sigma"],
            "simplify_epsilon": summary["simplify_epsilon"],
            "simplify_lift_window": summary["simplify_lift_window"],
            "simplify_gaussian_sigma": summary["simplify_gaussian_sigma"],
            "leak_band_px": summary["leak_band_px"],
            "per_frame_offsets": summary.get("per_frame_offsets", True),
            "max_offset_px": summary.get("max_offset_px", 120),
            "frame_leak_target": summary.get("frame_leak_target", DEFAULT_FRAME_LEAK_TARGET),
            "refine_max_extra": summary.get("refine_max_extra", 30),
        },
        "masked_ratio": round(float(summary["masked_ratio"]), 6),
        "leak_score": round(float(summary["leak_score"]), 6),
        "boundary_tv_after": round(float(summary["boundary_tv_after"]), 3),
        "boundary_tv_before": round(float(summary["boundary_tv_before"]), 3),
        "runtime_s": round(float(summary["runtime_s"]), 3),
    }
    if "worst_frame_leak_score" in summary:
        payload["worst_frame_leak_score"] = round(float(summary["worst_frame_leak_score"]), 6)
        payload["worst_frame_filename"] = summary["worst_frame_filename"]
        payload["worst_frame_dy"] = int(summary["worst_frame_dy"])
        payload["dy_min"] = int(summary["dy_min"])
        payload["dy_max"] = int(summary["dy_max"])
        payload["low_confidence_count"] = int(summary["low_confidence_count"])
        payload["leak_target_unmet_count"] = int(summary.get("leak_target_unmet_count", 0))
    (case_dir / "run.json").write_text(json.dumps(payload, indent=2) + "\n")


def run_case(case_def: dict[str, object]) -> dict[str, object] | None:
    case_name = str(case_def["case"])
    input_dir = resolve_input_dir(Path(case_def["input"]))
    if not input_dir.is_dir():
        print(f"{case_name}: MISSING input {input_dir}")
        return None

    paths = collect_jpegs(input_dir)
    if len(paths) < 3:
        print(f"{case_name}: need >=3 JPGs in {input_dir}, found {len(paths)}")
        return None

    case_dir = TEST_RUNS_ROOT / case_name
    sources_dir = case_dir / "sources"
    masked_dir = case_dir / "masked"
    overlay_dir = case_dir / "overlays"

    symlink_sources(paths, sources_dir)

    summary = run_helmet_mask(
        input_dir,
        case_dir / "mask.png",
        overlay_dir,
        apply_dir=masked_dir,
        boundary_json=case_dir / "boundary.json",
        offsets_json=case_dir / "offsets.json",
        all_frames=True,
    )

    frames, _work_size = load_frame_stack(paths, summary["downscale"])
    derive_kwargs = {
        "region_top_frac": float(summary["region_top_frac"]),
        "stability_percentile": float(summary["stability_percentile"]),
        "relaxed_factor": float(summary["relaxed_factor"]),
        "dilate_px": int(summary["dilate_px"]),
        "min_blob_frac": float(summary["min_blob_frac"]),
        "margin_px": int(summary["margin_px_work"]),
        "envelope_window": int(summary["envelope_window"]),
        "spike_min_run": int(summary["spike_min_run"]),
        "notch_min_run": int(summary["notch_min_run"]),
        "outlier_mad_k": float(summary["outlier_mad_k"]),
        "gaussian_sigma": float(summary["gaussian_sigma"]),
        "leak_band_px": int(summary["leak_band_px"]),
    }
    _, boundary_before, _ = derive_mask_from_frames(
        frames,
        **derive_kwargs,
        simplify_epsilon=0.0,
    )
    _, boundary_after_work, _ = derive_mask_from_frames(
        frames,
        **derive_kwargs,
        simplify_epsilon=float(summary["simplify_epsilon"]),
    )
    tv_before = boundary_total_variation(boundary_before)
    tv_after = boundary_total_variation(boundary_after_work)

    summary = {**summary, "boundary_tv_before": tv_before, "boundary_tv_after": tv_after}
    write_run_json(case_dir, summary)

    result = {
        "case": case_name,
        "frame_count": summary["frame_count"],
        "masked_ratio": summary["masked_ratio"],
        "leak_score": summary["leak_score"],
        "worst_frame_leak_score": summary.get("worst_frame_leak_score", summary["leak_score"]),
        "boundary_tv_before": tv_before,
        "boundary_tv_after": tv_after,
        "runtime_s": summary["runtime_s"],
        "case_dir": str(case_dir),
    }
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run helmet-mask test cases")
    parser.add_argument(
        "--case",
        choices=[c["case"] for c in CASES],
        default=None,
        help="Run one case (default: all)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    selected = [c for c in CASES if args.case is None or c["case"] == args.case]

    print(
        f"{'Case':<24} {'Frames':>6} {'Masked%':>8} {'Leak':>7} "
        f"{'Worst':>7} {'BndTV':>8} {'Time':>6}"
    )
    print("-" * 70)

    for case_def in selected:
        result = run_case(case_def)
        if result is None:
            continue
        print(
            f"{result['case']:<24} "
            f"{result['frame_count']:>6} "
            f"{float(result['masked_ratio']) * 100:>7.1f}% "
            f"{float(result['leak_score']):>7.4f} "
            f"{float(result.get('worst_frame_leak_score', result['leak_score'])):>7.4f} "
            f"{float(result['boundary_tv_after']):>8.1f} "
            f"{float(result['runtime_s']):>5.1f}s"
        )
        print(f"  -> {result['case_dir']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
