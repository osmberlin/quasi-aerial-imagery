"""DEM levelling and track-to-terrain shift in render_ground_ortho.py on synthetic terrain."""

from __future__ import annotations

import numpy as np
import pytest

import render_ground_ortho as rg

E0, N0 = 402000.0, 5370000.0  # realistic UTM magnitudes: conditioning matters
CAM_H = 2.0


def make_dem(f, size=240.0, cell=0.5) -> rg.Dem:
    n = int(size / cell)
    e = E0 + (np.arange(n) + 0.5) * cell
    north = N0 + size - (np.arange(n) + 0.5) * cell
    ee, nn = np.meshgrid(e, north)
    return rg.Dem(f(ee, nn).astype(np.float32), E0, N0 + size, cell, cell)


def plane(e, n):  # about 17 degrees uphill to the north-east
    return 300.0 + 0.30 * (e - E0) + 0.10 * (n - N0)


def hill(e, n):
    return 300.0 + 12.0 * np.sin((e - E0) / 30.0) + 9.0 * np.cos((n - N0) / 25.0) + 0.05 * (e - E0)


def track(count=120):
    """An S-shaped walk, so the camera plane is well conditioned (a straight track cannot see roll)."""
    t = np.linspace(0, 1, count)
    return np.column_stack([E0 + 60 + 100 * t, N0 + 120 + 25 * np.sin(2 * np.pi * t * 1.5) + 20 * t])


