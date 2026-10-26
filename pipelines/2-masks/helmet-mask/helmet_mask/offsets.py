"""Per-frame vertical boundary offsets from edge-contour alignment."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scipy import ndimage

from helmet_mask.boundary import boundary_to_mask

PALETTE_BAND_PX = 80
HELMET_SAMPLE_BELOW_PX = 30
EXCLUDE_BOTTOM_FRAC = 0.10
STREET_BAND_ABOVE_PX = 80
STREET_BAND_GAP_PX = 30
PALETTE_K = 6
PALETTE_RADIUS_PERCENTILE = 50.0
DEFAULT_FRAME_LEAK_TARGET = 0.02
PALETTE_MIN_STREET_SEPARATION = 10.0
CONTOUR_DY_MIN = -30
CONTOUR_DY_MAX = 120
CONTOUR_DY_STEP = 1
DEFAULT_CONTOUR_CONFIDENCE_MIN = 1.5
CONTOUR_SHARP_PEAK_MIN = 1.1
CONTOUR_SHARP_DY_MIN = 50
CONTOUR_ABS_DY_MAX = 100
CONTOUR_FALLBACK_DY_MAX = 80
SEAM_EXCLUDE_FRACS = (0.25, 0.75)
SEAM_EXCLUDE_HALFWIDTH = 0.03


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB float array (..., 3) in [0, 255] to CIE Lab (D65)."""
    rgb = np.clip(rgb, 0.0, 255.0) / 255.0
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float64,
    )
    xyz = linear @ matrix.T
    ref = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)

    def f(t: np.ndarray) -> np.ndarray:
        delta = 6.0 / 29.0
        return np.where(t > delta**3, np.cbrt(t), t / (3.0 * delta**2) + 4.0 / 29.0)

    fx, fy, fz = f(xyz[..., 0] / ref[0]), f(xyz[..., 1] / ref[1]), f(xyz[..., 2] / ref[2])
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)
    return lab.astype(np.float32)


