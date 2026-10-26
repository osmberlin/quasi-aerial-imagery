"""CLI entry point for helmet-mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from helmet_mask.boundary import parse_seam_fracs
from helmet_mask.io import parse_size, run_helmet_mask
from helmet_mask.offsets import DEFAULT_FRAME_LEAK_TARGET
from helmet_mask.texture import TextureParams

TEXTURE_DEFAULTS = TextureParams()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Derive a helmet/rig mask from temporally stable pixels in ERP frames.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Directory of ERP JPGs")
    parser.add_argument(
        "--out-mask",
        type=Path,
        required=True,
        help="Output mask PNG (white=keep, black=masked)",
    )
    parser.add_argument(
        "--overlay-dir",
        type=Path,
        default=None,
        help="Directory for review overlays with magenta boundary",
    )
    parser.add_argument(
        "--apply-dir",
        type=Path,
        default=None,
        help="Optional directory for masked JPG copies (masked region blacked)",
    )
    parser.add_argument(
        "--boundary-json",
        type=Path,
        default=None,
        help="Write per-column boundary row indices as JSON",
    )
    parser.add_argument(
        "--method",
        choices=["stability", "texture"],
        default="stability",
        help="stability: colour stability (default). texture: static-texture votes, one static mask "
        "(per-frame offsets are off)",
    )
    parser.add_argument(
        "--run-json",
        type=Path,
        default=None,
        help="Write the run summary (method, parameters, results) as JSON",
    )
    parser.add_argument(
        "--region-top-frac",
        type=float,
        default=0.45,
        help="Only consider pixels below this vertical fraction as helmet candidates",
    )
    parser.add_argument(
        "--downscale",
        type=parse_size,
        default=(1440, 720),
        help="Working resolution WIDTHxHEIGHT",
    )
    parser.add_argument(
        "--stability-percentile",
        type=float,
        default=12.0,
        help="Strict seed threshold: lowest N%% stability scores inside the candidate band",
    )
    parser.add_argument(
        "--relaxed-factor",
        type=float,
        default=1.8,
        help="Hysteresis grow factor on stability percentile (relaxed = strict × factor)",
    )
    parser.add_argument(
        "--dilate-px",
        type=int,
        default=12,
        help="Morphological dilation radius at working resolution before boundary extraction",
    )
    parser.add_argument(
        "--min-blob-frac",
        type=float,
        default=0.0005,
        help="Keep connected components at least this fraction of the frame",
    )
    parser.add_argument(
        "--margin-px",
        type=int,
        default=4,
        help="Safety margin moving boundary up at working resolution (scaled to full res)",
    )
    parser.add_argument(
        "--coarse-window",
        type=int,
        default=200,
        help="Wrapped rolling-min window for coarse silhouette sag prior (cols)",
    )
    parser.add_argument(
        "--max-sag",
        type=float,
        default=20.0,
        help="Max downward sag below coarse silhouette at working res (px)",
    )
    parser.add_argument(
        "--seam-fracs",
        type=str,
        default="0.25,0.75",
        help="Comma-separated stitch seam x/W fractions (GoPro Max default)",
    )
    parser.add_argument(
        "--seam-halfwidth",
        type=float,
        default=0.03,
        help="Half-width of each seam exception zone as fraction of width",
    )
    parser.add_argument(
        "--seam-sag",
        type=float,
        default=70.0,
        help="Allowed sag below coarse line inside seam zones at working res (px)",
    )
    parser.add_argument(
        "--envelope-window",
        type=int,
        default=15,
        help="Wrapped rolling-minimum window in columns (upper envelope)",
    )
    parser.add_argument(
        "--spike-min-run",
        type=int,
        default=8,
        help="Only reject upward spikes narrower than this many contiguous columns",
    )
    parser.add_argument(
        "--notch-min-run",
        type=int,
        default=48,
        help="Only reject downward notches narrower than this many contiguous columns",
    )
    parser.add_argument(
        "--outlier-mad-k",
        type=float,
        default=4.5,
        help="Reject narrow spikes/notches deviating more than k×MAD from local trend",
    )
    parser.add_argument(
        "--gaussian-sigma",
        type=float,
        default=2.0,
        help="Wrapped Gaussian smoothing sigma in columns (capped to envelope)",
    )
    parser.add_argument(
        "--simplify-epsilon",
        type=float,
        default=24.0,
        help="RDP polyline simplification epsilon in px (0 disables vector smoothing)",
    )
    parser.add_argument(
        "--simplify-lift-window",
        type=int,
        default=75,
        help="Rolling-topmost lift window in columns before vector RDP",
    )
    parser.add_argument(
        "--simplify-gaussian-sigma",
        type=float,
        default=3.0,
        help="Selective Gaussian sigma on near-collinear vector segments",
    )
    parser.add_argument(
        "--leak-band-px",
        type=int,
        default=60,
        help="Working-res band above boundary for leak_score self-check",
    )
    parser.add_argument(
        "--per-frame-offsets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Estimate per-frame vertical boundary offsets (default: on)",
    )
    parser.add_argument(
        "--max-offset-px",
        type=int,
        default=120,
        help="Max vertical NCC search range and accepted offset at working resolution",
    )
    parser.add_argument(
        "--frame-leak-target",
        type=float,
        default=DEFAULT_FRAME_LEAK_TARGET,
        help="Per-frame palette leak refinement target (fraction of band above boundary)",
    )
    parser.add_argument(
        "--refine-max-extra",
        type=int,
        default=30,
        help="Max working-res dy above NCC estimate during leak refinement",
    )
    parser.add_argument(
        "--texture-frames",
        type=int,
        default=TEXTURE_DEFAULTS.n_frames,
        help="--method texture: number of uniformly sampled frames used",
    )
    parser.add_argument(
        "--texture-buffer-px",
        type=float,
        default=TEXTURE_DEFAULTS.buffer_px,
        help="--method texture: safety margin moving the boundary up (working px)",
    )
    parser.add_argument(
        "--texture-static-thr",
        type=float,
        default=TEXTURE_DEFAULTS.thr_static,
        help="--method texture: textured pixels scoring >= this vote 'rig' (0-1, higher = stricter)",
    )
    parser.add_argument(
        "--texture-moving-thr",
        type=float,
        default=TEXTURE_DEFAULTS.thr_moving,
        help="--method texture: textured pixels scoring <= this vote 'world'",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    overlay_dir = args.overlay_dir
    if overlay_dir is None:
        overlay_dir = args.out_mask.parent / "overlays"

    per_frame_offsets = args.per_frame_offsets and args.method == "stability"
    summary = run_helmet_mask(
        args.input,
        args.out_mask,
        overlay_dir,
        apply_dir=args.apply_dir,
        boundary_json=args.boundary_json,
        region_top_frac=args.region_top_frac,
        downscale=args.downscale,
        stability_percentile=args.stability_percentile,
        relaxed_factor=args.relaxed_factor,
        dilate_px=args.dilate_px,
        min_blob_frac=args.min_blob_frac,
        margin_px=args.margin_px,
        coarse_window=args.coarse_window,
        max_sag=args.max_sag,
        seam_fracs=parse_seam_fracs(args.seam_fracs),
        seam_halfwidth=args.seam_halfwidth,
        seam_sag=args.seam_sag,
        envelope_window=args.envelope_window,
        spike_min_run=args.spike_min_run,
        notch_min_run=args.notch_min_run,
        outlier_mad_k=args.outlier_mad_k,
        gaussian_sigma=args.gaussian_sigma,
        simplify_epsilon=args.simplify_epsilon,
        simplify_lift_window=args.simplify_lift_window,
        simplify_gaussian_sigma=args.simplify_gaussian_sigma,
        leak_band_px=args.leak_band_px,
        per_frame_offsets=per_frame_offsets,
        max_offset_px=args.max_offset_px,
        offsets_json=args.out_mask.parent / "offsets.json" if per_frame_offsets else None,
        frame_leak_target=args.frame_leak_target,
        refine_max_extra=args.refine_max_extra,
        method=args.method,
        texture_params={
            "n_frames": args.texture_frames,
            "buffer_px": args.texture_buffer_px,
            "thr_static": args.texture_static_thr,
            "thr_moving": args.texture_moving_thr,
        },
    )
    if args.run_json is not None:
        args.run_json.parent.mkdir(parents=True, exist_ok=True)
        args.run_json.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    worst = summary.get("worst_frame_leak_score")
    worst_text = f", worst_frame_leak {worst:.4f}" if worst is not None else ""
    leak = summary.get("leak_score")
    leak_text = f"leak_score {leak:.4f}{worst_text}, " if leak is not None else ""
    print(
        f"Wrote {'texture ' if args.method == 'texture' else ''}mask for {summary['frame_count']} frames "
        f"({summary['masked_ratio']:.1%} masked, "
        f"boundary rows {summary['boundary_min_row']}-{summary['boundary_max_row']}, "
        f"{leak_text}"
        f"{summary['runtime_s']:.1f}s) -> {summary['out_mask']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