def rz(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def test_dem_sample_is_exact_on_a_plane_and_nan_outside():
    dem = make_dem(plane)
    xy = np.array([[E0 + 10.3, N0 + 77.7], [E0 + 200.1, N0 + 5.55], [E0 - 1, N0 + 50], [E0 + 100, N0 + 300]])
    got = rg.dem_sample(dem, xy)
    assert np.allclose(got[:2], plane(xy[:2, 0], xy[:2, 1]), atol=0.02)  # float32 grid at ~300 m
    assert np.isnan(got[2:]).all()


def test_dem_sample_nan_next_to_nodata():
    z = np.ones((4, 4), np.float32)
    z[1, 1] = np.nan
    dem = rg.Dem(z, 0.0, 4.0, 1.0, 1.0)
    out = rg.dem_sample(dem, np.array([[1.2, 2.8], [3.0, 1.0]]))
    assert np.isnan(out[0]) and out[1] == pytest.approx(1.0)


def test_tilt_rotation_is_a_rotation_about_a_horizontal_axis():
    r = rg.tilt_rotation(0.05, -0.03)
    assert np.allclose(r @ r.T, np.eye(3)) and np.linalg.det(r) == pytest.approx(1.0)
    assert np.allclose(rg.tilt_rotation(0, 0), np.eye(3))


def synthetic_model(dem_fn, true_tilt_deg, scale=1.7, outliers=0):
    """Model centres of cameras that walk CAM_H above the terrain, in a frame tilted by true_tilt_deg."""
    xy = track()
    up = dem_fn(xy[:, 0], xy[:, 1]) + CAM_H
    if outliers:  # stairs, stops: cameras that are higher than the terrain says
        up[::max(len(up) // outliers, 1)] += 3.0
    world = np.column_stack([xy - [E0, N0], up - 300.0])
    tilt = rg.tilt_rotation(*np.radians(true_tilt_deg))
    model = (tilt @ rz(0.7) @ world.T).T / scale + [3.0, -2.0, 5.0]
    return model, xy, up, tilt @ rz(0.7), scale


@pytest.mark.parametrize("tilt_deg,outliers", [((6.0, -4.0), 0), ((-9.0, 3.0), 6)])
def test_level_to_dem_recovers_tilt_scale_and_offset(tilt_deg, outliers):
    dem = make_dem(plane)
    model, xy, up, true_rot, scale = synthetic_model(plane, tilt_deg, outliers=outliers)
    lev = rg.level_to_dem(model, xy, dem, CAM_H, fit_tilt=True)
    # the tilted model's Z is vertical: the tilt maps the true "up" axis of the model onto Z
    assert np.allclose((lev["tilt"] @ true_rot)[2], [0, 0, 1], atol=2e-3)
    assert lev["scale"] == pytest.approx(scale, rel=2e-3)
    ok = np.abs(lev["residual"]) < 0.05
    assert ok.sum() >= len(xy) - outliers
    elevation = lev["z_m"] + lev["offset"]
    assert np.median(np.abs(elevation - up)) < 0.01
    assert lev["info"]["residual_median_abs_m"] < 0.01
    assert lev["info"]["cameras_kept"] <= len(xy) - outliers + 1


def test_level_to_dem_without_tilt_fits_only_the_offset():
    dem = make_dem(plane)
    model, xy, up, _, _ = synthetic_model(plane, (0.0, 0.0))
    # rotated about z only, so Z is already vertical: nothing to tilt
    lev = rg.level_to_dem(model, xy, dem, CAM_H, fit_tilt=False)
    assert np.allclose(lev["tilt"], np.eye(3))
    assert lev["info"]["tilt_applied_deg"] == 0
    assert np.median(np.abs(lev["residual"])) < 0.01


def test_level_to_dem_residual_shows_a_wrong_level():
    dem = make_dem(plane)
    model, xy, up, _, _ = synthetic_model(plane, (8.0, 0.0))
    flat = rg.level_to_dem(model, xy, dem, CAM_H, fit_tilt=False)
    assert flat["info"]["residual_p90_abs_m"] > 2.0
    fixed = rg.level_to_dem(model, xy, dem, CAM_H, fit_tilt=True)
    assert fixed["info"]["residual_p90_abs_m"] < 0.02


@pytest.mark.parametrize("fit_tilt", [False, True])
def test_find_dem_shift_recovers_the_known_shift(fit_tilt):
    dem = make_dem(hill)
    xy = track()
    true_shift = np.array([2.35, -1.6])
    elevation = hill(xy[:, 0], xy[:, 1]) + CAM_H  # the camera really stands at xy
    gps = xy - true_shift  # but its position is off, so DEM(gps + shift) is the terrain under it
    if fit_tilt:  # a small residual tilt of the model, which the plane freedom in dem_spread absorbs
        rel = xy - xy.mean(0)
        elevation = elevation + 0.02 * rel[:, 0] - 0.01 * rel[:, 1] + 0.3
    use = np.ones(len(xy), bool)
    best = rg.find_dem_shift(dem, gps, elevation, CAM_H, use, fit_tilt)
    assert np.allclose(best, true_shift, atol=0.05)
    before = rg.dem_spread(dem, gps, elevation, CAM_H, np.zeros((1, 2)), use, fit_tilt)[0]
    after = rg.dem_spread(dem, gps, elevation, CAM_H, best[None], use, fit_tilt)[0]
    assert before > 10 * after


def test_shift_and_tilt_together():
    """Tilted model on a hill with a GPS offset: alternate level and shift as main() does."""
    dem = make_dem(hill)
    xy = track()
    true_shift = np.array([-1.9, 2.6])
    up = hill(xy[:, 0], xy[:, 1]) + CAM_H
    world = np.column_stack([xy - [E0, N0], up - 300.0])
    tilt = rg.tilt_rotation(*np.radians((4.0, -5.0)))
    model = (tilt @ rz(-0.4) @ world.T).T / 0.9 + [1.0, 2.0, -3.0]
    gps = xy - true_shift
    shift = np.zeros(2)
    lev = rg.level_to_dem(model, gps, dem, CAM_H, shift, True)
    for _ in range(5):
        found = rg.find_dem_shift(dem, lev["cam_xy"], lev["z_m"], CAM_H, lev["keep"], True)
        if np.linalg.norm(found - shift) < 0.03:
            break
        shift = found
        lev = rg.level_to_dem(model, gps, dem, CAM_H, shift, True)
    assert np.allclose(shift, true_shift, atol=0.15)
    assert lev["info"]["residual_p90_abs_m"] < 0.1
