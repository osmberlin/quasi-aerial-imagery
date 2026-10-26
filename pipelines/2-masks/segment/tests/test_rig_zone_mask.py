"""Unit tests for rig_zone_mask.py on synthetic labels (small ERP, so the zone is a few dozen rows)."""

from __future__ import annotations

import numpy as np

import rig_zone_mask as rz

H, W = 200, 400
ZONE = 40
PERSON, ROAD, SIDEWALK = 19, 13, 15


def make_scene():
    static = np.zeros((H, W), bool)
    static[150:, :] = True  # helmet: bottom 50 rows
    static[140:, 300:] = True  # a bump, so the boundary is not flat
    lab = np.full((H, W), 255, np.uint8)
    lab[:150] = SIDEWALK
    lab[:, :] = np.where(static, 255, lab)
    return static, lab


def run(static, lab, **kw):
    zone = rz.zone_mask(static, ZONE)
    kw = {"buffer_px": 3, "close_px": 3, "touch_px": 2, **kw}
    return zone, rz.rig_additions(lab, static, zone, **kw)


def test_zone_is_directly_above_static_boundary():
    static, _ = make_scene()
    zone = rz.zone_mask(static, ZONE)
    assert zone[110:150, 10].all() and not zone[109, 10]
    assert zone[100:140, 350].all() and not zone[99, 350]
    assert not (zone & static).any()


def test_passenger_touching_static_is_masked_and_stays_in_zone():
    static, lab = make_scene()
    lab[120:150, 50:80] = PERSON  # passenger's head sitting on the helmet
    zone, added = run(static, lab)
    assert added[121:149, 52:78].all()
    assert not (added & static).any()
    assert not (added & ~zone).any()


def test_pedestrian_not_touching_static_is_not_masked():
    static, lab = make_scene()
    lab[100:125, 200:215] = PERSON  # inside the zone but a gap above the helmet
    _, added = run(static, lab)
    assert not added.any()


def test_road_pixels_are_never_masked():
    static, lab = make_scene()
    lab[120:150, 50:80] = PERSON
    lab[130:150, 80:95] = ROAD  # road right beside the passenger: the buffer must not swallow it
    lab[110:120, 50:80] = ROAD  # and road right above
    _, added = run(static, lab, buffer_px=6)
    assert not (added & (lab == ROAD)).any()


def test_nothing_above_the_zone_even_for_a_tall_person():
    static, lab = make_scene()
    lab[60:150, 100:130] = PERSON  # taller than the zone
    zone, added = run(static, lab)
    assert not added[:110].any()
    assert not (added & ~zone).any()
    assert added[111:149, 102:128].all()


def test_component_wrapping_over_the_seam_is_kept():
    static, lab = make_scene()
    lab[120:150, 390:400] = PERSON
    lab[120:150, 0:10] = PERSON
    static[130:150, 0:5] = True  # touches only on the left half of the wrapped blob
    zone, added = run(static, lab)
    assert added[121:140, 392:399].all()  # right half is connected through the seam
    assert not (added & ~zone).any()


def test_sky_mask_marks_sky_black():
    lab = np.full((20, 40), 13, np.uint8)
    lab[:5] = rz.SKY
    m = rz.sky_mask(lab, buffer_px=1)
    assert (m[:5] == 0).all() and m[5, 5] == 0 and m[10:].all()


def test_component_larger_than_cap_is_dropped():
    static, lab = make_scene()
    lab[110:150, 20:120] = 55  # a car parked right next to the helmet, 4000 px
    lab[120:150, 200:230] = PERSON  # the passenger, 900 px
    zone, added = run(static, lab, max_component_px=2000)
    assert not added[:, :150].any()
    assert added[121:149, 202:228].all()
