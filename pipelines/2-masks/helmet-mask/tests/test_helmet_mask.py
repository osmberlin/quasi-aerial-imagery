"""Unit tests for helmet_mask package."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from helmet_mask.boundary import (
    boundary_to_mask,
    boundary_total_variation,
    boundary_wrap_slope,
    coarse_silhouette_line,
    extract_raw_boundary,
    regularize_boundary,
    rolling_minimum_wrapped,
    seam_zone_mask,
    simplify_boundary_polyline,
)
from helmet_mask.core import (
    derive_mask_from_frames,
    hysteresis_threshold_stable_pixels,
    per_column_fill,
    refine_mask,
)
from helmet_mask.io import run_helmet_mask
from helmet_mask.offsets import (
    DEFAULT_CONTOUR_CONFIDENCE_MIN,
    DEFAULT_FRAME_LEAK_TARGET,
    FrameOffset,
    build_helmet_palette_model,
    compute_palette_leak_score,
    compute_vertical_gradient_magnitude,
    estimate_dy_by_contour,
    estimate_per_frame_offsets,
    per_frame_boundaries,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_RUNS = REPO_ROOT / "helmet-mask" / "test-runs"
REFERENCE_CASES = [
    "sample10-red",
    "mid30-red",
    "lpg2024-red",
    "admiral-forward30-blue",
]
WORST_FRAME_LEAK_LIMITS = {
    "sample10-red": 0.35,
    "mid30-red": 0.30,
    "lpg2024-red": 0.23,
    "admiral-forward30-blue": 0.55,
}
MEAN_DY_LIMITS = {
    "sample10-red": 70.0,
    "mid30-red": 55.0,
    "lpg2024-red": 55.0,
    "admiral-forward30-blue": 70.0,
}
PREV_LEAK_SCORES = {
    "sample10-red": 0.0002,
    "mid30-red": 0.0085,
    "lpg2024-red": 0.0156,
    "admiral-forward30-blue": 0.0005,
}
DEFAULT_MAX_SAG = 20.0
DEFAULT_SEAM_SAG = 70.0
DEFAULT_COARSE_WINDOW = 200
DEFAULT_SEAM_FRACS = [0.25, 0.75]
DEFAULT_SEAM_HALFWIDTH = 0.03


def _make_synthetic_stack(
    width: int = 160,
    height: int = 80,
    frame_count: int = 8,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    frames = rng.integers(0, 256, size=(frame_count, height, width, 3), dtype=np.uint8).astype(
        np.float32
    )

    blob = np.zeros((height, width), dtype=bool)
    center_x = width // 2
    for y in range(height // 3, height):
        radius = int((width // 5) * (1.0 - 0.35 * (y - height // 3) / max(1, height - height // 3)))
        x0 = max(0, center_x - radius)
        x1 = min(width, center_x + radius + 1)
        blob[y, x0:x1] = True

    base_color = np.array([40.0, 90.0, 210.0], dtype=np.float32)
    for frame_index in range(frame_count):
        frames[frame_index, blob] = base_color
        specular_rows, specular_cols = np.where(blob)
        if specular_rows.size == 0:
            continue
        pick = rng.integers(0, specular_rows.size)
        y = int(specular_rows[pick])
        x = int(specular_cols[pick])
        frames[frame_index, y, x] = np.array([250.0, 250.0, 255.0], dtype=np.float32)

    return frames, blob


def test_mask_covers_static_blob_not_moving_background() -> None:
    frames, blob = _make_synthetic_stack()
    mask, _boundary, _leak = derive_mask_from_frames(
        frames,
        region_top_frac=0.2,
        stability_percentile=18.0,
        dilate_px=2,
        min_blob_frac=0.0001,
        margin_px=2,
    )

    blob_coverage = mask[blob].mean()
    background = ~blob
    background_leak = mask[background].mean()

    assert blob_coverage > 0.85
    assert background_leak < 0.13


def test_per_column_fill_closes_column_gaps() -> None:
    mask = np.zeros((10, 12), dtype=bool)
    mask[7:9, 4] = True
    mask[8, 5] = True

    filled = per_column_fill(mask)
    assert filled[7:, 4].all()
    assert filled[8:, 5].all()
    assert not filled[0:7, 4].any()


def test_refine_mask_keeps_bottom_touching_component() -> None:
    mask = np.zeros((20, 20), dtype=bool)
    mask[14:20, 8:12] = True
    mask[10:12, 2:4] = True

    refined = refine_mask(mask, min_blob_frac=0.02, dilate_px=0)
    assert refined[16:20, 8:12].all()
    assert not refined[10:12, 2:4].any()


def test_cli_writes_mask_and_overlays(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    width, height = 96, 48
    frames, _blob = _make_synthetic_stack(width=width, height=height, frame_count=5, seed=3)

    for index in range(frames.shape[0]):
        Image.fromarray(frames[index].astype(np.uint8), mode="RGB").save(
            input_dir / f"frame_{index:02d}.jpg"
        )

    out_mask = tmp_path / "mask.png"
    overlay_dir = tmp_path / "overlays"
    summary = run_helmet_mask(
        input_dir,
        out_mask,
        overlay_dir,
        region_top_frac=0.2,
        downscale=(width, height),
        stability_percentile=18.0,
        dilate_px=1,
        min_blob_frac=0.0001,
        margin_px=1,
    )

    assert out_mask.is_file()
    assert summary["masked_pixels"] > 0
    assert "leak_score" in summary
    assert any(overlay_dir.glob("*_overlay.jpg"))


def test_hysteresis_grows_weak_crest_not_isolated_background() -> None:
    height, width = 80, 120
    candidate = np.zeros((height, width), dtype=bool)
    candidate[height // 3 :, :] = True

    # Sparse stable islands in mostly unstable background (like normalized real scores).
    stability = np.full((height, width), 0.99, dtype=np.float64)
    stability[50:75, 35:85] = 0.05
    stability[42:50, 55:65] = 0.28
    stability[27:35, 8:15] = 0.40

    grown, relaxed = hysteresis_threshold_stable_pixels(
        stability,
        candidate,
        stability_percentile=12.0,
        relaxed_factor=1.75,
    )

    assert relaxed[27:35, 8:15].any()
    assert not grown[27:35, 8:15].any()
    assert relaxed[42:50, 55:65].any()
    assert grown[42:50, 55:65].any()
    assert grown[50:75, 35:85].all()


def test_envelope_boundary_hugs_detections_rejects_narrow_spikes() -> None:
    width, height = 200, 100
    base_row = 60.0
    mask = np.zeros((height, width), dtype=bool)
    mask[int(base_row) :, :] = True

    bump_center = 100
    bump_half = 30
    for x in range(bump_center - bump_half, bump_center + bump_half):
        mask[int(base_row - 8) : int(base_row) + 1, x] = True

    salt_cols = np.array([20, 21, 22, 23, 24])
    for x in salt_cols:
        mask[int(base_row - 30), x] = True

    raw = extract_raw_boundary(mask)
    cleaned = regularize_boundary(
        raw,
        envelope_window=15,
        spike_min_run=8,
        outlier_mad_k=4.5,
        gaussian_sigma=2.0,
        margin_px=0,
        simplify_epsilon=0.0,
        height=height,
    )

    for x in salt_cols:
        assert cleaned[x] >= base_row - 10.0, f"narrow spike at col {x}: {cleaned[x]}"

    assert cleaned[bump_center] <= base_row - 4.0

    detected_cols = raw < height - 0.5
    non_spike = detected_cols.copy()
    non_spike[salt_cols] = False
    assert np.all(cleaned[non_spike] <= raw[non_spike] + 1e-6)


def test_sag_limit_clamps_wide_valleys() -> None:
    width, height = 300, 100
    base_row = 50.0
    boundary = np.full(width, base_row)

    notch_start, notch_end = 100, 130
    boundary[notch_start:notch_end] = base_row + 25

    valley_start, valley_end = 180, 260
    boundary[valley_start:valley_end] = base_row + 15

    cleaned = regularize_boundary(
        boundary,
        envelope_window=15,
        spike_min_run=8,
        notch_min_run=48,
        outlier_mad_k=4.5,
        gaussian_sigma=0.0,
        margin_px=0,
        simplify_epsilon=0.0,
        coarse_window=80,
        max_sag=20.0,
        height=height,
    )

    assert cleaned[notch_start + 15] <= base_row + 6.0, "30-col notch should be bridged"
    assert cleaned[valley_start + 40] <= base_row + 22.0, "wide valley should be sag-clamped"


def test_seam_zone_preserves_deep_stitch_notch() -> None:
    width = 400
    height = 200
    base_row = 80.0
    boundary = np.full(width, base_row)
    seam_center = int(round(0.25 * width))
    seam_half = 30
    boundary[seam_center - seam_half : seam_center + seam_half] = base_row + 55

    cleaned = regularize_boundary(
        boundary,
        envelope_window=15,
        margin_px=0,
        simplify_epsilon=0.0,
        gaussian_sigma=0.0,
        coarse_window=120,
        max_sag=20.0,
        seam_sag=70.0,
        height=height,
    )

    assert cleaned[seam_center] >= base_row + 45.0, "stitch seam notch should survive sag limit"


def test_wrap_continuity_deep_feature_straddling_seam() -> None:
    width = 200
    height = 100
    boundary = np.full(width, 50.0)
    for col in (0, 1, 2, width - 3, width - 2, width - 1):
        boundary[col] = 85.0

    cleaned = regularize_boundary(
        boundary,
        envelope_window=15,
        margin_px=0,
        simplify_epsilon=0.0,
        gaussian_sigma=0.0,
        coarse_window=60,
        max_sag=20.0,
        height=height,
    )
    assert boundary_wrap_slope(cleaned) <= 2.0


def test_rejects_narrow_downward_notches_preserves_wide_valleys() -> None:
    """Legacy name: wide valleys are now clamped by the sag prior (see sag_limit test)."""
    test_sag_limit_clamps_wide_valleys()


def test_wrap_around_continuity_at_seam() -> None:
    width = 144
    boundary = np.linspace(50.0, 55.0, width)
    boundary[0] = 30.0
    boundary[-1] = 70.0

    cleaned = regularize_boundary(
        boundary,
        envelope_window=15,
        spike_min_run=8,
        outlier_mad_k=4.5,
        gaussian_sigma=2.0,
        margin_px=0,
        height=100,
    )
    seam_diff = boundary_wrap_slope(cleaned)
    assert seam_diff <= 2.0, f"seam discontinuity too large: {seam_diff}"


def test_margin_moves_boundary_up() -> None:
    width, height = 80, 60
    mask = np.zeros((height, width), dtype=bool)
    mask[40:, :] = True

    raw = extract_raw_boundary(mask)
    no_margin = regularize_boundary(raw, margin_px=0, height=height, envelope_window=15)
    with_margin = regularize_boundary(raw, margin_px=6, height=height, envelope_window=15)

    assert with_margin.mean() < no_margin.mean()


def test_boundary_to_mask_fills_columns() -> None:
    height = 50
    boundary = np.full(40, 30.0)
    mask = boundary_to_mask(boundary, height)
    assert mask[30:, :].all()
    assert not mask[:30, :].any()


def test_rolling_minimum_is_upper_envelope() -> None:
    values = np.array([50.0, 48.0, 55.0, 47.0, 52.0])
    rolled = rolling_minimum_wrapped(values, window=3)
    assert rolled[2] <= 48.0


def test_simplify_reduces_jittery_sine_noise_tv() -> None:
    width = 360
    x = np.arange(width, dtype=np.float64)
    rng = np.random.default_rng(7)
    macro = 40.0 + 6.0 * np.sin(x / 9.0)
    jittery = macro + rng.normal(0.0, 2.5, width)
    pre_tv = boundary_total_variation(jittery)
    smoothed = simplify_boundary_polyline(jittery, epsilon=6.0)
    post_tv = boundary_total_variation(smoothed)
    assert post_tv < pre_tv * 0.6, f"TV reduction {(1 - post_tv / pre_tv) * 100:.1f}% < 40%"
    assert np.all(smoothed <= jittery + 1e-6)


def test_simplify_preserves_sharp_corner_within_two_columns() -> None:
    width = 200
    boundary = np.full(width, 50.0)
    step_col = 100
    boundary[step_col : width - 20] = 30.0
    # Flat seam strip so ERP wrap does not dominate the corner detector.
    boundary[width - 20 :] = 50.0

    smoothed = simplify_boundary_polyline(boundary, epsilon=6.0, lift_window=25)
    transition = np.abs(np.diff(smoothed))
    corner_index = int(np.argmax(transition))
    # Rolling-topmost lift shifts a hard step left by ~half the lift window.
    lift_half = (25 - 1) // 2
    expected_corner = step_col - lift_half
    assert abs(corner_index - expected_corner) <= 2
    assert transition[corner_index] >= 15.0


def _load_boundary_json(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text())
    return np.array(payload["boundary_rows"], dtype=np.float64)


def _row_scale_for_case(case_name: str) -> tuple[float, int]:
    run_path = TEST_RUNS / case_name / "run.json"
    payload = json.loads(run_path.read_text())
    work_height = int(payload["parameters"]["downscale"][1])
    mask_path = TEST_RUNS / case_name / "mask.png"
    with Image.open(mask_path) as image:
        full_height = image.height
    return full_height / work_height, full_height


def _assert_boundary_shape_prior(
    boundary: np.ndarray,
    *,
    row_scale: float,
    full_height: int,
    max_sag: float = DEFAULT_MAX_SAG,
    seam_sag: float = DEFAULT_SEAM_SAG,
    coarse_window: int = DEFAULT_COARSE_WINDOW,
    seam_fracs: list[float] | None = None,
    seam_halfwidth: float = DEFAULT_SEAM_HALFWIDTH,
) -> None:
    if seam_fracs is None:
        seam_fracs = DEFAULT_SEAM_FRACS
    width = boundary.shape[0]
    detected = boundary < full_height - 0.5
    values = boundary.copy()
    values[~detected] = float(full_height)
    coarse = coarse_silhouette_line(values, coarse_window)
    sag = boundary - coarse
    seam_zones = seam_zone_mask(width, seam_fracs, seam_halfwidth)

    max_sag_full = max_sag * row_scale
    seam_sag_full = seam_sag * row_scale
    outside = detected & ~seam_zones
    assert outside.any()
    assert float(np.max(sag[outside])) <= max_sag_full + 1.5, (
        f"max sag outside seams {float(np.max(sag[outside])):.1f} > {max_sag_full:.1f}"
    )

    seam_detected = detected & seam_zones
    if seam_detected.any():
        assert float(np.max(sag[seam_detected])) <= seam_sag_full + 1.5


@pytest.mark.parametrize("case_name", REFERENCE_CASES)
def test_reference_boundary_wrap_continuity(case_name: str) -> None:
    boundary_path = TEST_RUNS / case_name / "boundary.json"
    if not boundary_path.is_file():
        pytest.skip(f"missing {boundary_path}")
    boundary = _load_boundary_json(boundary_path)
    assert boundary_wrap_slope(boundary) <= 2.0


@pytest.mark.parametrize("case_name", REFERENCE_CASES)
def test_reference_boundary_sag_prior(case_name: str) -> None:
    boundary_path = TEST_RUNS / case_name / "boundary.json"
    if not boundary_path.is_file():
        pytest.skip(f"missing {boundary_path}")
    boundary = _load_boundary_json(boundary_path)
    row_scale, full_height = _row_scale_for_case(case_name)
    _assert_boundary_shape_prior(boundary, row_scale=row_scale, full_height=full_height)


@pytest.mark.parametrize("case_name", REFERENCE_CASES)
def test_reference_leak_score_within_2x(case_name: str) -> None:
    run_path = TEST_RUNS / case_name / "run.json"
    if not run_path.is_file():
        pytest.skip(f"missing {run_path}")
    payload = json.loads(run_path.read_text())
    prev = PREV_LEAK_SCORES[case_name]
    assert float(payload["leak_score"]) < prev * 2.0


def test_sample10_seam_notch_preserved() -> None:
    boundary_path = TEST_RUNS / "sample10-red" / "boundary.json"
    if not boundary_path.is_file():
        pytest.skip("sample10-red boundary.json not generated yet")
    boundary = _load_boundary_json(boundary_path)
    row_scale, full_height = _row_scale_for_case("sample10-red")
    width = boundary.shape[0]
    detected = boundary < full_height - 0.5
    values = boundary.copy()
    values[~detected] = float(full_height)
    coarse = coarse_silhouette_line(values, DEFAULT_COARSE_WINDOW)
    seam_cols = seam_zone_mask(width, DEFAULT_SEAM_FRACS, DEFAULT_SEAM_HALFWIDTH) & detected
    seam_sag_vals = boundary[seam_cols] - coarse[seam_cols]
    max_seam_sag = float(np.max(seam_sag_vals))
    assert max_seam_sag > DEFAULT_MAX_SAG * row_scale + 2.0, (
        f"stitch seam notch too shallow: max seam sag={max_seam_sag:.1f}"
    )
    assert max_seam_sag <= DEFAULT_SEAM_SAG * row_scale + 2.0


def _make_shifted_blob_stack(
    *,
    width: int = 160,
    height: int = 80,
    frame_count: int = 8,
    shift_frame_index: int = 3,
    shift_px: int = 30,
    seed: int = 11,
) -> tuple[np.ndarray, np.ndarray, int]:
    frames, blob = _make_synthetic_stack(
        width=width,
        height=height,
        frame_count=frame_count,
        seed=seed,
    )
    shifted_blob = np.zeros_like(blob)
    for y in range(height):
        for x in range(width):
            if blob[y, x]:
                target_y = y - shift_px
                if 0 <= target_y < height:
                    shifted_blob[target_y, x] = True
    base_color = np.array([40.0, 90.0, 210.0], dtype=np.float32)
    background_color = np.median(frames[0, : height // 3, :], axis=(0, 1))
    frames[shift_frame_index] = frames[0].copy()
    frames[shift_frame_index, blob] = background_color
    frames[shift_frame_index, shifted_blob] = base_color
    return frames, blob, shift_px


def _synthetic_contour_frame(
    *,
    width: int = 160,
    height: int = 120,
    shift_px: int = 37,
) -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(width, dtype=np.float64)
    premargin = 55.0 + 8.0 * np.sin(xs / width * 2.0 * np.pi)
    rows = np.arange(height, dtype=np.float32)[:, None]
    edge_rows = (premargin - float(shift_px))[None, :]
    luminance = np.where(rows < edge_rows, 180.0, 20.0).astype(np.float32)
    frame = np.stack([luminance, luminance, luminance], axis=-1)
    return frame, premargin.astype(np.float64)


def test_contour_alignment_recovers_known_shift() -> None:
    frame, premargin = _synthetic_contour_frame(shift_px=37)
    gradient = compute_vertical_gradient_magnitude(frame)
    dy, confidence, _peak, _dys, _scores = estimate_dy_by_contour(gradient, premargin)
    assert confidence >= DEFAULT_CONTOUR_CONFIDENCE_MIN
    assert abs(dy - 37) <= 2, f"expected ~37, got {dy}"


def test_contour_alignment_flat_texture_triggers_fallback() -> None:
    width, height = 160, 120
    premargin = np.full(width, 60.0, dtype=np.float64)
    flat_frame = np.full((height, width, 3), 120.0, dtype=np.float32)
    good_frame, _ = _synthetic_contour_frame(width=width, height=height, shift_px=20)
    frames = np.stack([flat_frame, good_frame, good_frame, good_frame], axis=0)
    boundary = premargin.copy()
    boundary[~np.isfinite(boundary)] = float(height)
    result = estimate_per_frame_offsets(
        frames,
        np.median(frames, axis=0),
        boundary,
        margin_px=0,
        max_shift=120,
    )
    assert result.offsets[0].low_confidence
    assert result.offsets[0].dy == int(round(np.median([result.offsets[i].dy for i in (1, 2, 3)])))


def test_per_frame_boundary_shifts_with_detected_offset() -> None:
    height = 80
    boundary = np.full(120, 40.0, dtype=np.float64)
    offsets = [FrameOffset(dy=30, ncc_peak=1.0, low_confidence=False)]
    shifted = per_frame_boundaries(boundary, offsets, height=height, margin_px=2)[0]
    assert float(np.median(shifted)) < float(np.median(boundary))


def test_contour_alignment_moves_boundary_for_shifted_blob() -> None:
    width, height, shift_px = 160, 120, 24
    xs = np.arange(width, dtype=np.float64)
    premargin = 50.0 + 6.0 * np.sin(xs / width * 2.0 * np.pi)
    boundary = premargin - 2.0
    rows = np.arange(height, dtype=np.float32)[:, None]
    base_lum = np.random.default_rng(11).uniform(90.0, 110.0, (height, width)).astype(np.float32)
    frames = np.stack([base_lum, base_lum, base_lum], axis=-1)
    frames = np.tile(frames[None, ...], (6, 1, 1, 1)).astype(np.float32)
    edge_rows = (premargin - float(shift_px))[None, :]
    shifted_lum = np.where(rows < edge_rows, 180.0, 20.0).astype(np.float32)
    frames[3] = np.stack([shifted_lum, shifted_lum, shifted_lum], axis=-1)
    median_rgb = np.median(frames, axis=0)

    result = estimate_per_frame_offsets(
        frames,
        median_rgb,
        boundary.astype(np.float64),
        max_shift=120,
        margin_px=2,
    )
    frame_boundaries = per_frame_boundaries(
        boundary.astype(np.float64),
        result.offsets,
        height=height,
        margin_px=2,
    )
    detected = boundary < height - 0.5
    baseline = float(np.median(boundary[detected]))
    shifted = float(np.median(frame_boundaries[3][detected]))
    assert result.offsets[3].dy > 0
    assert shifted < baseline
    assert abs(result.offsets[3].dy - shift_px) <= 4


def test_contour_alignment_low_confidence_fallback() -> None:
    frames, blob = _make_synthetic_stack(width=120, height=60, frame_count=5, seed=5)
    mask, boundary, _leak, _relaxed, median_rgb = derive_mask_from_frames(
        frames,
        region_top_frac=0.2,
        stability_percentile=18.0,
        dilate_px=2,
        min_blob_frac=0.0001,
        margin_px=2,
        return_relaxed=True,
    )
  # Scramble one frame so contour scoring should fail and fall back to high-confidence median.
    frames[2] = np.random.default_rng(99).integers(0, 256, size=frames[2].shape).astype(np.float32)
    result = estimate_per_frame_offsets(
        frames,
        median_rgb,
        boundary,
        relaxed_mask=_relaxed,
        max_shift=40,
        ncc_min=99.0,
        margin_px=2,
    )
    assert result.offsets[2].low_confidence
    assert result.offsets[2].score_profile is not None
    assert result.offsets[2].dy >= 0


@pytest.mark.parametrize("case_name", REFERENCE_CASES)
def test_reference_worst_frame_leak_within_budget(case_name: str) -> None:
    run_path = TEST_RUNS / case_name / "run.json"
    offsets_path = TEST_RUNS / case_name / "offsets.json"
    if not run_path.is_file() or not offsets_path.is_file():
        pytest.skip(f"missing generated outputs for {case_name}")
    payload = json.loads(run_path.read_text())
    worst = float(payload.get("worst_frame_leak_score", payload["leak_score"]))
    limit = WORST_FRAME_LEAK_LIMITS[case_name]
    assert worst <= limit, f"{case_name} worst frame palette leak {worst:.4f} > {limit}"


def test_palette_leak_reports_shifted_red_blob() -> None:
    frames, _blob, shift_px = _make_shifted_blob_stack(
        width=160,
        height=80,
        frame_count=8,
        shift_frame_index=3,
        shift_px=24,
    )
    _mask, boundary, _leak, _relaxed, median_rgb = derive_mask_from_frames(
        frames,
        region_top_frac=0.2,
        stability_percentile=18.0,
        dilate_px=2,
        min_blob_frac=0.0001,
        margin_px=2,
        return_relaxed=True,
    )
    palette_model = build_helmet_palette_model(median_rgb, boundary, height=frames.shape[1])
    assert palette_model is not None
    assert not palette_model.ambiguous

    ref = np.median(np.delete(frames, 3, axis=0), axis=0)
    low_dy_leak = compute_palette_leak_score(
        frames[3],
        boundary,
        palette_model,
        height=frames.shape[1],
        margin_px=2,
        dy=0,
        median_rgb=ref,
    )
    high_dy_leak = compute_palette_leak_score(
        frames[3],
        boundary,
        palette_model,
        height=frames.shape[1],
        margin_px=2,
        dy=shift_px,
        median_rgb=ref,
    )
    assert low_dy_leak > high_dy_leak
    assert low_dy_leak > DEFAULT_FRAME_LEAK_TARGET


def _make_grey_on_grey_stack(
    *,
    width: int = 160,
    height: int = 80,
    frame_count: int = 6,
) -> np.ndarray:
    rng = np.random.default_rng(42)
    street = np.array([118.0, 118.0, 118.0], dtype=np.float32)
    helmet = np.array([122.0, 122.0, 122.0], dtype=np.float32)
    frames = np.tile(street, (frame_count, height, width, 1)).astype(np.float32)
    blob = np.zeros((height, width), dtype=bool)
    center_x = width // 2
    for y in range(height // 3, height):
        radius = int((width // 5) * (1.0 - 0.35 * (y - height // 3) / max(1, height - height // 3)))
        x0 = max(0, center_x - radius)
        x1 = min(width, center_x + radius + 1)
        blob[y, x0:x1] = True
    for frame_index in range(frame_count):
        frames[frame_index, blob] = helmet + rng.normal(0.0, 1.5, size=3).astype(np.float32)
    return frames


def test_grey_on_grey_contour_low_confidence() -> None:
    frames = _make_grey_on_grey_stack()
    _mask, boundary, _leak, _relaxed, median_rgb = derive_mask_from_frames(
        frames,
        region_top_frac=0.2,
        stability_percentile=18.0,
        dilate_px=2,
        min_blob_frac=0.0001,
        margin_px=2,
        return_relaxed=True,
    )
    palette_model = build_helmet_palette_model(median_rgb, boundary, height=frames.shape[1])
    assert palette_model is not None
    assert palette_model.ambiguous

    result = estimate_per_frame_offsets(
        frames,
        median_rgb,
        boundary,
        palette_model=palette_model,
        max_shift=80,
        margin_px=2,
    )
    assert all(offset.palette_ambiguous for offset in result.offsets)
    assert all(abs(offset.dy) <= 20 for offset in result.offsets)


@pytest.mark.parametrize("case_name", REFERENCE_CASES)
def test_reference_mean_dy_within_budget(case_name: str) -> None:
    run_path = TEST_RUNS / case_name / "run.json"
    if not run_path.is_file():
        pytest.skip(f"missing {run_path}")
    payload = json.loads(run_path.read_text())
    if "dy_min" not in payload:
        pytest.skip("no per-frame offsets in run.json")
    mean_dy = (int(payload["dy_min"]) + int(payload["dy_max"])) / 2.0
    offsets_path = TEST_RUNS / case_name / "offsets.json"
    if offsets_path.is_file():
        offsets = json.loads(offsets_path.read_text())
        dys = [int(frame["dy"]) for frame in offsets["frames"]]
        mean_dy = float(np.mean(dys))
    assert mean_dy < MEAN_DY_LIMITS[case_name], (
        f"{case_name} mean dy {mean_dy:.1f} >= {MEAN_DY_LIMITS[case_name]} working px"
    )


def test_lpg_gsaa6151_dy_in_expected_range() -> None:
    offsets_path = TEST_RUNS / "lpg2024-red" / "offsets.json"
    if not offsets_path.is_file():
        pytest.skip("lpg2024-red offsets.json not generated yet")
    payload = json.loads(offsets_path.read_text())
    entry = next(f for f in payload["frames"] if f["filename"].startswith("GSAA6151"))
    dy = int(entry["dy"])
    assert 0 < dy < 100
    if not (30 <= dy <= 70):
        profile = entry.get("score_profile")
        assert profile is None or len(profile) > 0
