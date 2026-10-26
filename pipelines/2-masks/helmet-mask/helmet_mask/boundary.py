"""Per-column boundary extraction and ERP-aware curve regularization."""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def extract_raw_boundary(mask: np.ndarray, row_percentile: float | None = None) -> np.ndarray:
    """Per-column top boundary from a boolean mask (topmost detected row per column).

    When *row_percentile* is None (default), uses the minimum row (upper envelope).
    A percentile may be passed for legacy noise rejection experiments.
    """
    height, width = mask.shape
    boundary = np.full(width, float(height), dtype=np.float64)
    for x in range(width):
        rows = np.flatnonzero(mask[:, x])
        if rows.size > 0:
            if row_percentile is None:
                boundary[x] = float(rows.min())
            else:
                boundary[x] = float(np.percentile(rows, row_percentile))
    return boundary


def rolling_minimum_wrapped(values: np.ndarray, window: int) -> np.ndarray:
    """Horizontally wrapped rolling minimum (upper envelope in row space)."""
    size = max(1, int(window) | 1)  # ensure odd
    inverted = -values.astype(np.float64)
    rolled_max = ndimage.maximum_filter1d(inverted, size=size, mode="wrap")
    return -rolled_max


def rolling_median_wrapped(values: np.ndarray, window: int) -> np.ndarray:
    """Horizontally wrapped rolling median for ERP continuity."""
    size = max(1, int(window) | 1)  # ensure odd
    return ndimage.median_filter(values.astype(np.float64), size=size, mode="wrap")


def gaussian_smooth_wrapped(values: np.ndarray, sigma: float) -> np.ndarray:
    """Horizontally wrapped Gaussian smoothing."""
    if sigma <= 0:
        return values.astype(np.float64)
    return ndimage.gaussian_filter1d(values.astype(np.float64), sigma=sigma, mode="wrap")


def interpolate_outliers_wrapped(boundary: np.ndarray, outlier_mask: np.ndarray) -> np.ndarray:
    """Linear interpolation across outlier columns with horizontal wrap."""
    width = boundary.shape[0]
    good = ~outlier_mask
    if not outlier_mask.any():
        return boundary.copy()
    if good.sum() < 2:
        return boundary.copy()

    x = np.arange(width, dtype=np.float64)
    x_ext = np.concatenate([x - width, x, x + width])
    y_ext = np.concatenate([boundary, boundary, boundary])
    good_ext = np.concatenate([good, good, good])
    return np.interp(x, x_ext[good_ext], y_ext[good_ext])


def _find_short_runs_circular(mask: np.ndarray, min_run: int) -> np.ndarray:
    """Mark columns in outlier runs shorter than *min_run* (ERP wrap-aware)."""
    width = mask.shape[0]
    reject = np.zeros(width, dtype=bool)
    if not mask.any():
        return reject

    ext = np.concatenate([mask, mask])
    visited = np.zeros(width, dtype=bool)
    for start in range(width):
        if visited[start] or not mask[start]:
            continue
        end = start
        while end < start + width and ext[end]:
            end += 1
        for index in range(start, end):
            visited[index % width] = True
        if end - start < min_run:
            for index in range(start, end):
                reject[index % width] = True
    return reject


def _reject_narrow_deviation_runs(
    boundary: np.ndarray,
    *,
    trend_window: int,
    min_run: int,
    outlier_mad_k: float,
    downward: bool,
) -> np.ndarray:
    """Reject narrow runs deviating from the local wrapped rolling median."""
    trend = rolling_median_wrapped(boundary, trend_window)
    if downward:
        deviation = boundary - trend
    else:
        deviation = trend - boundary
    mad = float(np.median(np.abs(boundary - trend)))
    if mad < 1e-6:
        mad = 1.0
    outlier = deviation > outlier_mad_k * mad
    if not outlier.any():
        return boundary.copy()

    reject = _find_short_runs_circular(outlier, min_run)
    return interpolate_outliers_wrapped(boundary, reject)


def reject_narrow_upward_spikes(
    boundary: np.ndarray,
    *,
    trend_window: int,
    spike_min_run: int,
    outlier_mad_k: float,
) -> np.ndarray:
    """Reject only narrow upward spikes (small row = visually above local trend)."""
    return _reject_narrow_deviation_runs(
        boundary,
        trend_window=trend_window,
        min_run=spike_min_run,
        outlier_mad_k=outlier_mad_k,
        downward=False,
    )


def reject_narrow_downward_notches(
    boundary: np.ndarray,
    *,
    trend_window: int,
    notch_min_run: int,
    outlier_mad_k: float,
) -> np.ndarray:
    """Reject narrow downward notches (large row = detection gap / V-dip into helmet)."""
    return _reject_narrow_deviation_runs(
        boundary,
        trend_window=trend_window,
        min_run=notch_min_run,
        outlier_mad_k=outlier_mad_k,
        downward=True,
    )


