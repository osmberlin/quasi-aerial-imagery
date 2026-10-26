"""Tests for the texture-stability mask method (helmet_mask.texture, --method texture)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from helmet_mask.cli import main
from helmet_mask.io import run_helmet_mask
from helmet_mask.texture import derive_mask_texture, sample_indices, to_gray_stack

HEIGHT, WIDTH, FRAMES = 200, 400, 30
PATH_COLS = slice(150, 250)  # uniform grey path directly above the helmet
RIM_ROWS = 25  # textured rim of the helmet; the interior below it is flat


def _crest(wrap_bump: bool = False) -> np.ndarray:
    """Helmet top row per column (float); optionally an upward knob centred on x=0 (wraps the seam)."""
    x = np.arange(WIDTH)
    crest = 150 + 6 * np.sin(2 * np.pi * x * 2 / WIDTH)
    if wrap_bump:
        d = np.minimum(x, WIDTH - x)
        crest = crest - 18 * np.exp(-((d / 30.0) ** 2))
    return np.round(crest).astype(int)


def _noise(rng: np.random.Generator, shape: tuple[int, int]) -> np.ndarray:
    return (ndimage.gaussian_filter(rng.random(shape), 1.5) - 0.5) * 700.0  # zero-mean, SD ~ 40 grey levels


def _make_stack(crest: np.ndarray, seed: int = 0) -> np.ndarray:
    """(FRAMES, H, W) gray stack: static textured helmet rim + flat interior, uniform path, moving ground."""
    rng = np.random.default_rng(seed)
    rows = np.arange(HEIGHT)[:, None]
    helmet = rows >= crest[None, :]
    rim = helmet & (rows < crest[None, :] + RIM_ROWS)
    helmet_texture = 110 + _noise(rng, (HEIGHT, WIDTH)) * 0.6
    ground_texture = 110 + _noise(rng, (HEIGHT + 60, WIDTH + 60))
    stack = np.empty((FRAMES, HEIGHT, WIDTH), np.float32)
    for index in range(FRAMES):
        dy, dx = rng.integers(6, 30, size=2) * rng.choice([-1, 1], size=2)  # world moves between frames
        ground = np.roll(ground_texture, (dy, dx), axis=(0, 1))[30 : 30 + HEIGHT, 30 : 30 + WIDTH]
        frame = ground.copy()
        frame[:, PATH_COLS] = 128.0  # colour-constant, untextured, identical in every frame
        frame[helmet] = 40.0  # flat helmet interior
        frame[rim] = helmet_texture[rim]
        stack[index] = np.clip(frame, 0, 255)
    return stack


def test_boundary_matches_helmet_top_including_under_uniform_path() -> None:
    crest = _crest()
    mask, boundary = derive_mask_texture(_make_stack(crest), buffer_px=0)

    truth = np.arange(HEIGHT)[:, None] >= crest[None, :]
    error = np.abs(boundary - crest)
    assert error.mean() < 3.0
    assert error[PATH_COLS].max() < 5.0  # not pulled up through the flat path
    assert abs(mask.mean() - truth.mean()) < 0.02
    intersection = (mask & truth).sum()
    assert intersection / (mask | truth).sum() > 0.95


def test_uniform_path_is_not_masked() -> None:
    crest = _crest()
    mask, boundary = derive_mask_texture(_make_stack(crest))  # default 6 px buffer

    assert not mask[: int(crest[PATH_COLS].min()) - 10, PATH_COLS].any()
    assert not mask[0 : HEIGHT // 2, :].any()  # nothing above region_top_frac
    assert mask[crest.max() + 5 :, :].all()  # helmet incl. flat interior is masked
    assert (boundary <= crest + 1).all()  # buffer keeps the whole helmet masked


def test_rgb_stack_equals_gray_stack() -> None:
    stack = _make_stack(_crest())
    rgb = np.repeat(stack[..., None], 3, axis=-1)
    mask_gray, _ = derive_mask_texture(stack)
    mask_rgb, _ = derive_mask_texture(rgb)
    assert (mask_gray == mask_rgb).mean() > 0.999


def test_crest_crossing_the_seam_wraps() -> None:
    crest = _crest(wrap_bump=True)
    _mask, boundary = derive_mask_texture(_make_stack(crest, seed=1), buffer_px=0)

    assert abs(boundary[0] - boundary[-1]) < 2.0  # continuous across x=0 / x=W-1
    assert np.abs(boundary - crest).mean() < 4.0
    assert boundary[0] < crest[WIDTH // 2] - 10  # the knob on the seam is kept, not cut off
    assert abs(boundary[0] - crest[0]) < 6.0


def test_sample_indices_spread_and_unique() -> None:
    idx = sample_indices(100, 5)
    assert idx.tolist() == [0, 25, 50, 74, 99]
    assert sample_indices(3, 40).tolist() == [0, 1, 2]
    assert to_gray_stack(np.full((2, 4, 6, 3), 255.0)).max() <= 1.0 + 1e-6


def _write_jpegs(stack: np.ndarray, directory: Path) -> None:
    directory.mkdir()
    for index, frame in enumerate(stack):
        Image.fromarray(frame.astype(np.uint8), mode="L").convert("RGB").save(
            directory / f"frame_{index:02d}.jpg", quality=95
        )


def test_run_helmet_mask_texture_method_writes_static_mask(tmp_path: Path) -> None:
    crest = _crest()
    _write_jpegs(_make_stack(crest), tmp_path / "in")
    out_mask = tmp_path / "out" / "mask.png"
    boundary_json = tmp_path / "out" / "boundary.json"

    summary = run_helmet_mask(
        tmp_path / "in",
        out_mask,
        tmp_path / "out" / "overlays",
        apply_dir=tmp_path / "out" / "applied",
        boundary_json=boundary_json,
        downscale=(WIDTH, HEIGHT),
        method="texture",
        texture_params={"buffer_px": 0.0, "n_frames": 10},
    )

    truth_share = (HEIGHT - crest).mean() / HEIGHT
    assert summary["method"] == "texture"
    assert summary["per_frame_offsets"] is False  # static mask, offsets never applied
    assert summary["texture_frames_used"] == 10
    assert summary["texture_params"]["n_frames"] == 10
    assert abs(summary["masked_ratio"] - truth_share) < 0.03
    keep = np.asarray(Image.open(out_mask).convert("L")) >= 128  # white = keep
    assert keep[:50].all() and not keep[-5:].any()
    payload = json.loads(boundary_json.read_text())
    assert payload["width"] == WIDTH and "leak_score" not in payload
    assert len(list((tmp_path / "out" / "overlays").glob("*_overlay.jpg"))) == 3
    applied = np.asarray(Image.open(tmp_path / "out" / "applied" / "frame_00.jpg").convert("L"))
    assert applied[-5:].max() == 0 and applied[:50].max() > 0  # same static mask on every frame


def test_cli_method_texture_ignores_per_frame_offsets(tmp_path: Path) -> None:
    _write_jpegs(_make_stack(_crest()), tmp_path / "in")
    run_json = tmp_path / "out" / "run.json"

    code = main(
        [
            "--input", str(tmp_path / "in"),
            "--out-mask", str(tmp_path / "out" / "mask.png"),
            "--downscale", f"{WIDTH}x{HEIGHT}",
            "--method", "texture",
            "--texture-buffer-px", "3",
            "--texture-frames", "12",
            "--per-frame-offsets",
            "--run-json", str(run_json),
        ]
    )  # fmt: skip

    summary = json.loads(run_json.read_text())
    assert code == 0
    assert summary["method"] == "texture" and summary["per_frame_offsets"] is False
    assert summary["texture_params"]["buffer_px"] == 3.0 and summary["texture_frames_used"] == 12
    assert not (tmp_path / "out" / "offsets.json").exists()
