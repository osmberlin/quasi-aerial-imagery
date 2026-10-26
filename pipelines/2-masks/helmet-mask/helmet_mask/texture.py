"""Texture-stability helmet mask: static *texture* marks the rig, untextured pixels stay unknown.

Colour stability (``helmet_mask.core``) also flags uniform world regions that merely stay the same colour
(a grey path, sky, a wall). Here only pixels with image *structure* vote: structure that is identical in
every frame (gradient coherence + local NCC against the temporal median) is rig, structure that changes is
world, flat pixels abstain. The boundary per column is the lowest row maximising the bottom-up vote sum,
so flat stretches above the rig are never claimed and flat holes inside the rig are filled.

The helmet is fixed in image space, so the result is one static mask; per-frame offsets do not apply.
All lengths are in working pixels (default 1440x720). Uses scipy.ndimage only (no OpenCV).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from helmet_mask.boundary import boundary_to_mask

# Same luma weights as cv2.cvtColor(BGR2GRAY).
GRAY_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)


@dataclass(frozen=True)
class TextureParams:
    """Tunables of the texture method; defaults are the values that worked on the 2026-10 captures (D-107)."""

    n_frames: int = 40  # uniformly sampled frames used (run_helmet_mask loads only these)
    # evidence
    pre_sigma: float = 1.0  # blur before the gradient
    ncc_sigma: float = 3.0  # window of the local NCC
    ncc_eps: float = 1e-4
    ncc_sd: float = 0.01  # NCC weight = min(local SD / ncc_sd, 1): flat patches count little
    R_sigma: float = 3.0  # gradient-weighted smoothing of the coherence R
    tex_sigma: float = 2.0
    tex_thr: float = 0.012  # smoothed mean gradient magnitude above which a pixel has texture
    # boundary votes
    region_top_frac: float = 0.45  # nothing above this fraction of the height is ever masked
    thr_static: float = 0.6  # textured and score >= thr_static votes +1 (rig)
    thr_moving: float = 0.35  # textured and score <= thr_moving votes -lam (world)
    lam: float = 1.0
    vote_win: int = 15  # horizontal averaging of the votes (columns)
    tie_tol: float = 1.0  # rows within this of the best cumulative vote are ties; lowest row wins
    min_evidence: float = 3.0  # columns with less static evidence than this get no mask (interpolated)
    # regularisation
    med_win: int = 41  # wrapped median (columns)
    close_frac: float = 0.18  # closing width as a fraction of W: bridges notches narrower than this
    min_win: int = 15  # wrapped rolling minimum, hugs the crest (columns)
    smooth_sigma: float = 6.0  # wrapped Gaussian (columns)
    buffer_px: float = 6.0  # safety margin: boundary moved up (rows)


def to_gray_stack(stack: np.ndarray) -> np.ndarray:
    """Return an (N, H, W) float32 stack in [0, 1] from an (N, H, W) gray or (N, H, W, 3) RGB stack (0-255)."""
    arr = np.asarray(stack, dtype=np.float32)
    if arr.ndim == 4:
        arr = arr @ GRAY_WEIGHTS
    if arr.ndim != 3:
        raise ValueError("expected an (N, H, W) or (N, H, W, 3) frame stack")
    return arr / 255.0


def sample_indices(n_total: int, n: int) -> np.ndarray:
    """Up to *n* uniformly spread, unique frame indices out of *n_total*."""
    n = max(1, min(n, n_total))
    return np.unique(np.round(np.linspace(0, n_total - 1, n)).astype(int))


def _blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur with OpenCV's border handling (reflect-101)."""
    return ndimage.gaussian_filter(image, sigma, mode="mirror")