def boundary_total_variation(boundary: np.ndarray) -> float:
    """Sum of absolute column-to-column row differences (horizontal wrap included)."""
    values = boundary.astype(np.float64)
    width = values.shape[0]
    if width < 2:
        return 0.0
    diffs = np.abs(np.diff(values))
    wrap_diff = abs(float(values[0] - values[-1]))
    return float(diffs.sum() + wrap_diff)


def _perpendicular_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    if np.allclose(start, end):
        return float(np.linalg.norm(point - start))
    dx = float(end[0] - start[0])
    dy = float(end[1] - start[1])
    px = float(point[0] - start[0])
    py = float(point[1] - start[1])
    return abs(dx * py - dy * px) / float(np.hypot(dx, dy))


def _rdp_simplify(points: np.ndarray, epsilon: float) -> np.ndarray:
    """Ramer–Douglas–Peucker on an open polyline (N×2 array)."""
    if points.shape[0] <= 2 or epsilon <= 0:
        return points.copy()

    start, end = points[0], points[-1]
    max_dist = 0.0
    max_index = 0
    for index in range(1, points.shape[0] - 1):
        dist = _perpendicular_distance(points[index], start, end)
        if dist > max_dist:
            max_dist = dist
            max_index = index

    if max_dist <= epsilon:
        return np.vstack([start, end])

    left = _rdp_simplify(points[: max_index + 1], epsilon)
    right = _rdp_simplify(points[max_index:], epsilon)
    return np.vstack([left[:-1], right])


def _find_wrap_cut_index(boundary: np.ndarray, window: int = 31) -> int:
    """Pick an unwrap cut in the flattest horizontal stretch (min row range)."""
    width = boundary.shape[0]
    size = max(3, min(int(window) | 1, width))
    best_index = 0
    best_range = float("inf")
    for start in range(width):
        rows = boundary[np.arange(start, start + size) % width]
        row_range = float(rows.max() - rows.min())
        if row_range < best_range:
            best_range = row_range
            best_index = start
    return best_index


def _turn_angle_degrees(prev: np.ndarray, vertex: np.ndarray, nxt: np.ndarray) -> float:
    v1 = prev - vertex
    v2 = nxt - vertex
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos_angle = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_angle)))


def _interpolate_vertices_to_columns(
    vertex_x: np.ndarray,
    vertex_y: np.ndarray,
    width: int,
) -> np.ndarray:
    """Piecewise-linear interpolation from simplified vertices to every column."""
    if vertex_x.size == 0:
        return np.zeros(width, dtype=np.float64)
    if vertex_x.size == 1:
        return np.full(width, float(vertex_y[0]), dtype=np.float64)

    order = np.argsort(vertex_x)
    vx = vertex_x[order].astype(np.float64)
    vy = vertex_y[order].astype(np.float64)
    xs = np.arange(width, dtype=np.float64)
    return np.interp(xs, vx, vy)


def simplify_boundary_polyline(
    boundary: np.ndarray,
    *,
    epsilon: float = 6.0,
    gaussian_sigma: float = 3.0,
    corner_angle_deg: float = 50.0,
    corner_guard_cols: int = 5,
    cut_window: int = 31,
    lift_window: int = 25,
) -> np.ndarray:
    """Vector-like smoothing with one-directional safety (mask may only grow upward).

    Pipeline: rolling-topmost lift → wrapped RDP → linear interp → selective Gaussian
    → clamp to lifted curve (never pull down toward the jittery pre-lift envelope).

    Row indices may only decrease vs the input envelope (more mask, never expose helmet).
    """
    if epsilon <= 0:
        return boundary.astype(np.float64).copy()

    width = boundary.shape[0]
    pre = boundary.astype(np.float64)
    lifted = rolling_minimum_wrapped(pre, lift_window)
    cut = _find_wrap_cut_index(lifted, window=cut_window)
    order = np.array([(cut + i) % width for i in range(width)], dtype=np.int32)
    unwrapped = lifted[order]
    points = np.column_stack([np.arange(width, dtype=np.float64), unwrapped])
    simplified = _rdp_simplify(points, epsilon)

    simplified_rows = _interpolate_vertices_to_columns(
        simplified[:, 0],
        simplified[:, 1],
        width,
    )
    remapped = np.empty(width, dtype=np.float64)
    remapped[order] = simplified_rows

    corner_mask = np.zeros(width, dtype=bool)
    if simplified.shape[0] >= 3:
        for index in range(1, simplified.shape[0] - 1):
            turn = _turn_angle_degrees(
                simplified[index - 1],
                simplified[index],
                simplified[index + 1],
            )
            if turn > corner_angle_deg:
                col = int(round(float(simplified[index, 0]))) % width
                for offset in range(-corner_guard_cols, corner_guard_cols + 1):
                    corner_mask[(col + offset) % width] = True

    smoothable = np.ones(width, dtype=bool)
    smoothable[corner_mask] = False
    if gaussian_sigma > 0 and smoothable.any():
        smoothed = gaussian_smooth_wrapped(remapped, gaussian_sigma)
        remapped = np.where(smoothable, smoothed, remapped)

    return np.minimum(remapped, lifted)


