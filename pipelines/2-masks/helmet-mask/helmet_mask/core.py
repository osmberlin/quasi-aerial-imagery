"""Temporal-stability scoring and mask derivation for helmet/rig pixels."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from helmet_mask.boundary import (
    boundary_to_mask,
    extract_raw_boundary,
    regularize_boundary,
)


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert RGB float array (..., 3) to hue [0, 1), saturation, value."""
    rgb = np.clip(rgb, 0.0, 255.0) / 255.0
    maxc = rgb.max(axis=-1)
    minc = rgb.min(axis=-1)
    delta = maxc - minc
    safe_delta = np.where(delta > 1e-8, delta, 1.0)

    hue = np.zeros_like(maxc)
    chroma_mask = delta > 1e-8
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.where(chroma_mask & (maxc == r), ((g - b) / safe_delta) % 6.0, hue)
    hue = np.where(chroma_mask & (maxc == g), ((b - r) / safe_delta) + 2.0, hue)
    hue = np.where(chroma_mask & (maxc == b), ((r - g) / safe_delta) + 4.0, hue)
    hue = (hue / 6.0) % 1.0

    safe_maxc = np.where(maxc > 1e-8, maxc, 1.0)
    saturation = np.where(delta > 1e-8, delta / safe_maxc, 0.0)
    return hue, saturation, maxc


def circular_median(values: np.ndarray, axis: int = 0) -> np.ndarray:
    """Median on the unit circle for hue angles in [0, 1)."""
    angles = values * (2.0 * math.pi)
    sin_m = np.median(np.sin(angles), axis=axis)
    cos_m = np.median(np.cos(angles), axis=axis)
    result = np.arctan2(sin_m, cos_m) / (2.0 * math.pi)
    return result % 1.0


def circular_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    diff = np.abs(a - b)
    return np.minimum(diff, 1.0 - diff)


