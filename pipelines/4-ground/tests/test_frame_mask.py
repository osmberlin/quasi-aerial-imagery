"""Per-frame mask selection in render_ground_ortho.py: --mask-dir file, else --mask, else none."""

from __future__ import annotations

import sys

import cv2
import numpy as np
import pytest

import render_ground_ortho as rg

W, H = 40, 20


def write_mask(path, value: int, shape=(H, W)):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), np.full(shape, value, np.uint8))


def test_frame_file_is_used_and_not_static(tmp_path):
    mask_dir = tmp_path / "masks"
    write_mask(mask_dir / "frame0904.jpg.png", 255)
    static = np.zeros((H, W), np.uint8)
    mask, source = rg.frame_mask("frame0904.jpg", mask_dir, static, W, H)
    assert source == "frame"
    assert mask is not static
    assert int(mask.min()) == 255


def test_missing_frame_file_falls_back_to_static(tmp_path):
    mask_dir = tmp_path / "masks"
    mask_dir.mkdir()
    static = np.full((H, W), 200, np.uint8)
    mask, source = rg.frame_mask("frame0905.jpg", mask_dir, static, W, H)
    assert source == "static"
    assert mask is static


def test_no_frame_file_and_no_static_gives_no_mask(tmp_path):
    mask, source = rg.frame_mask("frame0905.jpg", tmp_path, None, W, H)
    assert mask is None
    assert source == "none"


def test_no_mask_dir_uses_static_only(tmp_path):
    static = np.full((H, W), 7, np.uint8)
    mask, source = rg.frame_mask("frame0905.jpg", None, static, W, H)
    assert source == "static"
    assert mask is static


def test_file_must_carry_the_frame_extension(tmp_path):
    # COLMAP convention is <image name>.png, so frame0904.png does not belong to frame0904.jpg
    mask_dir = tmp_path / "masks"
    write_mask(mask_dir / "frame0904.png", 255)
    static = np.zeros((H, W), np.uint8)
    _, source = rg.frame_mask("frame0904.jpg", mask_dir, static, W, H)
    assert source == "static"


def test_per_frame_mask_of_other_size_is_resized_to_erp(tmp_path):
    mask_dir = tmp_path / "masks"
    raw = np.zeros((2 * H, 2 * W), np.uint8)
    raw[:H] = 255  # top half white, nearest-neighbour resize keeps it sharp
    mask_dir.mkdir()
    cv2.imwrite(str(mask_dir / "frame0906.jpg.png"), raw)
    mask, source = rg.frame_mask("frame0906.jpg", mask_dir, None, W, H)
    assert source == "frame"
    assert mask.shape == (H, W)
    assert set(np.unique(mask)) == {0, 255}
    assert mask[0, 0] == 255 and mask[-1, -1] == 0


def test_load_mask_rejects_unreadable_file(tmp_path):
    bad = tmp_path / "not-an-image.png"
    bad.write_bytes(b"not a png")
    with pytest.raises(SystemExit):
        rg.load_mask(bad, W, H)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
