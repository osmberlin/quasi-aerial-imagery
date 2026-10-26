"""I/O helpers: frame loading, overlays, mask application with EXIF preservation."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from helmet_mask.boundary import scale_boundary
from helmet_mask.core import compute_leak_score, derive_mask_from_frames
from helmet_mask.offsets import (
    DEFAULT_CONTOUR_CONFIDENCE_MIN,
    DEFAULT_FRAME_LEAK_TARGET,
    FrameOffset,
    HelmetPaletteModel,
    OffsetEstimationResult,
    build_helmet_palette_model,
    compute_palette_leak_score,
    estimate_per_frame_offsets,
    per_frame_boundaries,
    per_frame_masks as build_per_frame_masks,
)
from helmet_mask.texture import TextureParams, derive_mask_texture, sample_indices

METHODS = ("stability", "texture")


def collect_jpegs(directory: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            paths.append(path)
    return paths


def parse_size(text: str) -> tuple[int, int]:
    if "x" not in text.lower():
        raise ValueError("downscale must be WIDTHxHEIGHT")
    w_str, h_str = text.lower().split("x", 1)
    width, height = int(w_str), int(h_str)
    if width <= 0 or height <= 0:
        raise ValueError("downscale dimensions must be positive")
    return width, height


def load_frame_stack(
    paths: list[Path],
    size: tuple[int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Load JPGs resized to *size* as float32 RGB in [0, 255]."""
    width, height = size
    frames = np.empty((len(paths), height, width, 3), dtype=np.float32)
    for index, path in enumerate(paths):
        with Image.open(path) as image:
            resized = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
            frames[index] = np.asarray(resized, dtype=np.float32)
    return frames, size


def upscale_mask_nearest(mask: np.ndarray, full_size: tuple[int, int]) -> np.ndarray:
    """Upscale a boolean mask where True = pixel to mask."""
    image = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    upscaled = image.resize(full_size, Image.Resampling.NEAREST)
    return np.asarray(upscaled) >= 128


def mask_to_keep_image(mask: np.ndarray) -> Image.Image:
    """White=keep, black=masked."""
    keep = (~mask).astype(np.uint8) * 255
    return Image.fromarray(keep, mode="L")


def draw_mask_boundary(image: Image.Image, mask: np.ndarray, color: tuple[int, int, int]) -> Image.Image:
    """Draw the mask boundary in *color* on a copy of *image*."""
    boundary = mask ^ ndimage.binary_erosion(mask, iterations=1)
    output = image.copy()
    pixels = output.load()
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if boundary[y, x]:
                pixels[x, y] = color
    return output


def draw_boundary_overlay(
    image: Image.Image,
    boundary: np.ndarray,
    color: tuple[int, int, int] = (255, 0, 255),
) -> Image.Image:
    """Draw the per-column boundary curve as a magenta line."""
    output = image.copy()
    draw = ImageDraw.Draw(output)
    width = boundary.shape[0]
    height = image.height
    points: list[tuple[int, int]] = []
    for x in range(width):
        y = int(round(float(boundary[x])))
        y = max(0, min(height - 1, y))
        points.append((x, y))
    if len(points) >= 2:
        draw.line(points, fill=color, width=2)
    return output