def sobel_gradient(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sobel gradient (gx, gy), normalised by 8 so it is in intensity units per pixel."""
    gx = ndimage.sobel(image, axis=1, mode="mirror") / 8.0
    gy = ndimage.sobel(image, axis=0, mode="mirror") / 8.0
    return gx, gy


def compute_evidence(gray: np.ndarray, p: TextureParams) -> dict[str, np.ndarray]:
    """Per-pixel evidence that a pixel shows the same structure in every frame.

    *gray*: (N, H, W) in [0, 1]. Returns ``score`` = (R + ncc) / 2 and the boolean ``tex`` map.
    ``R`` is the gradient-vector coherence |mean grad| / mean |grad| (1 = same gradient in all frames),
    ``ncc`` the mean local NCC of each frame against the temporal median, weighted by local SD.
    """
    n, h, w = gray.shape
    gx_sum = np.zeros((h, w), np.float32)
    gy_sum = np.zeros((h, w), np.float32)
    g_sum = np.zeros((h, w), np.float32)
    ncc_sum = np.zeros((h, w), np.float32)
    ncc_cnt = np.zeros((h, w), np.float32)

    med = np.median(gray, axis=0).astype(np.float32)
    s = p.ncc_sigma
    mu_m = _blur(med, s)
    var_m = np.maximum(_blur(med * med, s) - mu_m * mu_m, 0)

    for frame in gray:
        gx, gy = sobel_gradient(_blur(frame, p.pre_sigma))
        gx_sum += gx
        gy_sum += gy
        g_sum += np.hypot(gx, gy)
        # local NCC against the temporal median; flat patches get little weight
        mu_i = _blur(frame, s)
        var_i = np.maximum(_blur(frame * frame, s) - mu_i * mu_i, 0)
        cov = _blur(frame * med, s) - mu_i * mu_m
        ncc = np.clip(cov / (np.sqrt(var_i * var_m) + p.ncc_eps), -1, 1)
        weight = np.minimum(np.sqrt(var_i) / p.ncc_sd, 1.0)
        ncc_sum += ncc * weight
        ncc_cnt += weight

    mean_gx, mean_gy, mean_g = gx_sum / n, gy_sum / n, g_sum / n
    coherence = np.hypot(mean_gx, mean_gy) / (mean_g + 1e-4)
    coherence = _blur(coherence * mean_g, p.R_sigma) / (_blur(mean_g, p.R_sigma) + 1e-6)
    ncc_mean = ncc_sum / (ncc_cnt + 1e-6)
    return {
        "score": 0.5 * (coherence + ncc_mean),
        "tex": _blur(mean_g, p.tex_sigma) > p.tex_thr,
    }


def derive_boundary(score: np.ndarray, tex: np.ndarray, p: TextureParams) -> np.ndarray:
    """Raw per-column boundary row from signed votes (+1 static texture, -lam moving texture, 0 flat).

    Returns the lowest row maximising the cumulative vote sum from the bottom up; ``H`` where a column has
    no static evidence. Votes are averaged over ``vote_win`` columns with ERP wrap.
    """
    h, _w = score.shape
    top = int(p.region_top_frac * h)
    votes = np.zeros(score.shape, np.float32)
    votes[tex & (score >= p.thr_static)] = 1.0
    votes[tex & (score <= p.thr_moving)] = -p.lam
    votes[:top] = 0
    votes = ndimage.uniform_filter1d(votes, size=p.vote_win, axis=1, mode="wrap")
    csum = np.cumsum(votes[::-1], axis=0)[::-1]  # csum[r] = sum of votes in rows >= r
    csum[:top] = -1e9
    best = csum.max(axis=0)
    near_best = csum >= (best - p.tie_tol)[None, :]
    rows = np.arange(h)[:, None]
    boundary = np.where(near_best, rows, -1).max(axis=0)
    boundary = np.where(best <= p.min_evidence, h, boundary)
    return boundary.astype(np.float64)


def regularise(raw: np.ndarray, height: int, p: TextureParams) -> np.ndarray:
    """Wrapped median, closing, rolling minimum, Gaussian, then the safety buffer (rows, float).

    Columns without evidence (``raw >= height``) are interpolated from their neighbours first.
    """
    r = raw.copy()
    bad = r >= height
    if bad.all():
        return np.full_like(r, height)
    if bad.any():
        idx = np.arange(len(r))
        r[bad] = np.interp(idx[bad], idx[~bad], r[~bad], period=len(r))
    r = ndimage.median_filter(r, size=p.med_win, mode="wrap")
    k = int(p.close_frac * len(r))
    if k > 1:  # closing of the mask = bridge boundary notches narrower than k columns
        r = ndimage.maximum_filter1d(ndimage.minimum_filter1d(r, size=k, mode="wrap"), size=k, mode="wrap")
    r = ndimage.minimum_filter1d(r, size=p.min_win, mode="wrap")
    r = ndimage.gaussian_filter1d(r, p.smooth_sigma, mode="wrap")
    return np.clip(r - p.buffer_px, int(p.region_top_frac * height), height)


def derive_mask_texture(stack: np.ndarray, **params: float) -> tuple[np.ndarray, np.ndarray]:
    """Derive the static helmet mask from a frame stack by texture stability.

    *stack*: (N, H, W) gray or (N, H, W, 3) RGB, values 0-255. *params* override ``TextureParams`` fields.
    Returns ``(mask, boundary)``: ``mask`` (H, W) bool with True = masked, ``boundary`` (W,) float
    first masked row per column (``H`` = nothing masked). A stack longer than ``n_frames`` is subsampled.
    """
    p = TextureParams(**params)
    gray = to_gray_stack(stack)
    gray = gray[sample_indices(len(gray), p.n_frames)]
    evidence = compute_evidence(gray, p)
    boundary = regularise(derive_boundary(evidence["score"], evidence["tex"], p), gray.shape[1], p)
    return boundary_to_mask(boundary, gray.shape[1]), boundary