def compute_pixel_variability(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return per-pixel MAD (lower = more stable) and chroma inconsistency."""
    median_rgb = np.median(frames, axis=0)
    mad_rgb = np.median(np.abs(frames - median_rgb), axis=0).sum(axis=-1)

    hue, saturation, _value = rgb_to_hsv(frames)
    median_hue = circular_median(hue, axis=0)
    hue_dist = np.median(circular_distance(hue, median_hue[None, ...]), axis=0)
    sat_mad = np.median(np.abs(saturation - np.median(saturation, axis=0)), axis=0)

    chroma_inconsistency = hue_dist + sat_mad
    return mad_rgb, chroma_inconsistency


def normalize_within_mask(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    selected = values[mask]
    if selected.size == 0:
        return np.zeros_like(values)
    lo = float(np.percentile(selected, 5))
    hi = float(np.percentile(selected, 95))
    if hi <= lo:
        return np.zeros_like(values)
    scaled = (values - lo) / (hi - lo)
    return np.clip(scaled, 0.0, 1.0)


def combine_stability_scores(
    mad_rgb: np.ndarray,
    chroma_inconsistency: np.ndarray,
    candidate_mask: np.ndarray,
) -> np.ndarray:
    mad_norm = normalize_within_mask(mad_rgb, candidate_mask)
    chroma_norm = normalize_within_mask(chroma_inconsistency, candidate_mask)
    return mad_norm + 0.65 * chroma_norm


def threshold_stable_pixels(
    stability: np.ndarray,
    candidate_mask: np.ndarray,
    stability_percentile: float,
) -> np.ndarray:
    selected = stability[candidate_mask]
    if selected.size == 0:
        return np.zeros_like(stability, dtype=bool)
    cutoff = float(np.percentile(selected, stability_percentile))
    return candidate_mask & (stability <= cutoff)


def relaxed_stability_percentile(
    stability_percentile: float,
    relaxed_factor: float,
) -> float:
    """Loosen the stability cutoff for hysteresis grow (cap at 100th percentile)."""
    return min(100.0, stability_percentile * relaxed_factor)


def hysteresis_threshold_stable_pixels(
    stability: np.ndarray,
    candidate_mask: np.ndarray,
    stability_percentile: float,
    relaxed_factor: float = 1.8,
) -> tuple[np.ndarray, np.ndarray]:
    """Seed-and-grow: strict seeds inside connected relaxed-threshold components."""
    strict_mask = threshold_stable_pixels(stability, candidate_mask, stability_percentile)
    relaxed_pct = relaxed_stability_percentile(stability_percentile, relaxed_factor)
    relaxed_mask = threshold_stable_pixels(stability, candidate_mask, relaxed_pct)

    labeled, count = ndimage.label(relaxed_mask)
    if count == 0 or not strict_mask.any():
        return np.zeros_like(stability, dtype=bool), relaxed_mask

    seed_labels = set(labeled[strict_mask].tolist())
    seed_labels.discard(0)
    if not seed_labels:
        return np.zeros_like(stability, dtype=bool), relaxed_mask

    keep = np.zeros(count + 1, dtype=bool)
    for label in seed_labels:
        keep[label] = True
    return keep[labeled], relaxed_mask


def compute_leak_score(
    relaxed_mask: np.ndarray,
    boundary: np.ndarray,
    *,
    band_px: int = 60,
    height: int,
) -> float:
    """Fraction of relaxed-stable pixels in the band above the boundary curve."""
    width = boundary.shape[0]
    leak_pixels = 0
    band_pixels = 0
    for x in range(width):
        boundary_row = int(round(float(boundary[x])))
        if boundary_row >= height:
            continue
        top_row = max(0, boundary_row - band_px)
        band = relaxed_mask[top_row:boundary_row, x]
        if band.size == 0:
            continue
        band_pixels += int(band.size)
        leak_pixels += int(band.sum())
    if band_pixels == 0:
        return 0.0
    return float(leak_pixels) / float(band_pixels)


def filter_components(
    mask: np.ndarray,
    min_blob_frac: float,
) -> np.ndarray:
    labeled, count = ndimage.label(mask)
    if count == 0:
        return mask

    height, width = mask.shape
    total_pixels = height * width
    min_area = max(1, int(min_blob_frac * total_pixels))
    bottom_labels = set(labeled[-1, :].tolist())
    bottom_labels.discard(0)

    keep = np.zeros(count + 1, dtype=bool)
    for label in range(1, count + 1):
        area = int((labeled == label).sum())
        if label in bottom_labels or area >= min_area:
            keep[label] = True

    return keep[labeled]


def per_column_fill(mask: np.ndarray) -> np.ndarray:
    """Fill each column from the topmost masked pixel down to the bottom edge."""
    height, width = mask.shape
    filled = np.zeros_like(mask, dtype=bool)
    for x in range(width):
        rows = np.flatnonzero(mask[:, x])
        if rows.size == 0:
            continue
        filled[int(rows.min()) :, x] = True
    return filled


def morphological_refine(
    raw_mask: np.ndarray,
    min_blob_frac: float,
    dilate_px: int,
) -> np.ndarray:
    """Closing, opening, component filter, and optional dilation — no column fill."""
    structure = ndimage.generate_binary_structure(2, 1)
    closed = ndimage.binary_closing(raw_mask, structure=structure, iterations=2)
    opened = ndimage.binary_opening(closed, structure=structure, iterations=1)
    components = filter_components(opened, min_blob_frac)
    if dilate_px > 0:
        radius = max(1, dilate_px)
        return ndimage.binary_dilation(
            components,
            structure=ndimage.generate_binary_structure(2, 1),
            iterations=radius,
        )
    return components


def refine_mask(
    raw_mask: np.ndarray,
    min_blob_frac: float,
    dilate_px: int,
) -> np.ndarray:
    """Legacy per-pixel refine: morphology + column fill + dilation."""
    components = morphological_refine(raw_mask, min_blob_frac, dilate_px=0)
    column_filled = per_column_fill(components)
    if dilate_px > 0:
        radius = max(1, dilate_px)
        return ndimage.binary_dilation(
            column_filled,
            structure=ndimage.generate_binary_structure(2, 1),
            iterations=radius,
        )
    return column_filled


def derive_mask_from_frames(
    frames: np.ndarray,
    *,
    region_top_frac: float = 0.45,
    stability_percentile: float = 12.0,
    relaxed_factor: float = 1.8,
    dilate_px: int = 12,
    min_blob_frac: float = 0.0005,
    margin_px: int = 4,
    coarse_window: int = 200,
    max_sag: float = 20.0,
    seam_fracs: list[float] | None = None,
    seam_halfwidth: float = 0.03,
    seam_sag: float = 70.0,
    envelope_window: int = 15,
    spike_min_run: int = 8,
    notch_min_run: int = 48,
    outlier_mad_k: float = 4.5,
    gaussian_sigma: float = 2.0,
    simplify_epsilon: float = 24.0,
    simplify_lift_window: int = 75,
    simplify_gaussian_sigma: float = 3.0,
    leak_band_px: int = 60,
    return_relaxed: bool = False,
) -> tuple[np.ndarray, np.ndarray, float] | tuple[np.ndarray, np.ndarray, float, np.ndarray, np.ndarray]:
    """Return (boolean mask, per-column boundary rows, leak_score). True = pixel to mask.

    When *return_relaxed* is True, also returns ``(relaxed_mask, median_rgb)``.
    """
    height = frames.shape[1]
    width = frames.shape[2]
    top_row = int(height * region_top_frac)
    candidate_mask = np.zeros((height, width), dtype=bool)
    candidate_mask[top_row:, :] = True

    mad_rgb, chroma_inconsistency = compute_pixel_variability(frames)
    stability = combine_stability_scores(mad_rgb, chroma_inconsistency, candidate_mask)
    raw_mask, relaxed_mask = hysteresis_threshold_stable_pixels(
        stability,
        candidate_mask,
        stability_percentile,
        relaxed_factor=relaxed_factor,
    )
    morph_mask = morphological_refine(raw_mask, min_blob_frac, dilate_px=dilate_px)

    raw_boundary = extract_raw_boundary(morph_mask)
    boundary = regularize_boundary(
        raw_boundary,
        envelope_window=envelope_window,
        spike_min_run=spike_min_run,
        notch_min_run=notch_min_run,
        outlier_mad_k=outlier_mad_k,
        gaussian_sigma=gaussian_sigma,
        margin_px=margin_px,
        coarse_window=coarse_window,
        max_sag=max_sag,
        seam_fracs=seam_fracs,
        seam_halfwidth=seam_halfwidth,
        seam_sag=seam_sag,
        simplify_epsilon=simplify_epsilon,
        simplify_lift_window=simplify_lift_window,
        simplify_gaussian_sigma=simplify_gaussian_sigma,
        height=height,
    )
    mask = boundary_to_mask(boundary, height)
    leak_score = compute_leak_score(
        relaxed_mask,
        boundary,
        band_px=leak_band_px,
        height=height,
    )
    if return_relaxed:
        median_rgb = np.median(frames, axis=0)
        return mask, boundary, leak_score, relaxed_mask, median_rgb
    return mask, boundary, leak_score


def derive_mask_from_frames_legacy(
    frames: np.ndarray,
    *,
    region_top_frac: float = 0.45,
    stability_percentile: float = 12.0,
    dilate_px: int = 12,
    min_blob_frac: float = 0.0005,
) -> np.ndarray:
    """Legacy API returning mask only (per-pixel column fill path)."""
    height = frames.shape[1]
    top_row = int(height * region_top_frac)
    candidate_mask = np.zeros((height, frames.shape[2]), dtype=bool)
    candidate_mask[top_row:, :] = True

    mad_rgb, chroma_inconsistency = compute_pixel_variability(frames)
    stability = combine_stability_scores(mad_rgb, chroma_inconsistency, candidate_mask)
    raw_mask = threshold_stable_pixels(stability, candidate_mask, stability_percentile)
    return refine_mask(raw_mask, min_blob_frac, dilate_px)