def write_boundary_json(
    boundary: np.ndarray,
    path: Path,
    *,
    leak_score: float | None = None,
    leak_band_px: int | None = None,
) -> None:
    """Write per-column boundary row indices as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "width": int(boundary.shape[0]),
        "boundary_rows": [round(float(v), 2) for v in boundary.tolist()],
    }
    if leak_score is not None:
        payload["leak_score"] = round(float(leak_score), 6)
    if leak_band_px is not None:
        payload["leak_band_px"] = int(leak_band_px)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_overlays(
    paths: list[Path],
    mask: np.ndarray,
    overlay_dir: Path,
    *,
    boundary: np.ndarray | None = None,
    per_frame_boundaries: list[np.ndarray] | None = None,
    overlay_width: int = 1440,
    sample_names: list[str] | None = None,
    all_frames: bool = False,
) -> None:
    overlay_dir.mkdir(parents=True, exist_ok=True)

    full_height, full_width = mask.shape
    overlay_height = max(1, round(full_height * overlay_width / full_width))
    work_mask = Image.fromarray((mask.astype(np.uint8) * 255), mode="L").resize(
        (overlay_width, overlay_height),
        Image.Resampling.NEAREST,
    )
    work_mask_arr = np.asarray(work_mask) > 127

    work_boundary: np.ndarray | None = None
    if boundary is not None:
        scale = overlay_height / full_height
        work_boundary = boundary * scale

    if all_frames:
        sample_indices = set(range(len(paths)))
    else:
        sample_indices: set[int] = set()
        if sample_names:
            name_to_index = {p.name: i for i, p in enumerate(paths)}
            for name in sample_names:
                if name in name_to_index:
                    sample_indices.add(name_to_index[name])

        if len(paths) <= 3:
            sample_indices.update(range(len(paths)))
        else:
            sample_indices.update([0, len(paths) // 2, len(paths) - 1])

    for index in sorted(sample_indices):
        with Image.open(paths[index]) as image:
            rgb = image.convert("RGB").resize(
                (overlay_width, overlay_height),
                Image.Resampling.BILINEAR,
            )
            frame_boundary = work_boundary
            if per_frame_boundaries is not None:
                scale = overlay_height / full_height
                frame_boundary = per_frame_boundaries[index] * scale
            if frame_boundary is not None:
                outlined = draw_boundary_overlay(rgb, frame_boundary)
            else:
                outlined = draw_mask_boundary(rgb, work_mask_arr, (255, 0, 255))
            out_path = overlay_dir / f"{paths[index].stem}_overlay.jpg"
            outlined.save(out_path, quality=90)


def apply_mask_to_directory(
    paths: list[Path],
    mask: np.ndarray,
    apply_dir: Path,
    *,
    per_frame_masks: list[np.ndarray] | None = None,
) -> None:
    apply_dir.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(paths):
        frame_mask = per_frame_masks[index] if per_frame_masks is not None else mask
        keep = ~frame_mask
        with Image.open(path) as image:
            exif_bytes = image.info.get("exif")  # preserve GPS EXIF — the pipeline aligns on it
            rgb = np.array(image.convert("RGB"))  # np.array copies; np.asarray is read-only
            rgb[~keep] = 0
            out = Image.fromarray(rgb, mode="RGB")
            save_kwargs: dict = {"quality": 92}
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes
            out.save(apply_dir / path.name, **save_kwargs)


def write_offsets_json(
    path: Path,
    *,
    filenames: list[str],
    offsets: list[FrameOffset],
    per_frame_leaks: list[float],
    max_shift: int,
    ncc_min: float,
    palette_model: HelmetPaletteModel | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames_payload: list[dict[str, object]] = []
    for filename, offset, leak in zip(filenames, offsets, per_frame_leaks, strict=True):
        entry: dict[str, object] = {
            "filename": filename,
            "dy": int(offset.dy),
            "ncc_peak": round(float(offset.ncc_peak), 6),
            "low_confidence": bool(offset.low_confidence),
            "leak_score": round(float(leak), 6),
            "palette_leak": round(float(offset.palette_leak), 6),
        }
        if offset.dy_left is not None:
            entry["dy_left"] = int(offset.dy_left)
        if offset.dy_right is not None:
            entry["dy_right"] = int(offset.dy_right)
        if offset.dy_per_column is not None:
            entry["dy_per_column"] = [round(float(v), 2) for v in offset.dy_per_column.tolist()]
        if offset.leak_target_unmet:
            entry["leak_target_unmet"] = True
        if offset.palette_ambiguous:
            entry["palette_ambiguous"] = True
        if offset.contour_peak_score > 0:
            entry["contour_peak_score"] = round(float(offset.contour_peak_score), 6)
        if offset.score_profile is not None:
            entry["score_profile"] = offset.score_profile
        frames_payload.append(entry)

    dy_values = [int(offset.dy) for offset in offsets]
    payload: dict[str, object] = {
        "max_shift": int(max_shift),
        "ncc_min": float(ncc_min),
        "confidence_min": float(ncc_min),
        "dy_min": int(min(dy_values)) if dy_values else 0,
        "dy_max": int(max(dy_values)) if dy_values else 0,
        "low_confidence_count": sum(1 for offset in offsets if offset.low_confidence),
        "leak_target_unmet_count": sum(1 for offset in offsets if offset.leak_target_unmet),
        "frames": frames_payload,
    }
    if palette_model is not None:
        payload["palette_street_separation"] = round(float(palette_model.separation), 3)
        payload["palette_ambiguous"] = bool(palette_model.ambiguous)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def compute_per_frame_palette_leak_scores(
    frames: np.ndarray,
    boundaries: list[np.ndarray],
    palette_model: HelmetPaletteModel,
    *,
    height: int,
    palette_band_px: int,
) -> list[float]:
    leaks: list[float] = []
    for index, boundary in enumerate(boundaries):
        if frames.shape[0] > 1:
            ref_rgb = np.median(np.delete(frames, index, axis=0), axis=0)
        else:
            ref_rgb = frames[index]
        leaks.append(
            compute_palette_leak_score(
                frames[index],
                boundary,
                palette_model,
                height=height,
                apply_dy=False,
                band_px=palette_band_px,
                median_rgb=ref_rgb,
            )
        )
    return leaks


def compute_mask_iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    """IoU on masked (True) pixels."""
    if mask_a.shape != mask_b.shape:
        smaller = (
            min(mask_a.shape[0], mask_b.shape[0]),
            min(mask_a.shape[1], mask_b.shape[1]),
        )
        mask_a = mask_a[: smaller[0], : smaller[1]]
        mask_b = mask_b[: smaller[0], : smaller[1]]
    intersection = np.logical_and(mask_a, mask_b).sum()
    union = np.logical_or(mask_a, mask_b).sum()
    if union == 0:
        return 1.0
    return float(intersection) / float(union)


def run_helmet_mask(
    input_dir: Path,
    out_mask: Path,
    overlay_dir: Path,
    *,
    apply_dir: Path | None = None,
    boundary_json: Path | None = None,
    region_top_frac: float = 0.45,
    downscale: tuple[int, int] = (1440, 720),
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
    sample_names: list[str] | None = None,
    all_frames: bool = False,
    per_frame_offsets: bool = True,
    max_offset_px: int = 120,
    offsets_json: Path | None = None,
    ncc_min: float = DEFAULT_CONTOUR_CONFIDENCE_MIN,
    frame_leak_target: float = DEFAULT_FRAME_LEAK_TARGET,
    refine_max_extra: int = 30,
    palette_band_px: int = 80,
    method: str = "stability",
    texture_params: dict[str, float] | None = None,
) -> dict[str, object]:
    """Derive and write the helmet mask. ``method="texture"`` ignores per-frame offsets (static mask).

    *texture_params* override ``helmet_mask.texture.TextureParams`` fields (texture method only).
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    t0 = time.perf_counter()
    paths = collect_jpegs(input_dir)
    if len(paths) < 3:
        raise ValueError(f"need at least 3 JPG frames in {input_dir}, found {len(paths)}")

    with Image.open(paths[0]) as first_image:
        full_size = first_image.size

    leak_score: float | None = None
    relaxed_mask = median_rgb = None
    texture_used: TextureParams | None = None
    if method == "texture":
        # the helmet is fixed in image space: one static mask for every frame, no per-frame offsets
        per_frame_offsets = False
        texture_used = TextureParams(region_top_frac=region_top_frac, **(texture_params or {}))
        used = sample_indices(len(paths), texture_used.n_frames)
        frames, work_size = load_frame_stack([paths[i] for i in used], downscale)
        work_mask, work_boundary = derive_mask_texture(frames, **asdict(texture_used))
    else:
        frames, work_size = load_frame_stack(paths, downscale)
        (
            work_mask,
            work_boundary,
            leak_score,
            relaxed_mask,
            median_rgb,
        ) = derive_mask_from_frames(
            frames,
            region_top_frac=region_top_frac,
            stability_percentile=stability_percentile,
            relaxed_factor=relaxed_factor,
            dilate_px=dilate_px,
            min_blob_frac=min_blob_frac,
            margin_px=margin_px,
            coarse_window=coarse_window,
            max_sag=max_sag,
            seam_fracs=seam_fracs,
            seam_halfwidth=seam_halfwidth,
            seam_sag=seam_sag,
            envelope_window=envelope_window,
            spike_min_run=spike_min_run,
            notch_min_run=notch_min_run,
            outlier_mad_k=outlier_mad_k,
            gaussian_sigma=gaussian_sigma,
            simplify_epsilon=simplify_epsilon,
            simplify_lift_window=simplify_lift_window,
            simplify_gaussian_sigma=simplify_gaussian_sigma,
            leak_band_px=leak_band_px,
            return_relaxed=True,
        )
    work_height = work_size[1]

    offset_result: OffsetEstimationResult | None = None
    work_frame_boundaries: list[np.ndarray] | None = None
    work_frame_masks: list[np.ndarray] | None = None
    per_frame_leak_scores: list[float] | None = None
    palette_model: HelmetPaletteModel | None = None
    if per_frame_offsets:
        palette_model = build_helmet_palette_model(median_rgb, work_boundary, height=work_height)
        offset_result = estimate_per_frame_offsets(
            frames,
            median_rgb,
            work_boundary,
            palette_model=palette_model,
            relaxed_mask=relaxed_mask,
            max_shift=max_offset_px,
            ncc_min=ncc_min,
            max_abs_dy=max_offset_px,
            margin_px=margin_px,
            leak_band_px=leak_band_px,
            palette_band_px=palette_band_px,
            frame_leak_target=frame_leak_target,
            refine_max_extra=refine_max_extra,
        )
        palette_model = offset_result.palette_model or palette_model
        work_frame_boundaries = per_frame_boundaries(
            work_boundary,
            offset_result.offsets,
            height=work_height,
            margin_px=margin_px,
        )
        work_frame_masks = build_per_frame_masks(work_frame_boundaries, work_height)
        if palette_model is not None:
            per_frame_leak_scores = compute_per_frame_palette_leak_scores(
                frames,
                work_frame_boundaries,
                palette_model,
                height=work_height,
                palette_band_px=palette_band_px,
            )
            updated_offsets = [
                FrameOffset(
                    dy=offset.dy,
                    ncc_peak=offset.ncc_peak,
                    low_confidence=offset.low_confidence,
                    dy_left=offset.dy_left,
                    dy_right=offset.dy_right,
                    dy_per_column=offset.dy_per_column,
                    leak_target_unmet=offset.leak_target_unmet,
                    palette_ambiguous=offset.palette_ambiguous,
                    palette_leak=per_frame_leak_scores[index],
                    contour_peak_score=offset.contour_peak_score,
                    score_profile=offset.score_profile,
                )
                for index, offset in enumerate(offset_result.offsets)
            ]
            offset_result = OffsetEstimationResult(
                offsets=updated_offsets,
                dy_values=offset_result.dy_values,
                low_confidence_count=offset_result.low_confidence_count,
                palette_model=offset_result.palette_model,
            )

    full_height = full_size[1]
    margin_full = int(round(margin_px * full_height / work_height))
    full_boundary = scale_boundary(work_boundary, work_height, full_height)

    full_mask = upscale_mask_nearest(work_mask, full_size)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    mask_to_keep_image(full_mask).save(out_mask)

    full_frame_boundaries: list[np.ndarray] | None = None
    full_frame_masks: list[np.ndarray] | None = None
    if work_frame_boundaries is not None:
        full_frame_boundaries = [
            scale_boundary(boundary, work_height, full_height) for boundary in work_frame_boundaries
        ]
        full_frame_masks = [
            upscale_mask_nearest(frame_mask, full_size) for frame_mask in work_frame_masks or []
        ]

    if boundary_json is not None:
        write_boundary_json(
            full_boundary,
            boundary_json,
            leak_score=leak_score,
            leak_band_px=leak_band_px if method == "stability" else None,
        )

    if offsets_json is not None and offset_result is not None and per_frame_leak_scores is not None:
        write_offsets_json(
            offsets_json,
            filenames=[p.name for p in paths],
            offsets=offset_result.offsets,
            per_frame_leaks=per_frame_leak_scores,
            max_shift=max_offset_px,
            ncc_min=ncc_min,
            palette_model=palette_model,
        )

    write_overlays(
        paths,
        full_mask,
        overlay_dir,
        boundary=full_boundary,
        per_frame_boundaries=full_frame_boundaries,
        sample_names=sample_names,
        all_frames=all_frames,
    )

    if apply_dir is not None:
        apply_mask_to_directory(
            paths,
            full_mask,
            apply_dir,
            per_frame_masks=full_frame_masks,
        )

    masked_pixels = int(full_mask.sum())
    total_pixels = full_mask.size
    elapsed = time.perf_counter() - t0
    summary: dict[str, object] = {
        "method": method,
        "frame_count": len(paths),
        "input_paths": [str(p.resolve()) for p in paths],
        "input_filenames": [p.name for p in paths],
        "full_size": full_size,
        "work_size": work_size,
        "masked_pixels": masked_pixels,
        "masked_ratio": masked_pixels / total_pixels,
        "boundary_min_row": int(np.round(full_boundary.min())),
        "boundary_max_row": int(np.round(full_boundary.max())),
        "region_top_frac": region_top_frac,
        "downscale": downscale,
        "runtime_s": elapsed,
        "out_mask": str(out_mask),
        "overlay_dir": str(overlay_dir),
        "boundary_json": str(boundary_json) if boundary_json else None,
        "per_frame_offsets": per_frame_offsets,
        "offsets_json": str(offsets_json) if offsets_json else None,
    }
    if method == "stability":
        summary.update(
            {
                "leak_score": leak_score,
                "leak_band_px": leak_band_px,
                "margin_px_work": margin_px,
                "margin_px_full": margin_full,
                "coarse_window": coarse_window,
                "max_sag": max_sag,
                "seam_fracs": seam_fracs if seam_fracs is not None else [0.25, 0.75],
                "seam_halfwidth": seam_halfwidth,
                "seam_sag": seam_sag,
                "simplify_epsilon": simplify_epsilon,
                "simplify_lift_window": simplify_lift_window,
                "simplify_gaussian_sigma": simplify_gaussian_sigma,
                "stability_percentile": stability_percentile,
                "relaxed_factor": relaxed_factor,
                "dilate_px": dilate_px,
                "min_blob_frac": min_blob_frac,
                "envelope_window": envelope_window,
                "spike_min_run": spike_min_run,
                "notch_min_run": notch_min_run,
                "outlier_mad_k": outlier_mad_k,
                "gaussian_sigma": gaussian_sigma,
                "max_offset_px": max_offset_px,
                "ncc_min": ncc_min,
                "frame_leak_target": frame_leak_target,
                "refine_max_extra": refine_max_extra,
                "palette_band_px": palette_band_px,
            }
        )
    if texture_used is not None:
        summary["texture_params"] = asdict(texture_used)
        summary["texture_frames_used"] = len(frames)
    if palette_model is not None:
        summary["palette_street_separation"] = float(palette_model.separation)
        summary["palette_ambiguous"] = bool(palette_model.ambiguous)
    if offset_result is not None and per_frame_leak_scores is not None:
        summary["dy_min"] = int(offset_result.dy_values.min())
        summary["dy_max"] = int(offset_result.dy_values.max())
        summary["low_confidence_count"] = int(offset_result.low_confidence_count)
        summary["leak_target_unmet_count"] = sum(
            1 for offset in offset_result.offsets if offset.leak_target_unmet
        )
        summary["worst_frame_leak_score"] = float(max(per_frame_leak_scores))
        summary["per_frame_leak_scores"] = per_frame_leak_scores
        summary["mean_dy"] = float(offset_result.dy_values.mean())
        worst_index = int(np.argmax(per_frame_leak_scores))
        summary["worst_frame_filename"] = paths[worst_index].name
        summary["worst_frame_dy"] = int(offset_result.offsets[worst_index].dy)
    return summary
