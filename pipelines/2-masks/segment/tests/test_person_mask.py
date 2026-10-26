"""Unit tests for person_mask.py on tiny synthetic labels and frames (label grid 60x120, full frames 120x240)."""

from __future__ import annotations

import json

import cv2
import numpy as np

import person_mask as pm

LAB_H, LAB_W = 60, 120
FULL_H, FULL_W = 120, 240  # factor 2 to the label grid
PERSON, ROAD = 19, 13
BUFFER = 6


def make_inputs(tmp_path):
    lab_a, lab_b = tmp_path / "labels_a", tmp_path / "labels_b"
    frames, out = tmp_path / "frames", tmp_path / "out"
    for d in (lab_a, lab_b, frames):
        d.mkdir()

    a = np.full((LAB_H, LAB_W), 255, np.uint8)
    a[20:30, 40:50] = PERSON  # blob 1, full res rows 40:60, cols 80:100
    a[5, 5] = PERSON  # single-pixel noise, dropped by --min-component-px
    a[45:, :] = ROAD  # ground, never person
    cv2.imwrite(str(lab_a / "frame0001.png"), a)

    b = np.full((LAB_H, LAB_W), 255, np.uint8)  # 255 everywhere except what it sees
    b[34:42, 100:110] = PERSON  # blob 2, only in this dir, full res rows 68:84, cols 200:220
    b[10:18, 0:4] = PERSON  # blob 3 on the x=0 seam, full res cols 0:8
    cv2.imwrite(str(lab_b / "frame0001.png"), b)

    grey = np.full((FULL_H, FULL_W), 128, np.uint8)
    cv2.imwrite(str(frames / "frame0001.jpg"), grey)
    cv2.imwrite(str(frames / "frame0002.jpg"), grey)  # no label file in any dir

    static = np.full((LAB_H, LAB_W), 255, np.uint8)  # half size on purpose, must be resized
    static[50:] = 0  # helmet: bottom 10 rows of the half-size mask
    cv2.imwrite(str(tmp_path / "static.png"), static)
    return lab_a, lab_b, frames, out, tmp_path / "static.png"


def run(*args):
    assert pm.main([str(a) for a in args]) == 0


def test_output_size_names_and_values(tmp_path):
    lab_a, lab_b, frames, out, static = make_inputs(tmp_path)
    run("--labels", lab_a, lab_b, "--frames", frames, "--out", out, "--static-mask", static, "--buffer-px", BUFFER)

    m = cv2.imread(str(out / "frame0001.jpg.png"), cv2.IMREAD_GRAYSCALE)
    assert m.shape == (FULL_H, FULL_W)
    assert set(np.unique(m)) <= {0, 255}

    # person blob from dir A, blob only in dir B, and the seam blob: interiors masked
    assert (m[40:60, 80:100] == 0).all()
    assert (m[68:84, 200:220] == 0).all()
    assert (m[20:36, 0:8] == 0).all()

    # buffer is a disc of BUFFER full-res px: distance 6 masked, 7 kept, diagonal 8.49 kept
    assert m[34, 90] == 0 and m[33, 90] == 255
    assert m[50, 74] == 0 and m[50, 73] == 255
    assert m[34, 74] == 255

    # buffer wraps across the x=0 seam: left blob reaches cols 234..239
    assert m[25, 234] == 0 and m[25, 233] == 255

    # single-pixel noise dropped
    assert (m[10:12, 10:12] == 255).all()

    # ground (road) is kept, far from persons
    assert (m[92, 20] == 255)
    assert (m[:100, 150:180] == 255).all()

    # static mask ANDed: bottom 20 full-res rows black even where nothing is a person
    assert (m[100:, :] == 0).all()


def test_frame_without_labels_gets_static_only_and_is_listed(tmp_path):
    lab_a, lab_b, frames, out, static = make_inputs(tmp_path)
    run("--labels", lab_a, lab_b, "--frames", frames, "--out", out, "--static-mask", static)

    m = cv2.imread(str(out / "frame0002.jpg.png"), cv2.IMREAD_GRAYSCALE)
    assert (m[:100] == 255).all() and (m[100:] == 0).all()

    report = json.loads((out / "person_mask.json").read_text())
    assert report["frames"] == 2
    assert report["frames_without_labels"] == {"count": 1, "names": ["frame0002.jpg"]}
    by_name = {r["frame"]: r for r in report["per_frame"]}
    assert by_name["frame0002.jpg"]["person"] == 0
    assert abs(by_name["frame0002.jpg"]["total"] - 20 / 120) < 1e-5
    assert by_name["frame0001.jpg"]["person"] > 0
    assert by_name["frame0001.jpg"]["total"] >= by_name["frame0001.jpg"]["person"]
    for key in ("person_share", "total_share"):
        assert set(report[key]) == {"median", "p95", "max"}
    assert report["args"]["buffer_px"] == pm.DEFAULT_BUFFER_PX  # this test passes no --buffer-px
    assert report["args"]["labels"] == [str(lab_a), str(lab_b)]


def test_names_filter_and_preview(tmp_path):
    lab_a, lab_b, frames, out, static = make_inputs(tmp_path)
    run("--labels", lab_a, lab_b, "--frames", frames, "--out", out, "--names", "frame0001.jpg", "--preview", "1")

    assert sorted(p.name for p in out.glob("*.png")) == ["frame0001.jpg.png"]
    prev = cv2.imread(str(out / "preview" / "frame0001.jpg"), cv2.IMREAD_COLOR)
    assert prev is not None and prev.shape[1] == pm.PREVIEW_WIDTH
    report = json.loads((out / "person_mask.json").read_text())
    assert report["frames"] == 1 and report["frames_without_labels"]["count"] == 0