def regularize_boundary(
    raw_boundary: np.ndarray,
    *,
    envelope_window: int = 15,
    spike_min_run: int = 8,
    notch_min_run: int = 48,
    outlier_mad_k: float = 4.5,
    gaussian_sigma: float = 2.0,
    margin_px: int = 4,
    simplify_epsilon: float = 24.0,
    simplify_lift_window: int = 75,
    simplify_gaussian_sigma: float = 3.0,
    coarse_window: int = 200,
    max_sag: float = 20.0,
    seam_fracs: list[float] | None = None,
    seam_halfwidth: float = 0.03,
    seam_sag: float = 70.0,
    wrap_max_jump: float = 2.0,
    height: int | None = None,
    # Legacy aliases (rolling_window → envelope_window for trend/spike step)
    rolling_window: int | None = None,
) -> np.ndarray:
    """Fit an upper-envelope boundary curve for ERP helmet masking.

    Steps: reject narrow upward spikes and downward notches → wrapped rolling minimum
    → light Gaussian smooth (capped to never dip below pre-smooth envelope)
    → vector simplify → physical sag limit (coarse silhouette prior, seam exceptions)
    → ERP wrap continuity → subtract safety margin (move boundary up).

    Columns with no detection (sentinel = height) are left untouched so tapered
    rigs do not leak mask into side background.
    """
    trend_window = rolling_window if rolling_window is not None else max(envelope_window, 15)
    boundary = raw_boundary.astype(np.float64).copy()
    no_detection = np.zeros(boundary.shape[0], dtype=bool)
    if height is not None:
        no_detection = boundary >= height - 0.5

    detected = ~no_detection
    if detected.sum() >= 2:
        work = boundary.copy()
        work[no_detection] = np.median(boundary[detected])  # temp fill for filters only
        work = reject_narrow_upward_spikes(
            work,
            trend_window=trend_window,
            spike_min_run=spike_min_run,
            outlier_mad_k=outlier_mad_k,
        )
        work = reject_narrow_downward_notches(
            work,
            trend_window=max(trend_window, notch_min_run * 2 + 1),
            notch_min_run=notch_min_run,
            outlier_mad_k=outlier_mad_k,
        )
        envelope = rolling_minimum_wrapped(work, envelope_window)
        smoothed = gaussian_smooth_wrapped(envelope, gaussian_sigma)
        work = np.minimum(smoothed, envelope)
        if simplify_epsilon > 0:
            work = simplify_boundary_polyline(
                work,
                epsilon=simplify_epsilon,
                lift_window=simplify_lift_window,
                gaussian_sigma=simplify_gaussian_sigma,
            )
        work = enforce_wrap_continuity(work, max_jump=wrap_max_jump)
        boundary[detected] = work[detected]

    if margin_px > 0:
        boundary[detected] = boundary[detected] - float(margin_px)

    if height is not None:
        boundary[no_detection] = float(height)
        boundary[detected] = np.clip(boundary[detected], 0.0, float(height - 1))

    if detected.sum() >= 2 and max_sag > 0 and coarse_window > 0:
        fill_row = float(height) if height is not None else float(np.max(boundary[detected]))
        for _ in range(3):
            clamped, _coarse, was_clamped = apply_sag_limit(
                boundary,
                coarse_window=coarse_window,
                max_sag=max_sag,
                seam_fracs=seam_fracs,
                seam_halfwidth=seam_halfwidth,
                seam_sag=seam_sag,
                detected=detected,
                coarse_fill_row=fill_row,
            )
            boundary[detected] = clamped[detected]
            if not was_clamped[detected].any():
                break
        if height is not None:
            boundary[no_detection] = float(height)

    if detected.sum() >= 2:
        boundary = pin_wrap_endpoints(boundary, max_jump=wrap_max_jump)

    return boundary


def boundary_to_mask(boundary: np.ndarray, height: int) -> np.ndarray:
    """Build a column-fill mask from per-column boundary rows (inclusive downward)."""
    width = boundary.shape[0]
    boundary_rows = np.round(boundary).astype(np.int32)
    no_mask = boundary_rows >= height
    boundary_rows = np.clip(boundary_rows, 0, height - 1)
    rows = np.arange(height, dtype=np.int32)[:, None]
    masked = rows >= boundary_rows[None, :]
    masked[:, no_mask] = False
    return masked