def rgb_to_grayscale(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB float array (..., 3) to grayscale luminance."""
    return (
        0.299 * rgb[..., 0]
        + 0.587 * rgb[..., 1]
        + 0.114 * rgb[..., 2]
    )


def temporal_median_rgb(frames: np.ndarray) -> np.ndarray:
    """Per-pixel temporal median RGB across the frame stack."""
    return np.median(frames, axis=0)


def _kmeans_lab(
    samples: np.ndarray,
    k: int,
    *,
    max_iter: int = 40,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Simple k-means in Lab space. Returns (centers, labels)."""
    if samples.shape[0] < k:
        k = max(1, samples.shape[0])
    rng = np.random.default_rng(seed)
    init_idx = rng.choice(samples.shape[0], size=k, replace=False)
    centers = samples[init_idx].astype(np.float64).copy()

    labels = np.zeros(samples.shape[0], dtype=np.int32)
    for _ in range(max_iter):
        dists = np.linalg.norm(samples[:, None, :] - centers[None, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for cluster in range(k):
            members = samples[labels == cluster]
            if members.shape[0] > 0:
                centers[cluster] = members.mean(axis=0)

    return centers.astype(np.float32), labels


def _cluster_radii(samples: np.ndarray, centers: np.ndarray, labels: np.ndarray) -> np.ndarray:
    radii = np.zeros(centers.shape[0], dtype=np.float32)
    for cluster in range(centers.shape[0]):
        members = samples[labels == cluster]
        if members.shape[0] == 0:
            radii[cluster] = 1.0
            continue
        dists = np.linalg.norm(members - centers[cluster], axis=1)
        radii[cluster] = float(np.percentile(dists, PALETTE_RADIUS_PERCENTILE))
        if radii[cluster] < 1.0:
            radii[cluster] = 1.0
    return radii


def _sample_region_pixels(
    rgb: np.ndarray,
    boundary: np.ndarray,
    *,
    height: int,
    row_start_offset: int,
    row_end_offset: int | None = None,
    row_end_abs_max: int | None = None,
) -> np.ndarray:
    """Collect RGB pixels per column between boundary-relative row offsets."""
    width = boundary.shape[0]
    pixels: list[np.ndarray] = []
    for x in range(width):
        boundary_row = int(round(float(boundary[x])))
        if boundary_row >= height - 0.5:
            continue
        row_start = max(0, boundary_row + row_start_offset)
        if row_end_offset is not None:
            row_end = min(height - 1, boundary_row + row_end_offset)
        else:
            row_end = height - 1
        if row_end_abs_max is not None:
            row_end = min(row_end, row_end_abs_max)
        if row_start > row_end:
            continue
        column_pixels = rgb[row_start : row_end + 1, x, :].reshape(-1, 3)
        if column_pixels.size > 0:
            pixels.append(column_pixels)
    if not pixels:
        return np.empty((0, 3), dtype=np.float32)
    return np.concatenate(pixels, axis=0).astype(np.float32)


@dataclass(frozen=True)
class ColorPalette:
    """Compact Lab color model with per-cluster match radii."""

    centers: np.ndarray
    radii: np.ndarray

    def match_fraction(self, pixels_lab: np.ndarray) -> float:
        if pixels_lab.shape[0] == 0 or self.centers.shape[0] == 0:
            return 0.0
        return float(self.match_count(pixels_lab)) / float(pixels_lab.shape[0])

    def match_count(self, pixels_lab: np.ndarray) -> int:
        if pixels_lab.shape[0] == 0 or self.centers.shape[0] == 0:
            return 0
        dists = np.linalg.norm(
            pixels_lab[:, None, :] - self.centers[None, :, :],
            axis=2,
        )
        nearest = np.argmin(dists, axis=1)
        nearest_dist = dists[np.arange(dists.shape[0]), nearest]
        return int((nearest_dist <= self.radii[nearest]).sum())


def _build_palette_from_samples(samples_rgb: np.ndarray, k: int = PALETTE_K) -> ColorPalette | None:
    if samples_rgb.shape[0] < k:
        return None
    samples_lab = rgb_to_lab(samples_rgb)
    if samples_lab.shape[0] > 8000:
        rng = np.random.default_rng(0)
        pick = rng.choice(samples_lab.shape[0], size=8000, replace=False)
        samples_lab = samples_lab[pick]
    centers, labels = _kmeans_lab(samples_lab, k)
    radii = _cluster_radii(samples_lab, centers, labels)
    return ColorPalette(centers=centers, radii=radii)


def palette_street_separation(helmet: ColorPalette, street: ColorPalette) -> float:
    """Minimum margin-adjusted Lab gap between helmet and street clusters."""
    if helmet.centers.shape[0] == 0 or street.centers.shape[0] == 0:
        return 0.0
    min_gap = float("inf")
    for cluster_i, center in enumerate(helmet.centers):
        for cluster_j, street_center in enumerate(street.centers):
            gap = float(
                np.linalg.norm(center - street_center)
                - float(helmet.radii[cluster_i])
                - float(street.radii[cluster_j])
            )
            min_gap = min(min_gap, gap)
    return min_gap


def _mean_lab_separation(helmet_rgb: np.ndarray, street_rgb: np.ndarray) -> float:
    if helmet_rgb.shape[0] == 0 or street_rgb.shape[0] == 0:
        return 0.0
    helmet_mean = rgb_to_lab(helmet_rgb.mean(axis=0, keepdims=True))[0]
    street_mean = rgb_to_lab(street_rgb.mean(axis=0, keepdims=True))[0]
    return float(np.linalg.norm(helmet_mean - street_mean))


@dataclass(frozen=True)
class HelmetPaletteModel:
    """Dataset helmet palette with street separation diagnostics."""

    helmet: ColorPalette
    street: ColorPalette
    separation: float
    ambiguous: bool


def _sample_street_pixels(
    rgb: np.ndarray,
    boundary: np.ndarray,
    *,
    height: int,
) -> np.ndarray:
    """Sample street colors from rows well above the dataset boundary."""
    per_column = _sample_region_pixels(
        rgb,
        boundary,
        height=height,
        row_start_offset=-STREET_BAND_ABOVE_PX,
        row_end_offset=-STREET_BAND_GAP_PX,
    )
    if per_column.shape[0] >= PALETTE_K:
        return per_column

    detected = boundary < height - 0.5
    if not detected.any():
        top_rows = max(0, int(height * 0.05))
        bottom_rows = max(top_rows, int(height * 0.25))
        return rgb[top_rows:bottom_rows, :, :].reshape(-1, 3).astype(np.float32)

    boundary_min = int(np.floor(float(np.min(boundary[detected]))))
    row_end = max(0, boundary_min - STREET_BAND_GAP_PX)
    row_start = max(0, row_end - STREET_BAND_ABOVE_PX)
    if row_start >= row_end:
        row_start = 0
        row_end = max(1, boundary_min // 2)
    return rgb[row_start:row_end, :, :].reshape(-1, 3).astype(np.float32)


def build_helmet_palette_model(
    median_rgb: np.ndarray,
    boundary: np.ndarray,
    *,
    height: int,
    min_street_separation: float = PALETTE_MIN_STREET_SEPARATION,
) -> HelmetPaletteModel | None:
    """Build helmet and street palettes from the temporal median image."""
    helmet_rgb = _sample_region_pixels(
        median_rgb,
        boundary,
        height=height,
        row_start_offset=HELMET_SAMPLE_BELOW_PX,
        row_end_offset=None,
        row_end_abs_max=int(height * (1.0 - EXCLUDE_BOTTOM_FRAC)) - 1,
    )
    street_rgb = _sample_street_pixels(median_rgb, boundary, height=height)
    helmet_palette = _build_palette_from_samples(helmet_rgb)
    street_palette = _build_palette_from_samples(street_rgb, k=min(PALETTE_K, 4))
    if helmet_palette is None or street_palette is None:
        return None
    cluster_separation = palette_street_separation(helmet_palette, street_palette)
    mean_separation = _mean_lab_separation(helmet_rgb, street_rgb)
    separation = min(cluster_separation, mean_separation)
    ambiguous = cluster_separation < min_street_separation and mean_separation < min_street_separation
    return HelmetPaletteModel(
        helmet=helmet_palette,
        street=street_palette,
        separation=separation,
        ambiguous=ambiguous,
    )


def shifted_boundary_at_dy(
    boundary: np.ndarray,
    *,
    height: int,
    margin_px: int,
    dy: int,
) -> np.ndarray:
    """Boundary curve after applying uniform vertical shift *dy*."""
    detected = boundary < height - 0.5
    base = pre_margin_boundary(boundary, height, margin_px)
    shifted = base.copy()
    shifted[detected] = base[detected] - float(dy) - float(margin_px)
    shifted[detected] = np.clip(shifted[detected], 0.0, float(height - 1))
    shifted[~detected] = float(height)
    return shifted


def compute_palette_leak_score(
    frame_rgb: np.ndarray,
    boundary: np.ndarray,
    palette_model: HelmetPaletteModel,
    *,
    height: int,
    margin_px: int = 0,
    dy: int = 0,
    band_px: int = PALETTE_BAND_PX,
    apply_dy: bool = True,
    median_rgb: np.ndarray | None = None,
) -> float:
    """Palette leak: helmet-like pixels in the band vs leave-one-out median."""
    if apply_dy:
        eval_boundary = shifted_boundary_at_dy(
            boundary,
            height=height,
            margin_px=margin_px,
            dy=dy,
        )
    else:
        eval_boundary = boundary
    frame_lab = rgb_to_lab(frame_rgb)
    median_lab = rgb_to_lab(median_rgb) if median_rgb is not None else None
    width = boundary.shape[0]
    matched = 0
    total = 0
    for x in range(width):
        boundary_row = int(round(float(eval_boundary[x])))
        if boundary_row >= height:
            continue
        top_row = max(0, boundary_row - band_px)
        if top_row >= boundary_row:
            continue
        band_frame = frame_lab[top_row:boundary_row, x, :].reshape(-1, 3)
        if band_frame.shape[0] == 0:
            continue
        total += band_frame.shape[0]
        if median_lab is not None:
            band_median = median_lab[top_row:boundary_row, x, :].reshape(-1, 3)
            frame_helmet = _helmet_match_mask(band_frame, palette_model.helmet)
            median_helmet = _helmet_match_mask(band_median, palette_model.helmet)
            matched += int(np.sum(frame_helmet & ~median_helmet))
        else:
            matched += palette_model.helmet.match_count(band_frame)
    if total == 0:
        return 0.0
    return float(matched) / float(total)


def _helmet_match_mask(pixels_lab: np.ndarray, helmet: ColorPalette) -> np.ndarray:
    if pixels_lab.shape[0] == 0 or helmet.centers.shape[0] == 0:
        return np.zeros(pixels_lab.shape[0], dtype=bool)
    helmet_dists = np.linalg.norm(
        pixels_lab[:, None, :] - helmet.centers[None, :, :],
        axis=2,
    )
    helmet_nearest = np.argmin(helmet_dists, axis=1)
    return helmet_dists[np.arange(helmet_dists.shape[0]), helmet_nearest] <= helmet.radii[
        helmet_nearest
    ]


def _helmet_not_street_mask(pixels_lab: np.ndarray, palette_model: HelmetPaletteModel) -> np.ndarray:
    if pixels_lab.shape[0] == 0:
        return np.zeros(0, dtype=bool)
    helmet = palette_model.helmet
    street = palette_model.street
    helmet_dists = np.linalg.norm(
        pixels_lab[:, None, :] - helmet.centers[None, :, :],
        axis=2,
    )
    helmet_nearest = np.argmin(helmet_dists, axis=1)
    helmet_ok = helmet_dists[np.arange(helmet_dists.shape[0]), helmet_nearest] <= helmet.radii[
        helmet_nearest
    ]

    street_dists = np.linalg.norm(
        pixels_lab[:, None, :] - street.centers[None, :, :],
        axis=2,
    )
    street_nearest = np.argmin(street_dists, axis=1)
    street_ok = street_dists[np.arange(street_dists.shape[0]), street_nearest] <= street.radii[
        street_nearest
    ]
    return helmet_ok & ~street_ok


def _helmet_not_street_count(pixels_lab: np.ndarray, palette_model: HelmetPaletteModel) -> int:
    return int(_helmet_not_street_mask(pixels_lab, palette_model).sum())


def helmet_band_row_range(
    boundary: np.ndarray,
    height: int,
    *,
    band_above_min_px: int = 40,
    band_below_min_px: int = 80,
) -> tuple[int, int]:
    """Return inclusive row range [top, bottom] for the helmet reference band.

    Focuses on the crown near the dataset boundary rather than the full rig
    column, so head-pitch shifts are not drowned out by bottom-anchored pixels.
    """
    detected = boundary < height - 0.5
    if not detected.any():
        top = max(0, int(height * 0.45))
        return top, height - 1

    boundary_min = float(np.min(boundary[detected]))
    top = max(0, int(np.floor(boundary_min)) - band_above_min_px)
    bottom = min(height - 1, int(np.ceil(boundary_min)) + band_below_min_px)
    if bottom <= top:
        bottom = min(height - 1, top + band_below_min_px)
    return top, bottom


def compute_vertical_gradient_magnitude(
    frame_rgb: np.ndarray,
    *,
    use_rgb_channel_max: bool = False,
) -> np.ndarray:
    """Vertical gradient magnitude |dI/dy| from grayscale, optionally max over RGB."""
    gray = rgb_to_grayscale(frame_rgb)
    magnitude = np.abs(ndimage.sobel(gray, axis=0, mode="reflect"))
    if use_rgb_channel_max:
        for channel in range(frame_rgb.shape[-1]):
            channel_grad = np.abs(ndimage.sobel(frame_rgb[..., channel], axis=0, mode="reflect"))
            magnitude = np.maximum(magnitude, channel_grad)
    return magnitude.astype(np.float32)


def column_exclusion_mask(
    width: int,
    *,
    seam_fracs: tuple[float, ...] = SEAM_EXCLUDE_FRACS,
    halfwidth_frac: float = SEAM_EXCLUDE_HALFWIDTH,
) -> np.ndarray:
    """Columns to exclude from contour scoring near panorama seam zones."""
    include = np.ones(width, dtype=bool)
    half = max(1, int(round(width * halfwidth_frac)))
    for frac in seam_fracs:
        center = int(round(width * frac))
        lo = max(0, center - half)
        hi = min(width, center + half + 1)
        include[lo:hi] = False
    return include


def _bilinear_sample_rows(
    gradient: np.ndarray,
    rows: np.ndarray,
    columns: np.ndarray,
) -> np.ndarray:
    """Sample *gradient* at (rows[i], columns[i]) with bilinear interpolation in y."""
    height = gradient.shape[0]
    rows = np.clip(rows, 0.0, float(height) - 1.001)
    y0 = np.floor(rows).astype(np.int32)
    y1 = np.minimum(y0 + 1, height - 1)
    weight = (rows - y0).astype(np.float64)
    return (1.0 - weight) * gradient[y0, columns] + weight * gradient[y1, columns]


def score_contour_alignment_profile(
    gradient: np.ndarray,
    premargin_boundary: np.ndarray,
    *,
    dy_min: int = CONTOUR_DY_MIN,
    dy_max: int = CONTOUR_DY_MAX,
    dy_step: int = CONTOUR_DY_STEP,
    column_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Score edge strength along the premargin curve shifted up by each candidate dy."""
    width = premargin_boundary.shape[0]
    if column_mask is None:
        column_mask = np.ones(width, dtype=bool)
    columns = np.where(column_mask)[0]
    dys = np.arange(dy_min, dy_max + 1, dy_step, dtype=np.int32)
    if columns.size == 0:
        return dys, np.zeros(dys.shape[0], dtype=np.float64)

    boundary_rows = premargin_boundary[columns].astype(np.float64)
    scores = np.zeros(dys.shape[0], dtype=np.float64)
    for index, dy in enumerate(dys):
        sample_rows = boundary_rows - float(dy)
        samples = _bilinear_sample_rows(gradient, sample_rows, columns)
        scores[index] = float(np.mean(samples))
    return dys, scores


def _contour_confidence(scores: np.ndarray) -> tuple[float, float]:
    """Return (confidence ratio, second-best score)."""
    peak_score = float(np.max(scores))
    median_score = float(np.median(scores))
    if median_score <= 1e-8:
        confidence = peak_score if peak_score > 1e-8 else 0.0
    else:
        confidence = peak_score / median_score
    sorted_scores = np.sort(scores)
    second_peak = float(sorted_scores[-2]) if sorted_scores.size > 1 else peak_score
    return confidence, second_peak


def _is_low_confidence_contour(
    dy: int,
    confidence: float,
    peak_score: float,
    second_peak: float,
    *,
    confidence_min: float,
    abs_dy_max: int,
    sharp_dy_min: int,
    sharp_peak_min: float,
) -> bool:
    if confidence < confidence_min:
        return True
    if abs(dy) > abs_dy_max:
        return True
    if dy > sharp_dy_min and second_peak > 1e-8 and (peak_score / second_peak) < sharp_peak_min:
        return True
    return False


def _nonnegative_pitch_dy(dy: int) -> int:
    """Helmet pitch correction is upward-only at apply time."""
    return max(0, int(dy))


def estimate_dy_by_contour(
    gradient: np.ndarray,
    premargin_boundary: np.ndarray,
    *,
    dy_min: int = CONTOUR_DY_MIN,
    dy_max: int = CONTOUR_DY_MAX,
    dy_step: int = CONTOUR_DY_STEP,
    column_mask: np.ndarray | None = None,
    confidence_min: float = DEFAULT_CONTOUR_CONFIDENCE_MIN,
) -> tuple[int, float, float, np.ndarray, np.ndarray]:
    """Return (dy, confidence, peak_score, dys, scores)."""
    dys, scores = score_contour_alignment_profile(
        gradient,
        premargin_boundary,
        dy_min=dy_min,
        dy_max=dy_max,
        dy_step=dy_step,
        column_mask=column_mask,
    )
    peak_index = int(np.argmax(scores))
    dy = int(dys[peak_index])
    peak_score = float(scores[peak_index])
    confidence, _second_peak = _contour_confidence(scores)
    return dy, confidence, peak_score, dys, scores


def top_dy_candidates(
    dys: np.ndarray,
    scores: np.ndarray,
    *,
    count: int = 5,
) -> list[dict[str, float | int]]:
    """Return the top *count* dy candidates sorted by score descending."""
    if dys.size == 0:
        return []
    order = np.argsort(scores)[::-1][:count]
    return [
        {"dy": int(dys[index]), "score": round(float(scores[index]), 6)}
        for index in order
    ]


def _mean_subtract(band: np.ndarray) -> np.ndarray:
    centered = band.astype(np.float64) - float(band.mean())
    return centered


def _highpass_vertical(band: np.ndarray, sigma: float = 15.0) -> np.ndarray:
    """Remove slowly-varying vertical structure so crown pitch dominates NCC."""
    if sigma <= 0:
        return band.astype(np.float64)
    low = ndimage.gaussian_filter1d(band.astype(np.float64), sigma=sigma, axis=0)
    return band.astype(np.float64) - low


def _ncc_peak(
    frame_band: np.ndarray,
    ref_band: np.ndarray,
    *,
    max_shift: int,
    pixel_mask: np.ndarray | None = None,
    highpass_sigma: float = 15.0,
) -> tuple[int, float]:
    """Vertical NCC over shifts in [-max_shift, max_shift]. Returns (dy, peak)."""
    frame_work = _highpass_vertical(frame_band, sigma=highpass_sigma)
    ref_work = _highpass_vertical(ref_band, sigma=highpass_sigma)
    ref = _mean_subtract(ref_work)
    if pixel_mask is not None:
        weights = pixel_mask.astype(np.float64)
        ref = ref * weights
    ref_norm = float(np.linalg.norm(ref))
    if ref_norm < 1e-8:
        return 0, 0.0

    height, _width = ref_band.shape
    best_shift = 0
    best_score = -1.0

    for shift in range(-max_shift, max_shift + 1):
        if shift >= 0:
            frame_slice = frame_work[shift:, :]
            ref_slice = ref[: height - shift, :]
            mask_slice = pixel_mask[shift:, :] if pixel_mask is not None else None
        else:
            neg = -shift
            frame_slice = frame_work[: height - neg, :]
            ref_slice = ref[neg:, :]
            mask_slice = pixel_mask[: height - neg, :] if pixel_mask is not None else None
        if frame_slice.size == 0 or ref_slice.size == 0:
            continue

        frame_centered = _mean_subtract(frame_slice)
        if mask_slice is not None:
            weights = mask_slice.astype(np.float64)
            frame_centered = frame_centered * weights
            ref_slice = ref_slice * weights
        frame_norm = float(np.linalg.norm(frame_centered))
        ref_slice_norm = float(np.linalg.norm(ref_slice))
        if frame_norm < 1e-8 or ref_slice_norm < 1e-8:
            continue
        score = float(np.sum(frame_centered * ref_slice) / (frame_norm * ref_slice_norm))
        if score > best_score:
            best_score = score
            best_shift = shift

    return -best_shift, best_score


def estimate_vertical_offset(
    frame_gray: np.ndarray,
    ref_gray: np.ndarray,
    *,
    row_top: int,
    row_bottom: int,
    max_shift: int,
    column_slice: slice | None = None,
    pixel_mask: np.ndarray | None = None,
) -> tuple[int, float]:
    """Estimate vertical shift for one frame band against the reference band."""
    cols = column_slice if column_slice is not None else slice(None)
    frame_band = frame_gray[row_top : row_bottom + 1, cols]
    ref_band = ref_gray[row_top : row_bottom + 1, cols]
    band_mask = pixel_mask[row_top : row_bottom + 1, cols] if pixel_mask is not None else None
    return _ncc_peak(frame_band, ref_band, max_shift=max_shift, pixel_mask=band_mask)


def _interpolate_dy_columns(
    dy_left: int,
    dy_right: int,
    width: int,
) -> np.ndarray:
    """Linear dy from left half to right half across columns."""
    left_center = width // 4
    right_center = (3 * width) // 4
    xs = np.arange(width, dtype=np.float64)
    return np.interp(xs, [left_center, right_center], [float(dy_left), float(dy_right)])


@dataclass(frozen=True)
class FrameOffset:
    """Per-frame vertical offset estimate at working resolution."""

    dy: int
    ncc_peak: float
    low_confidence: bool
    dy_left: int | None = None
    dy_right: int | None = None
    dy_per_column: np.ndarray | None = None
    leak_target_unmet: bool = False
    palette_ambiguous: bool = False
    palette_leak: float = 0.0
    contour_peak_score: float = 0.0
    score_profile: list[dict[str, float | int]] | None = None


@dataclass(frozen=True)
class OffsetEstimationResult:
    """All per-frame offsets plus summary stats."""

    offsets: list[FrameOffset]
    dy_values: np.ndarray
    low_confidence_count: int
    palette_model: HelmetPaletteModel | None = None


def estimate_per_frame_offsets(
    frames: np.ndarray,
    median_rgb: np.ndarray,
    boundary: np.ndarray,
    *,
    palette_model: HelmetPaletteModel | None = None,
    relaxed_mask: np.ndarray | None = None,
    max_shift: int = 120,
    ncc_min: float = DEFAULT_CONTOUR_CONFIDENCE_MIN,
    max_abs_dy: int = 100,
    band_above_min_px: int = 40,
    tilt_threshold_px: int = 8,
    margin_px: int = 4,
    leak_band_px: int = 60,
    palette_band_px: int = PALETTE_BAND_PX,
    leak_refine: bool = True,
    frame_leak_target: float = DEFAULT_FRAME_LEAK_TARGET,
    refine_max_extra: int = 30,
    dy_min: int = CONTOUR_DY_MIN,
    dy_max: int = CONTOUR_DY_MAX,
    dy_step: int = CONTOUR_DY_STEP,
) -> OffsetEstimationResult:
    """Estimate per-frame vertical offsets by aligning image edges to the premargin envelope."""
    del (
        relaxed_mask,
        leak_band_px,
        refine_max_extra,
        leak_refine,
        band_above_min_px,
        tilt_threshold_px,
    )
    height = frames.shape[1]
    width = frames.shape[2]
    confidence_min = float(ncc_min)
    abs_dy_max = min(int(max_abs_dy), int(max_shift))

    if palette_model is None:
        palette_model = build_helmet_palette_model(median_rgb, boundary, height=height)

    premargin = pre_margin_boundary(boundary, height, margin_px)
    column_mask = column_exclusion_mask(width)
    detected = boundary < height - 0.5
    column_mask &= detected

    search_hi = min(int(dy_max), int(max_shift))
    frame_count = frames.shape[0]
    raw_estimates: list[
        tuple[int, float, float, float, np.ndarray, np.ndarray, bool]
    ] = []

    for frame_index in range(frame_count):
        gradient = compute_vertical_gradient_magnitude(frames[frame_index])
        dys, scores = score_contour_alignment_profile(
            gradient,
            premargin,
            dy_min=dy_min,
            dy_max=search_hi,
            dy_step=dy_step,
            column_mask=column_mask,
        )
        peak_index = int(np.argmax(scores))
        dy = int(dys[peak_index])
        peak_score = float(scores[peak_index])
        confidence, second_peak = _contour_confidence(scores)
        low_confidence = _is_low_confidence_contour(
            dy,
            confidence,
            peak_score,
            second_peak,
            confidence_min=confidence_min,
            abs_dy_max=abs_dy_max,
            sharp_dy_min=CONTOUR_SHARP_DY_MIN,
            sharp_peak_min=CONTOUR_SHARP_PEAK_MIN,
        )
        raw_estimates.append(
            (dy, confidence, peak_score, second_peak, dys, scores, low_confidence)
        )

    high_confidence_dys = [
        _nonnegative_pitch_dy(dy)
        for dy, _confidence, _peak, _second, _dys, _scores, low in raw_estimates
        if not low and 0 <= _nonnegative_pitch_dy(dy) <= CONTOUR_FALLBACK_DY_MAX
    ]
    fallback_dy = (
        _nonnegative_pitch_dy(int(round(float(np.median(high_confidence_dys)))))
        if high_confidence_dys
        else 0
    )

    offsets: list[FrameOffset] = []
    for frame_index, (
        dy,
        confidence,
        peak_score,
        _second_peak,
        dys,
        scores,
        low_confidence,
    ) in enumerate(raw_estimates):
        selected_dy = fallback_dy if low_confidence else dy
        final_dy = _nonnegative_pitch_dy(selected_dy)
        score_profile = (
            top_dy_candidates(dys, scores)
            if low_confidence or final_dy < 30 or final_dy > 70
            else None
        )

        palette_ambiguous = palette_model.ambiguous if palette_model is not None else False
        palette_leak = 0.0
        leak_target_unmet = False
        if palette_model is not None:
            if frame_count > 1:
                ref_rgb = np.median(np.delete(frames, frame_index, axis=0), axis=0)
            else:
                ref_rgb = median_rgb
            palette_leak = compute_palette_leak_score(
                frames[frame_index],
                boundary,
                palette_model,
                height=height,
                margin_px=margin_px,
                dy=final_dy,
                band_px=palette_band_px,
                median_rgb=ref_rgb,
            )
            leak_target_unmet = palette_leak > frame_leak_target

        offsets.append(
            FrameOffset(
                dy=final_dy,
                ncc_peak=confidence,
                low_confidence=low_confidence,
                contour_peak_score=peak_score,
                score_profile=score_profile,
                leak_target_unmet=leak_target_unmet,
                palette_ambiguous=palette_ambiguous,
                palette_leak=palette_leak,
            )
        )

    dy_values = np.array([offset.dy for offset in offsets], dtype=np.int32)
    low_confidence_count = sum(1 for offset in offsets if offset.low_confidence)
    return OffsetEstimationResult(
        offsets=offsets,
        dy_values=dy_values,
        low_confidence_count=low_confidence_count,
        palette_model=palette_model,
    )


def pre_margin_boundary(boundary: np.ndarray, height: int, margin_px: int) -> np.ndarray:
    """Recover the boundary curve before the safety margin was subtracted."""
    out = boundary.astype(np.float64).copy()
    detected = out < height - 0.5
    out[detected] += float(margin_px)
    return out


def per_frame_boundaries(
    boundary: np.ndarray,
    offsets: list[FrameOffset],
    *,
    height: int,
    margin_px: int,
) -> list[np.ndarray]:
    """Shift dataset boundary per frame and re-apply the safety margin."""
    base = pre_margin_boundary(boundary, height, margin_px)
    detected = boundary < height - 0.5
    width = boundary.shape[0]
    results: list[np.ndarray] = []

    for offset in offsets:
        shifted = base.copy()
        if offset.dy_per_column is not None:
            dy_cols = offset.dy_per_column.astype(np.float64)
        else:
            dy_cols = np.full(width, float(offset.dy), dtype=np.float64)

        shifted[detected] = base[detected] - dy_cols[detected]
        shifted[detected] -= float(margin_px)
        shifted[detected] = np.clip(shifted[detected], 0.0, float(height - 1))
        shifted[~detected] = float(height)
        results.append(shifted)

    return results


def per_frame_masks(
    boundaries: list[np.ndarray],
    height: int,
) -> list[np.ndarray]:
    """Build boolean masks (True = masked) for each per-frame boundary."""
    return [boundary_to_mask(boundary, height) for boundary in boundaries]