def scale_boundary(boundary: np.ndarray, work_height: int, full_height: int) -> np.ndarray:
    """Scale boundary row indices from working to full resolution."""
    if work_height == full_height:
        return boundary.copy()
    scale = full_height / work_height
    return boundary * scale


def boundary_wrap_slope(boundary: np.ndarray) -> float:
    """Absolute row difference between column 0 and column W-1 (ERP seam)."""
    return float(abs(boundary[0] - boundary[-1]))


def parse_seam_fracs(text: str) -> list[float]:
    """Parse comma-separated seam fractions (e.g. ``'0.25,0.75'``)."""
    return [float(part.strip()) for part in text.split(",") if part.strip()]


def seam_zone_mask(
    width: int,
    seam_fracs: list[float],
    seam_halfwidth_frac: float,
) -> np.ndarray:
    """Boolean mask of columns allowed extra downward sag at stitch seams."""
    half_cols = max(1, int(round(seam_halfwidth_frac * width)))
    zones = np.zeros(width, dtype=bool)
    for frac in seam_fracs:
        center = int(round(frac * width)) % width
        for offset in range(-half_cols, half_cols + 1):
            zones[(center + offset) % width] = True
    return zones


def coarse_silhouette_line(boundary: np.ndarray, coarse_window: int) -> np.ndarray:
    """Wrapped rolling minimum — coarse quasi-elliptical helmet silhouette."""
    return rolling_minimum_wrapped(boundary, coarse_window)


def apply_sag_limit(
    boundary: np.ndarray,
    *,
    coarse_window: int = 200,
    max_sag: float = 20.0,
    seam_fracs: list[float] | None = None,
    seam_halfwidth: float = 0.03,
    seam_sag: float = 70.0,
    detected: np.ndarray | None = None,
    coarse_fill_row: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clamp downward sag relative to the coarse silhouette (rows move up).

    Returns ``(clamped, coarse_line, was_clamped)`` where *was_clamped* marks
    columns whose boundary row decreased (physically implausible dips removed).

  When *detected* is set, only those columns are clamped. Non-detected columns
  are filled with *coarse_fill_row* (or the max detected row) for coarse-line
  estimation so ERP edge sentinels do not distort the silhouette prior.
    """
    if seam_fracs is None:
        seam_fracs = [0.25, 0.75]
    width = boundary.shape[0]
    values = boundary.astype(np.float64).copy()
    if detected is not None and detected.any():
        fill_row = coarse_fill_row
        if fill_row is None:
            fill_row = float(np.max(values[detected]))
        values[~detected] = fill_row
    coarse = coarse_silhouette_line(values, coarse_window)
    seam_zones = seam_zone_mask(width, seam_fracs, seam_halfwidth)
    allowed_sag = np.where(seam_zones, seam_sag, max_sag)
    ceiling = coarse + allowed_sag
    clamped = values.copy()
    if detected is None:
        clamped = np.minimum(values, ceiling)
    else:
        clamped[detected] = np.minimum(values[detected], ceiling[detected])
    was_clamped = clamped < values - 1e-9
    return clamped, coarse, was_clamped


def enforce_wrap_continuity(
    boundary: np.ndarray,
    *,
    max_jump: float = 2.0,
    seam_blend_cols: int = 12,
) -> np.ndarray:
    """Blend ERP wrap seam so column 0 and W-1 agree within *max_jump*."""
    out = boundary.astype(np.float64).copy()
    width = out.shape[0]
    if width < 2:
        return out

    mid = (out[0] + out[-1]) / 2.0
    blend_cols = min(seam_blend_cols, width // 2)
    for offset in range(blend_cols):
        weight = (blend_cols - offset) / blend_cols
        left = int(offset)
        right = width - 1 - offset
        out[left] = out[left] * (1.0 - weight) + mid * weight
        out[right] = out[right] * (1.0 - weight) + mid * weight
    out[0] = out[-1] = mid
    if boundary_wrap_slope(out) > max_jump + 1e-9:
        out[0] = out[-1] = mid
    return out


def pin_wrap_endpoints(boundary: np.ndarray, *, max_jump: float = 2.0) -> np.ndarray:
    """Pin column 0 and W-1 to their average without blending interior columns."""
    out = boundary.astype(np.float64).copy()
    if out.shape[0] < 2:
        return out
    mid = (out[0] + out[-1]) / 2.0
    out[0] = out[-1] = mid
    if boundary_wrap_slope(out) > max_jump + 1e-9:
        out[0] = out[-1] = mid
    return out
