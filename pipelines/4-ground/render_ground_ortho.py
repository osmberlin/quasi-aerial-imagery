#!/usr/bin/env python3
"""Ground orthophoto by projecting full-resolution ERP pixels onto a ground surface.

Poses come from a COLMAP text model (EQUIRECTANGULAR camera, metric, Z up, as written by
`pose_prior_mapper` with GPS priors). The ground is assumed to lie `camera_height` below each
camera; between cameras the ground height is interpolated. Output is a north-up GeoTIFF in
the scene CRS (auto UTM from GPS, EPSG:25833 in Berlin; `--crs` or env ORTHO_CRS override),
georeferenced by a 2D similarity fit of the camera centres to their EXIF GPS.

`--ground-dem` replaces that ground by an elevation model (a LiDAR terrain GeoTIFF in the scene CRS).
The vertical relation model -> DEM comes from the cameras (elevation = DEM at the camera + camera
height). On a slope the levelling of the model has to be true vertical for that: `--level dem` tilts
the model about horizontal axes until the camera centres fit DEM + camera height, and `--dem-shift`
registers the track horizontally to the terrain.

Masks: `--mask` is one ERP mask for all frames (white = usable). `--mask-dir DIR` gives each frame
its own mask, DIR/<frame file name>.png (COLMAP mask_path convention, e.g. frame0904.jpg.png);
a frame without such a file uses `--mask`, or no mask if that is not given. Masks of another size
are resized to the ERP size (nearest neighbour). Per-frame masks are read when the frame is
rendered, so they are never all in memory.

Nothing here reconstructs geometry: every output pixel is looked
up in the source frames.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.crs import resolve_crs  # noqa: E402


def quat_to_rot(w: float, x: float, y: float, z: float) -> np.ndarray:
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def read_model(model_txt: Path):
    """Return {name: (R_cam_from_world, t, centre, observations[(u, v, point_id)])} and points."""
    lines = [l for l in (model_txt / "images.txt").read_text().splitlines() if not l.startswith("#")]
    images = {}
    for head, obs in zip(lines[0::2], lines[1::2]):
        p = head.split()
        rot = quat_to_rot(*map(float, p[1:5]))
        t = np.array(list(map(float, p[5:8])))
        o = np.array(obs.split(), dtype=float).reshape(-1, 3) if obs.strip() else np.zeros((0, 3))
        images[p[9]] = (rot, t, -rot.T @ t, o[o[:, 2] >= 0])
    points = {}
    for l in (model_txt / "points3D.txt").read_text().splitlines():
        if l.startswith("#") or not l.strip():
            continue
        p = l.split()
        points[int(p[0])] = np.array(list(map(float, p[1:4])))
    return images, points


# Candidate ERP conventions: camera ray (x, y, z) -> (longitude, latitude-from-top-of-image).
ERP_CONVENTIONS = {
    "x-right_y-down_z-forward": lambda d: (np.arctan2(d[..., 0], d[..., 2]), np.arcsin(np.clip(d[..., 1], -1, 1))),
    "x-right_y-up_z-forward": lambda d: (np.arctan2(d[..., 0], d[..., 2]), np.arcsin(np.clip(-d[..., 1], -1, 1))),
    "x-right_y-down_z-backward": lambda d: (np.arctan2(d[..., 0], -d[..., 2]), np.arcsin(np.clip(d[..., 1], -1, 1))),
}


# Label groups for --labels: index = value written to <out>.labels.tif (0 = no label)
LABEL_GROUPS = {
    "road": ["Road", "Parking", "Bike Lane", "Service Lane", "Crosswalk - Plain", "Lane Marking - General",
             "Lane Marking - Crosswalk", "Rail Track", "Pothole", "Manhole", "Catch Basin"],
    "sidewalk": ["Sidewalk", "Pedestrian Area", "Curb Cut"],
    "curb": ["Curb"],
    "marking": [],  # filled below: lane markings are also listed under road; kept separate for line analysis
    "vehicle": ["Car", "Truck", "Bus", "Motorcycle", "Bicycle", "Caravan", "Trailer", "Other Vehicle", "On Rails",
                "Boat", "Wheeled Slow", "Car Mount"],
    "person": ["Person", "Bicyclist", "Motorcyclist", "Other Rider", "Ego Vehicle", "Bird", "Ground Animal"],
    "other": [],
}
# --wall-range: what counts as ground that may be used far from the camera
GROUND_FOR_RANGE = ("road", "sidewalk", "curb", "marking")
GROUND_NAMES_FOR_RANGE = ("Terrain", "Sand", "Snow", "Water")
LABEL_GROUPS["marking"] = ["Lane Marking - General", "Lane Marking - Crosswalk"]
LABEL_GROUPS["road"] = [n for n in LABEL_GROUPS["road"] if n not in LABEL_GROUPS["marking"]]


def label_lut(names_by_id: dict[int, str]) -> np.ndarray:
    """Vistas id -> group index (1-based in LABEL_GROUPS order); 255 (not covered) -> 0."""
    groups = list(LABEL_GROUPS)
    lut = np.full(256, groups.index("other") + 1, np.uint8)
    lut[255] = 0
    for vid, name in names_by_id.items():
        for g, members in LABEL_GROUPS.items():
            if name in members:
                lut[vid] = groups.index(g) + 1
    return lut


def load_mask(path: Path, width: int, height: int) -> np.ndarray:
    """ERP mask (grey, white = usable) at the ERP size; nearest-neighbour resize if the sizes differ."""
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise SystemExit(f"cannot read mask {path}")
    if mask.shape != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    return mask


def frame_mask(frame: str, mask_dir: Path | None, static: np.ndarray | None, width: int, height: int):
    """The ERP mask for one frame, and its source: 'frame' (DIR/<frame>.png), 'static' (--mask) or 'none'.

    The per-frame file is loaded only when this is called, so masks are not all held in memory.
    """
    if mask_dir is not None:
        path = mask_dir / (frame + ".png")
        if path.exists():
            return load_mask(path, width, height), "frame"
    if static is not None:
        return static, "static"
    return None, "none"


def project(conv: str, cam_xyz: np.ndarray, width: int, height: int):
    d = cam_xyz / np.linalg.norm(cam_xyz, axis=-1, keepdims=True)
    lon, lat = ERP_CONVENTIONS[conv](d)
    return (lon / (2 * math.pi) + 0.5) * width, (lat / math.pi + 0.5) * height


def pick_convention(images, points, width: int, height: int) -> tuple[str, float]:
    """Choose the ERP convention by reprojecting triangulated points onto their observations."""
    best = None
    for conv in ERP_CONVENTIONS:
        errs = []
        for rot, t, _, obs in list(images.values())[:10]:
            if not len(obs):
                continue
            world = np.array([points[int(i)] for i in obs[:, 2] if int(i) in points])
            keep = np.array([int(i) in points for i in obs[:, 2]])
            u, v = project(conv, world @ rot.T + t, width, height)
            du = np.abs(u - obs[keep, 0])
            du = np.minimum(du, width - du)
            errs.append(np.hypot(du, v - obs[keep, 1]))
        med = float(np.median(np.concatenate(errs)))
        if best is None or med < best[1]:
            best = (conv, med)
    return best


def level_rotation(images) -> tuple[np.ndarray, dict]:
    """Rotation that makes "up" the model Z axis, derived from the cameras themselves.

    GPS altitude is too noisy to level a model (it tilted the 238-frame set by 45 degrees). A helmet
    camera stays at constant height, so the camera centres span the ground plane; where the track is
    a single line, the plane is only constrained along the track and the mean camera down-axis
    supplies the rest.
    """
    centres = np.array([v[2] for v in images.values()])
    down = np.array([v[0].T @ np.array([0.0, 1.0, 0.0]) for v in images.values()]).mean(0)
    down /= np.linalg.norm(down)
    _, sv, vt = np.linalg.svd(centres - centres.mean(0))
    spread = sv / math.sqrt(len(centres))
    step = float(np.median(np.linalg.norm(np.diff(centres, axis=0), axis=1)))
    if spread[1] > 0.5 * step:  # relative to the frame spacing: models without GPS have arbitrary units
        up, method = vt[2], "camera-centre plane"
    else:
        up, method = -down - (-down @ vt[0]) * vt[0], "mean camera down-axis, orthogonal to track"
    up = up / np.linalg.norm(up)
    if up @ down > 0:
        up = -up
    z = np.array([0.0, 0.0, 1.0])
    v, c = np.cross(up, z), float(up @ z)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    rot = np.eye(3) + k + k @ k / (1 + c)
    info = {
        "method": method, "tilt_removed_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, c)))), 2),
        "up_vs_camera_down_axis_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, float(-up @ down))))), 2),
        "camera_height_spread_std_m": round(float(((centres - centres.mean(0)) @ up).std()), 3),
        "track_spread_m": [round(float(x), 2) for x in spread],
    }
    return rot, info


def exif_gps(image_dir: Path) -> dict[str, tuple[float, float]]:
    out = subprocess.check_output(["exiftool", "-j", "-n", "-GPSLatitude", "-GPSLongitude", str(image_dir)])
    return {
        Path(e["SourceFile"]).name: (e["GPSLatitude"], e["GPSLongitude"])
        for e in json.loads(out)
        if "GPSLatitude" in e and "GPSLongitude" in e
    }


def camera_height_in_model_units(images, points) -> tuple[float, dict]:
    """Distance from the cameras down to the ground in model units, from the sparse points.

    Expects a levelled model. Nothing lies below the ground, so among the points close to a camera
    the ground is the far end of the distribution of (camera z - point z); the 97th percentile is
    used to stay clear of the few points triangulated below ground. On the two GPS-scaled
    Admiralstrasse sets this gives 1.85 m and 1.93 m (R-093), so expect about 5 % scale uncertainty.
    """
    centres = np.array([v[2] for v in images.values()])
    pts = np.array(list(points.values()))
    step = float(np.median(np.linalg.norm(np.diff(centres, axis=0), axis=1)))
    dist, idx = cKDTree(centres[:, :2]).query(pts[:, :2])
    drop = centres[idx, 2] - pts[:, 2]
    near = (dist < 1.5 * step) & (drop > 0.05 * step)
    height = float(np.percentile(drop[near], 97))
    return height, {"points_used": int(near.sum()), "median_frame_step_model": round(step, 4),
                    "drop_percentiles_50_75_90_97": [round(float(x), 4) for x in np.percentile(drop[near], [50, 75, 90, 97])]}


def reference_xy(images, points, names, image_dir: Path, no_gps: bool, camera_height: float, crs_override: str | None = None):
    """Target XY for the camera centres: scene CRS from EXIF GPS, or local metres without GPS.

    The scene CRS is `crs_override` / env ORTHO_CRS, else the UTM zone at the median GPS position.

    Without GPS the model has no scale; it is set so that the cameras are `camera_height` above the
    ground found in the sparse points. Returns (names, target_xy, crs or None, info).
    """
    if not no_gps:
        gps = exif_gps(image_dir)
        names = sorted(n for n in names if n in gps)
        lat_med = float(np.median([gps[n][0] for n in names]))
        lon_med = float(np.median([gps[n][1] for n in names]))
        crs = resolve_crs(lon_med, lat_med, crs_override)
        to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        return names, np.array([to_utm.transform(gps[n][1], gps[n][0]) for n in names]), crs, {"georeference": "EXIF GPS", "crs": crs}
    h_model, info = camera_height_in_model_units(images, points)
    scale = camera_height / h_model
    names = sorted(names)
    info |= {"georeference": "none (local metres)", "scale_from_camera_height": round(scale, 5),
             "median_frame_step_m": round(info["median_frame_step_model"] * scale, 3)}
    return names, scale * np.array([images[n][2][:2] for n in names]), None, info


def fit_similarity_2d(src: np.ndarray, dst: np.ndarray):
    """Least-squares s, R (2x2), t with dst ~ s * R @ src + t."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    u, sv, vt = np.linalg.svd(b.T @ a / len(src))
    d = np.diag([1.0, np.sign(np.linalg.det(u @ vt))])
    rot = u @ d @ vt
    scale = np.trace(np.diag(sv) @ d) / a.var(0).sum()
    return scale, rot, md - scale * rot @ ms


class Dem(NamedTuple):
    """North-up elevation grid in the scene CRS: z[row, col] sits at (x0 + (col + 0.5) dx, y0 - (row + 0.5) dy); NaN = no data."""

    z: np.ndarray
    x0: float
    y0: float
    dx: float
    dy: float


def load_dem(path: Path) -> tuple[Dem, object]:
    """Read band 1 of a north-up GeoTIFF as a Dem (no-data -> NaN); also returns its CRS."""
    with rasterio.open(path) as src:
        t = src.transform
        if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
            sys.exit(f"--ground-dem must be a north-up GeoTIFF: {path}")
        z = src.read(1).astype(np.float32)
        if src.nodata is not None:
            z[z == np.float32(src.nodata)] = np.nan
        z[~np.isfinite(z) | (z < -1000)] = np.nan
        return Dem(z, t.c, t.f, t.a, -t.e), src.crs


def dem_sample(dem: Dem, xy: np.ndarray) -> np.ndarray:
    """Bilinear DEM height at scene-CRS points xy (..., 2); NaN outside the grid or next to no-data cells."""
    xy = np.asarray(xy, float)
    h, w = dem.z.shape
    fx = (xy[..., 0] - dem.x0) / dem.dx - 0.5
    fy = (dem.y0 - xy[..., 1]) / dem.dy - 0.5
    inside = (fx >= 0) & (fx <= w - 1) & (fy >= 0) & (fy <= h - 1)
    fx, fy = np.clip(fx, 0, w - 1), np.clip(fy, 0, h - 1)
    c0, r0 = np.minimum(fx.astype(int), w - 2), np.minimum(fy.astype(int), h - 2)
    tx, ty = fx - c0, fy - r0
    z = dem.z
    top = z[r0, c0] * (1 - tx) + z[r0, c0 + 1] * tx
    bottom = z[r0 + 1, c0] * (1 - tx) + z[r0 + 1, c0 + 1] * tx
    return np.where(inside, top * (1 - ty) + bottom * ty, np.nan)


def tilt_rotation(rx: float, ry: float) -> np.ndarray:
    """Rotation by the vector (rx, ry, 0) in radians: a tilt about a horizontal axis (Rodrigues)."""
    angle = math.hypot(rx, ry)
    if angle < 1e-14:
        return np.eye(3)
    k = np.array([rx, ry, 0.0]) / angle
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


def robust_offset(d: np.ndarray, ok: np.ndarray, max_rounds: int = 3):
    """Median offset of d over the cameras in `ok`, dropping those whose residual exceeds 3 x median (min 0.02 m).

    Same rule as the quadratic ground surface fit. Returns (offset, keep, residual = d - offset).
    """
    keep = ok.copy()
    for _ in range(max_rounds):
        off = float(np.median(d[keep]))
        res = d - off
        new = ok & (np.abs(res) < 3 * max(float(np.median(np.abs(res[keep]))), 0.02))
        if new.sum() < max(6, 0.3 * ok.sum()) or (new == keep).all():
            break
        keep = new
    off = float(np.median(d[keep]))
    return off, keep, d - off


def level_to_dem(centres: np.ndarray, utm: np.ndarray, dem: Dem, camera_height: float, shift=(0.0, 0.0),
                 fit_tilt: bool = True, max_iter: int = 12) -> dict:
    """Vertical relation between a model and a DEM from the camera centres.

    The cameras are `camera_height` above the ground, so their elevation is DEM(xy) + camera_height.
    `centres` are model centres (Z roughly up), `utm` their scene-CRS XY (GPS); the DEM is sampled at
    camera xy + shift. With fit_tilt the model is also rotated about horizontal axes until
    scale * Z + offset matches that elevation (model Z becomes truly vertical); the 2D similarity is
    refitted after every step because the tilt moves the XY. Cameras whose residual exceeds 3 x median
    (stops, stairs) are dropped from the fit. Without fit_tilt only the offset is fitted.

    Returns {tilt (3x3, applied to the model), centres (tilted), scale, rot2, trans2 (2D fit of the
    tilted centres), offset (metres: elevation = scale * Z + offset), keep, ok, residual (metres,
    target - fitted, NaN where the DEM has no data), cam_xy, z_m (scale * Z), info}.
    """
    shift = np.asarray(shift, float)
    tilt, c = np.eye(3), np.array(centres, float)
    for _ in range(max_iter):
        scale, rot2, trans2 = fit_similarity_2d(c[:, :2], utm)
        cam_xy = scale * c[:, :2] @ rot2.T + trans2
        target = dem_sample(dem, cam_xy + shift) + camera_height
        ok = np.isfinite(target)
        if ok.sum() < 6:
            sys.exit(f"--ground-dem covers only {int(ok.sum())} of {len(c)} camera positions")
        if not fit_tilt:
            break
        d = target - scale * c[:, 2]
        _, keep, _ = robust_offset(d, ok)
        rel = c - c[keep].mean(0)
        # z' = z + (r x rel)_z = z + rx * rel_y - ry * rel_x for a small rotation vector r = (rx, ry, 0)
        a = np.column_stack([np.ones(len(c)), scale * rel[:, 1], -scale * rel[:, 0]])
        sol = np.linalg.lstsq(a[keep], d[keep], rcond=None)[0]
        step = tilt_rotation(sol[1], sol[2])
        tilt, c = step @ tilt, c @ step.T
        if math.hypot(sol[1], sol[2]) < 1e-7:
            break
    scale, rot2, trans2 = fit_similarity_2d(c[:, :2], utm)
    cam_xy = scale * c[:, :2] @ rot2.T + trans2
    target = dem_sample(dem, cam_xy + shift) + camera_height
    ok = np.isfinite(target)
    offset, keep, res = robust_offset(target - scale * c[:, 2], ok)
    res = np.where(ok, res, np.nan)
    info = {
        "tilt_applied_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(tilt) - 1) / 2)))), 3),
        "offset_m": round(offset, 3),
        "residual_median_abs_m": round(float(np.median(np.abs(res[ok]))), 3),
        "residual_p90_abs_m": round(float(np.percentile(np.abs(res[ok]), 90)), 3),
        "cameras": int(len(c)), "cameras_in_dem": int(ok.sum()), "cameras_kept": int(keep.sum()),
    }
    return {"tilt": tilt, "centres": c, "scale": scale, "rot2": rot2, "trans2": trans2, "offset": offset,
            "keep": keep, "ok": ok, "residual": res, "cam_xy": cam_xy, "z_m": scale * c[:, 2], "info": info}


def dem_spread(dem: Dem, cam_xy: np.ndarray, z_m: np.ndarray, camera_height: float, shifts: np.ndarray,
               use: np.ndarray, fit_tilt: bool) -> np.ndarray:
    """MAD (metres) of camera elevation - (DEM(xy + shift) + camera_height) for every shift (S, 2).

    The elevation z_m is first adjusted by an offset (and with fit_tilt a plane, i.e. a small tilt about
    horizontal axes), the freedom the levelling has, so the spread measures how well the track's
    horizontal position matches the terrain. Only cameras in `use` that have DEM data at every shift count.
    """
    xy, z = cam_xy[use], z_m[use]
    shifts = np.atleast_2d(shifts)
    samples = np.concatenate([dem_sample(dem, xy[None] + shifts[i:i + 100, None]) for i in range(0, len(shifts), 100)])
    good = np.isfinite(samples).all(0)
    if good.sum() < 6:
        return np.full(len(shifts), np.nan)
    d = samples[:, good] + camera_height - z[good]
    rel = xy[good] - xy[good].mean(0)
    a = np.column_stack([np.ones(good.sum()), rel]) if fit_tilt else np.ones((good.sum(), 1))
    res = d - (d @ np.linalg.pinv(a).T) @ a.T
    return np.median(np.abs(res - np.median(res, axis=1, keepdims=True)), axis=1)


def find_dem_shift(dem: Dem, cam_xy: np.ndarray, z_m: np.ndarray, camera_height: float, use: np.ndarray,
                   fit_tilt: bool, radius: float = 6.0, step: float = 0.25) -> np.ndarray:
    """Horizontal shift (E, N) of the track against the DEM that minimises the elevation MAD (dem_spread).

    Grid search over +-radius in `step`, then two refinements (+-step in step/5, +-step/5 in step/25).
    """
    best = np.zeros(2)
    for r, s in ((radius, step), (step, step / 5), (step / 5, step / 25)):
        axis = np.linspace(-r, r, int(round(2 * r / s)) + 1)
        shifts = best + np.stack(np.meshgrid(axis, axis), -1).reshape(-1, 2)
        spread = dem_spread(dem, cam_xy, z_m, camera_height, shifts, use, fit_tilt)
        if not np.isfinite(spread).any():
            sys.exit("--dem-shift auto: too few cameras have DEM data at every shift of the search window")
        best = shifts[int(np.nanargmin(spread))]
    return best


def twoband(colour, dist, sigma: float) -> np.ndarray:
    """Two-band blend: fine detail from the nearest frame, coarse brightness from all kept frames.

    The nearest-frame mosaic is sharp but switches brightness at every frame border. Its low
    frequencies are replaced by those of a distance-weighted mean of the kept frames, so the borders
    disappear while edges and texture stay where the nearest frame put them. Blurs ignore empty
    cells (normalised convolution).
    """
    valid0 = np.isfinite(dist[0])
    w = np.where(np.isfinite(dist), 1.0 / np.maximum(dist, 0.5), 0).astype(np.float32)
    total = w.sum(0)
    mean = np.zeros(colour.shape[1:], np.float32)
    for k in range(colour.shape[0]):
        mean += colour[k].astype(np.float32) * w[k][..., None]
    mean /= np.maximum(total, 1e-6)[..., None]

    def blur(img, valid):
        weight = cv2.GaussianBlur(valid.astype(np.float32), (0, 0), sigma)
        out = np.empty_like(img)
        for c in range(3):
            out[..., c] = cv2.GaussianBlur(np.where(valid, img[..., c], 0), (0, 0), sigma) / np.maximum(weight, 1e-3)
        return out

    near = colour[0].astype(np.float32)
    result = near + blur(mean, total > 0) - blur(near, valid0)
    return np.where(valid0[..., None], np.clip(result, 0, 255), 0).astype(np.uint8)


def write_disagreement(output: Path, colour, dist, seen, texture, in_strip, crs, transform, gsd: float) -> dict:
    """How much the frames that see a ground cell disagree, as a map.

    For every cell seen by at least two of the kept frames: the spread (standard deviation across
    frames) of the luminance after removing structures larger than about 0.3 m. Brightness and
    exposure differences between frames are slow and drop out; what remains is fine texture that
    lands in different places, i.e. pose error, a wrong ground height, or objects above the ground.
    Writes <output>.disagreement.tif (RGBA heat map, for the viewer) and
    <output>.disagreement-data.tif (float: frames seeing the cell, high-pass spread, luminance spread).
    """
    layers, h, w = dist.shape
    hp = texture.astype(np.float32)
    lum_all = np.full((layers, h, w), np.nan, np.float32)
    for k in range(layers):
        valid = np.isfinite(dist[k])
        if valid.any():
            lum_all[k] = np.where(valid, cv2.cvtColor(colour[k], cv2.COLOR_BGR2GRAY).astype(np.float32), np.nan)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        n = np.isfinite(hp).sum(0)
        hp_std = np.where(n >= 2, np.nanstd(hp, axis=0), np.nan).astype(np.float32)
        lum_std = np.where(n >= 2, np.nanstd(lum_all, axis=0), np.nan).astype(np.float32)
    del hp, lum_all
    with rasterio.open(output.with_suffix(".disagreement-data.tif"), "w", driver="GTiff", width=w, height=h, count=3,
                       dtype="float32", crs=crs, transform=transform, compress="deflate", tiled=True, nodata=np.nan) as dst:
        dst.write(seen.astype(np.float32), 1)
        dst.write(hp_std, 2)
        dst.write(lum_std, 3)
    # heat map: 0 (frames agree) ... 25 grey levels of fine-texture spread and more (frames disagree)
    scaled = np.clip(np.nan_to_num(hp_std, nan=0) / 25 * 255, 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
    alpha = np.where(np.isfinite(hp_std), 255, 0).astype(np.uint8)
    with rasterio.open(output.with_suffix(".disagreement.tif"), "w", driver="GTiff", width=w, height=h, count=4,
                       dtype="uint8", crs=crs, transform=transform, compress="deflate", tiled=True, photometric="RGB") as dst:
        for band in range(3):
            dst.write(heat[..., 2 - band], band + 1)
        dst.write(alpha, 4)
    strip = np.zeros((h, w), bool)
    sub = in_strip
    strip[::10, ::10][: sub.shape[0], : sub.shape[1]] = sub
    sample = hp_std[::10, ::10][in_strip[: hp_std[::10, ::10].shape[0], : hp_std[::10, ::10].shape[1]]]
    sample = sample[np.isfinite(sample)]
    seen_strip = seen[::10, ::10][in_strip[: seen[::10, ::10].shape[0], : seen[::10, ::10].shape[1]]]
    return {
        "highpass_spread_p50_p75_p90": [round(float(x), 2) for x in np.percentile(sample, [50, 75, 90])] if sample.size else None,
        "share_of_strip_seen_twice_or_more": round(float((seen_strip >= 2).mean()), 4),
        "frames_per_cell_p50_p90": [int(x) for x in np.percentile(seen_strip[seen_strip > 0], [50, 90])] if (seen_strip > 0).any() else None,
        "scale": "grey levels (0-255) of luminance after removing structures larger than about 0.3 m",
    }


def parse_dem_shift(text: str):
    """--dem-shift value: 'auto', 'none' or 'DX,DY' (metres) -> 'auto' | 'none' | (dx, dy)."""
    if text in ("auto", "none"):
        return text
    try:
        dx, dy = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected auto, none or DX,DY, got {text!r}") from None
    return (dx, dy)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, required=True, help="COLMAP model directory containing txt/")
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True, help="GeoTIFF path; a .json report is written next to it")
    ap.add_argument("--mask", type=Path,
                    help="ERP mask, white = usable, black = helmet/rig; used for every frame without a --mask-dir file")
    ap.add_argument("--mask-dir", type=Path,
                    help="per-frame ERP masks DIR/<frame file name>.png (e.g. frame0904.jpg.png), white = usable; "
                         "a frame without its file uses --mask (counted and printed at the end)")
    ap.add_argument("--camera-height", type=float, default=2.0, help="metres above ground (helmet mount)")
    ap.add_argument("--gsd", type=float, default=0.02, help="output pixel size in metres")
    ap.add_argument("--strip", type=float, default=4.0, help="render within this distance of the track, metres")
    ap.add_argument("--max-range", type=float, default=6.0, help="a frame only contributes within this ground distance")
    ap.add_argument("--min-range", type=float, default=1.0, help="skip the area right below the camera (helmet, rider)")
    ap.add_argument("--blend", choices=["nearest", "median", "twoband"], default="nearest",
                    help="twoband: detail from the nearest frame, brightness from the distance-weighted mean of the kept frames")
    ap.add_argument("--median-range", type=float, default=None,
                    help="metres; in median mode only frames closer than this count (nearest frame always counts). "
                         "Far frames see the ground flat and coarse and blur the median (R-115)")
    ap.add_argument("--twoband-scale", type=float, default=0.5, help="metres; structures larger than this come from the mean")
    ap.add_argument("--median-k", type=int, default=5, help="frames per pixel kept for the median and the disagreement map")
    ap.add_argument("--gain-compensation", choices=["none", "global", "local"], default="none", nargs="?", const="global",
                    help="match each frame's colours to the multi-frame median before blending: one gain per frame "
                         "and channel (global) or a smooth gain field over the frame's footprint (local)")
    ap.add_argument("--gain-scale", type=float, default=0.6, help="metres; smoothness of the local gain field")
    ap.add_argument("--no-disagreement", action="store_true", help="skip the disagreement map")
    ap.add_argument("--write-source", action="store_true",
                    help="also write <output>.source.tif: index of the frame that the nearest-frame mosaic took each cell from")
    ap.add_argument("--ground-surface", choices=["per-camera", "quadratic", "plane"], default="per-camera",
                    help="per-camera: ground = camera height below the nearest cameras (inherits each pass's camera "
                         "height); plane/quadratic: one smooth surface fitted through all camera centres, shared by all passes")
    ap.add_argument("--ground-model", type=Path,
                    help=".npz ground height grid instead of a flat plane (made by earlier experiments that are not part of this repository)")
    ap.add_argument("--ground-dem", type=Path,
                    help="GeoTIFF terrain model in the scene CRS (orthometric heights, e.g. IGN LiDAR HD MNT): ground height "
                         "from the DEM, vertical offset from the cameras (DEM at the camera + --camera-height)")
    ap.add_argument("--level", choices=["auto", "dem"], default="auto",
                    help="auto: level from the camera-centre plane (flat tracks); dem: after the 2D fit, tilt the model about "
                         "horizontal axes until the camera centres match DEM + camera height (sloping tracks; needs --ground-dem)")
    ap.add_argument("--dem-shift", type=parse_dem_shift, default="none", metavar="auto|none|DX,DY",
                    help="horizontal shift (E,N metres) of the track against the DEM: the DEM is sampled at cell + shift. "
                         "auto searches +-6 m (0.25 m steps, then refines) for the minimum MAD of camera elevation - DEM; "
                         "the output georeference does not change")
    ap.add_argument("--labels", type=Path,
                    help="folder of per-frame label ERPs (<stem>.png, Mapillary Vistas ids, from segment/segment_frames.py); "
                         "writes <out>.labels.tif (class group of the nearest frame and a distance-weighted vote)")
    ap.add_argument("--exclude-groups", nargs="*", default=[], choices=list(LABEL_GROUPS),
                    help="treat pixels of these label groups as unusable, e.g. vehicle person")
    ap.add_argument("--wall-range", type=float, metavar="METRES",
                    help="with --labels: pixels not labelled as ground (GROUND_FOR_RANGE) are used only within this "
                         "distance, so walls and hedges do not stretch into long rays while open ground still fills "
                         "gaps up to --max-range")
    ap.add_argument("--raise-groups", nargs="*", default=[], metavar="GROUP=METRES",
                    help="lift the ground surface where the label vote of --raise-from is this group, e.g. sidewalk=0.12 curb=0.12")
    ap.add_argument("--raise-from", type=Path, help="<out>.labels.tif of an earlier run on the same grid")
    ap.add_argument("--line-tiles", type=float, default=0.0, metavar="METRES",
                    help="along kerbs and lane markings (label vote of --line-labels, curb/marking, +-0.2 m) take one "
                         "frame per tile of this size instead of the nearest frame per cell, so lines are not cut by seams")
    ap.add_argument("--line-labels", type=Path, help="<out>.labels.tif of an earlier run on the same grid")
    ap.add_argument("--object-regions", type=Path,
                    help="mask raster (255 = object, any grid): each connected object takes its colour from one frame")
    ap.add_argument("--export-patches", type=Path,
                    help="write each frame's ground patch (colour, valid mask, distance) to this folder and stop")
    ap.add_argument("--frame-offsets", type=Path,
                    help="JSON {frame: [east_m, north_m]}: frame i is sampled at cell + offset (from coregister_frames.py)")
    ap.add_argument("--write-corrected-model", type=Path,
                    help="with --frame-offsets (shift only): write a COLMAP text model whose camera centres are moved by "
                         "-offset horizontally, so other tools (splat, MVS) get the co-registration; then stop")
    ap.add_argument("--print-extent", action="store_true", help="print the output extent (E0 N0 E1 N1) and stop")
    ap.add_argument("--max-reproj", type=float, default=5.0, help="refuse models whose median reprojection exceeds this (px)")
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("E0", "N0", "E1", "N1"), help="render only this window")
    ap.add_argument("--crs", help="scene CRS, e.g. EPSG:25832 (default: env ORTHO_CRS, else the UTM zone at the median GPS position)")
    ap.add_argument("--no-gps", action="store_true",
                    help="frames have no GPS: scale from camera height, output in local metres without CRS")
    args = ap.parse_args()
    if args.level == "dem" and not args.ground_dem:
        sys.exit("--level dem needs --ground-dem")
    if args.dem_shift != "none" and not args.ground_dem:
        sys.exit("--dem-shift needs --ground-dem")
    if args.ground_dem and args.no_gps:
        sys.exit("--ground-dem needs GPS: the DEM is in the scene CRS (drop --no-gps)")
    if args.ground_dem and (args.ground_model or args.ground_surface != "per-camera"):
        sys.exit("--ground-dem replaces --ground-model and --ground-surface; use only one ground source")
    if args.level == "dem" and (args.ground_model or args.ground_surface != "per-camera"):
        sys.exit("--level dem changes the levelling, which --ground-model (built with another levelling) and "
                 "--ground-surface (fitted through the track) do not follow; refusing")

    images, points = read_model(args.model / "txt")
    cam_line = [l for l in (args.model / "txt" / "cameras.txt").read_text().splitlines() if not l.startswith("#")][0].split()
    if cam_line[1] != "EQUIRECTANGULAR":
        sys.exit(f"expected an EQUIRECTANGULAR camera, got {cam_line[1]}")
    width, height = int(cam_line[2]), int(cam_line[3])
    conv, reproj = pick_convention(images, points, width, height)
    if reproj > args.max_reproj:
        sys.exit(f"no ERP convention reproduces the observations (best {conv}: {reproj:.1f} px)")
    print(f"ERP convention {conv}, median reprojection {reproj:.2f} px")

    level, level_info = level_rotation(images)
    images = {n: (r @ level.T, t, level @ c, o) for n, (r, t, c, o) in images.items()}
    print(f"levelled by {level_info['method']}: removed {level_info['tilt_removed_deg']} deg tilt, "
          f"camera height spread {level_info['camera_height_spread_std_m']} m")

    points = {k: level @ v for k, v in points.items()}
    names, utm, crs, ref_info = reference_xy(images, points, list(images), args.images, args.no_gps, args.camera_height, args.crs)
    centres = np.array([images[n][2] for n in names])
    print(ref_info)
    scale, rot2, trans2 = fit_similarity_2d(centres[:, :2], utm)
    resid = np.linalg.norm((scale * centres[:, :2] @ rot2.T + trans2) - utm, axis=1)
    print(f"model -> target XY: scale {scale:.4f}, rotation {math.degrees(math.atan2(rot2[1, 0], rot2[0, 0])):.2f} deg, "
          f"GPS residual mean {resid.mean():.2f} m, max {resid.max():.2f} m")

    dem = dem_offset = dem_report = None
    dem_shift_xy = np.zeros(2)
    if args.ground_dem:
        dem, dem_crs = load_dem(args.ground_dem)
        if dem_crs is None or rasterio.crs.CRS.from_user_input(crs) != dem_crs:
            sys.exit(f"--ground-dem CRS ({dem_crs}) differs from the scene CRS ({crs}); reproject the DEM first")
        fit_tilt = args.level == "dem"
        auto_shift = args.dem_shift == "auto"
        shift = np.array(args.dem_shift, float) if isinstance(args.dem_shift, tuple) else np.zeros(2)
        lev0 = level_to_dem(centres, utm, dem, args.camera_height, np.zeros(2), fit_tilt)  # before any shift
        lev = lev0 if not shift.any() else level_to_dem(centres, utm, dem, args.camera_height, shift, fit_tilt)
        if auto_shift:  # the tilt depends on the shift and the shift on the tilt: alternate until it settles
            for _ in range(5):
                found = find_dem_shift(dem, lev["cam_xy"], lev["z_m"], args.camera_height, lev["keep"], fit_tilt)
                if np.linalg.norm(found - shift) < 0.03:
                    break
                shift = found
                lev = level_to_dem(centres, utm, dem, args.camera_height, shift, fit_tilt)
        mad = lambda l, sh: float(dem_spread(dem, l["cam_xy"], l["z_m"], args.camera_height, np.array([sh]), l["keep"], fit_tilt)[0])
        mad_before, mad_after = mad(lev0, np.zeros(2)), mad(lev, shift)
        above = lambda l, sh: l["z_m"] + l["offset"] - dem_sample(dem, l["cam_xy"] + sh)
        stats = lambda v: dict(zip(("p10", "median", "p90"), (round(float(x), 3) for x in np.nanpercentile(v, [10, 50, 90]))))
        if fit_tilt:
            tilt = lev["tilt"]
            level = tilt @ level
            images = {n: (r @ tilt.T, t, tilt @ c, o) for n, (r, t, c, o) in images.items()}
            points = {k: tilt @ v for k, v in points.items()}
            centres = np.array([images[n][2] for n in names])
            level_info = level_info | {"dem_tilt_applied_deg": lev["info"]["tilt_applied_deg"]}
        scale, rot2, trans2 = lev["scale"], lev["rot2"], lev["trans2"]
        resid = np.linalg.norm((scale * centres[:, :2] @ rot2.T + trans2) - utm, axis=1)
        dem_offset, dem_shift_xy = lev["offset"], shift
        li = lev["info"]
        print(f"DEM levelling ({args.level}): tilt applied {li['tilt_applied_deg']} deg, elevation offset {li['offset_m']} m, "
              f"camera elevation - (DEM + {args.camera_height} m) residual median {li['residual_median_abs_m']} m, "
              f"p90 {li['residual_p90_abs_m']} m, kept {li['cameras_kept']} of {li['cameras_in_dem']} cameras with DEM data "
              f"({li['cameras']} total); model -> target XY: scale {scale:.4f}, GPS residual mean {resid.mean():.2f} m, "
              f"max {resid.max():.2f} m")
        print(f"DEM shift ({'auto' if auto_shift else 'fixed' if shift.any() else 'none'}): E {shift[0]:+.2f} m, N {shift[1]:+.2f} m, "
              f"elevation MAD {mad_before:.3f} m -> {mad_after:.3f} m")
        before_stats, after_stats = stats(above(lev0, np.zeros(2))), stats(above(lev, shift))
        print(f"camera height above DEM before shift: p10 {before_stats['p10']} median {before_stats['median']} p90 {before_stats['p90']} m")
        if args.level == "auto" and li["residual_p90_abs_m"] > 0.5:
            print("warning: the cameras do not follow the DEM (+ camera height) with --level auto; "
                  "a sloping track is levelled wrongly, try --level dem")
        dem_report = {
            "path": str(args.ground_dem), "crs": crs, "cell_m": [dem.dx, dem.dy], "level": args.level,
            "offset_m": round(lev["offset"], 4),
            "level_residuals": {k: li[k] for k in ("tilt_applied_deg", "residual_median_abs_m", "residual_p90_abs_m",
                                                   "cameras", "cameras_in_dem", "cameras_kept")},
            "shift": {"mode": "auto" if auto_shift else "fixed" if shift.any() else "none",
                      "east_m": round(float(shift[0]), 3), "north_m": round(float(shift[1]), 3),
                      "elevation_mad_before_m": round(mad_before, 4), "elevation_mad_after_m": round(mad_after, 4)},
            "camera_height_above_dem_m": {"before_shift": before_stats, "after_shift": after_stats},
        }

    cam_utm = scale * centres[:, :2] @ rot2.T + trans2
    ground_z = centres[:, 2] - args.camera_height / scale  # model units
    if args.write_corrected_model:
        # sampling cell p+s from camera c equals sampling p from camera c-s (horizontal), so move each centre by -s
        offs = json.loads(args.frame_offsets.read_text())
        src = args.model / "txt"
        dst = args.write_corrected_model / "txt"
        dst.mkdir(parents=True, exist_ok=True)
        out_lines, moved, k = [], [], 0
        for line in (src / "images.txt").read_text().splitlines():
            if line.startswith("#"):
                out_lines.append(line)
                continue
            if k % 2 == 0:
                p = line.split()
                name = p[9]
                if name in offs and len(offs[name]) == 2:
                    r_orig = quat_to_rot(*map(float, p[1:5]))
                    c_orig = -r_orig.T @ np.array(list(map(float, p[5:8])))
                    d_lev = np.r_[np.asarray(offs[name]) @ rot2 / scale, 0.0]
                    c_new = level.T @ (level @ c_orig - d_lev)
                    t_new = -r_orig @ c_new
                    p[5:8] = [f"{v:.9f}" for v in t_new]
                    moved.append(float(np.hypot(*offs[name])))
                    line = " ".join(p)
            out_lines.append(line)
            k += 1
        (dst / "images.txt").write_text("\n".join(out_lines) + "\n")
        for f in ("cameras.txt", "points3D.txt"):
            (dst / f).write_text((src / f).read_text())
        print(json.dumps({"corrected_model": str(dst), "frames_moved": len(moved),
                          "shift_m_p50_max": [round(float(np.median(moved)), 4), round(float(max(moved)), 4)] if moved else None}))
        return 0
    e0, n0 = cam_utm.min(0) - args.strip
    e1, n1 = cam_utm.max(0) + args.strip
    if args.print_extent:
        print(f"CRS {crs}")
        print(f"EXTENT {e0:.3f} {n0:.3f} {e1:.3f} {n1:.3f}")
        return 0
    if args.bbox:  # experiments on a crop: render only this window (scene CRS)
        e0, n0 = max(e0, args.bbox[0]), max(n0, args.bbox[1])
        e1, n1 = min(e1, args.bbox[2]), min(n1, args.bbox[3])
    w_px, h_px = int(math.ceil((e1 - e0) / args.gsd)), int(math.ceil((n1 - n0) / args.gsd))
    print(f"output {w_px} x {h_px} px at {args.gsd} m ({(e1 - e0):.0f} x {(n1 - n0):.0f} m)")

    layers = args.median_k
    if args.mask_dir is not None and not args.mask_dir.is_dir():
        sys.exit(f"--mask-dir is not a folder: {args.mask_dir}")
    mask = load_mask(args.mask, width, height) if args.mask is not None else None
    mask_sources: dict[str, str] = {}  # frame name -> 'frame' | 'static' | 'none', set when the frame is rendered

    def mask_info():
        """Per-frame mask counts over the frames rendered so far; a frame counts once however many passes see it."""
        if args.mask_dir is None:
            return None
        counts = Counter(mask_sources.values())
        info = {"mask_dir": str(args.mask_dir), "static_mask": str(args.mask) if args.mask is not None else None,
                "frames": len(mask_sources), "frames_with_own_mask": counts["frame"],
                "fallback_frames": counts["static"] + counts["none"]}
        fallback = "--mask" if args.mask is not None else "no mask"
        print(f"per-frame masks: {info['frames_with_own_mask']} of {info['frames']} frames used {args.mask_dir}; "
              f"{info['fallback_frames']} fell back to {fallback}")
        return info

    lut = None
    if args.labels:
        lut = label_lut({int(k): v for k, v in json.loads((args.labels / "labels.json").read_text()).items()})
    excluded = [list(LABEL_GROUPS).index(g) + 1 for g in args.exclude_groups]
    if excluded and lut is None:
        sys.exit("--exclude-groups needs --labels")
    ground_lut = None  # Vistas id -> may be used beyond --wall-range
    if args.wall_range is not None:
        if lut is None:
            sys.exit("--wall-range needs --labels")
        names_by_id = {int(k): v for k, v in json.loads((args.labels / "labels.json").read_text()).items()}
        ground_ids = [list(LABEL_GROUPS).index(g) + 1 for g in GROUND_FOR_RANGE]
        ground_lut = np.isin(lut, ground_ids)
        for vid, name in names_by_id.items():
            if name in GROUND_NAMES_FOR_RANGE:
                ground_lut[vid] = True
    missing_labels = []

    offsets = None
    if args.frame_offsets:
        offsets = {k: np.asarray(v, float) for k, v in json.loads(args.frame_offsets.read_text()).items()}
    lift = None
    if args.raise_groups:
        with rasterio.open(args.raise_from) as src:
            if (src.width, src.height) != (w_px, h_px) or abs(src.transform.c - e0) > 1e-6 or abs(src.transform.f - n1) > 1e-6:
                sys.exit("--raise-from is on a different grid")
            vote = src.read(2)
        lift = np.zeros((h_px, w_px), np.float32)
        for item in args.raise_groups:
            g, metres = item.split("=")
            lift[vote == list(LABEL_GROUPS).index(g) + 1] = float(metres)
        # close single-cell holes in the step so the edge follows the kerb, not label noise
        lift = cv2.medianBlur(lift, 5)

    tree = cKDTree(cam_utm)
    inv_rot2 = rot2.T / scale
    surface = None
    if args.ground_surface != "per-camera":
        cx_, cy_ = centres[:, 0] - centres[:, 0].mean(), centres[:, 1] - centres[:, 1].mean()
        terms = [np.ones_like(cx_), cx_, cy_] + ([cx_ * cx_, cx_ * cy_, cy_ * cy_] if args.ground_surface == "quadratic" else [])
        A = np.stack(terms, 1)
        coef = np.linalg.lstsq(A, centres[:, 2], rcond=None)[0]
        for _ in range(3):  # robust: drop cameras far off the surface (stops, curbs)
            res = centres[:, 2] - A @ coef
            keep = np.abs(res) < 3 * max(np.median(np.abs(res)), 1e-6)
            coef = np.linalg.lstsq(A[keep], centres[keep, 2], rcond=None)[0]
        mean_xy = centres[:, :2].mean(0)
        surface = (coef, mean_xy)
        print(f"ground surface {args.ground_surface}: camera residual std {float(np.std(centres[:, 2] - A @ coef)) * scale:.3f} m")
    ground_grid = None
    if args.ground_model:
        g = np.load(args.ground_model)
        if not np.allclose(g["level"], level, atol=1e-6):
            sys.exit("ground model was built with a different levelling")
        ground_grid = (float(g["x0"]), float(g["y0"]), float(g["cell"]), g["z"])
    r_px = int(math.ceil(args.max_range / args.gsd))

    def frame_patches():
        """Yield (index, window, patch, valid, distance) per frame: the frame's pixels on the ground grid."""
        for i, name in enumerate(names):
            rot, t, _, _ = images[name]
            cx = int((cam_utm[i, 0] - e0) / args.gsd)
            cy = int((n1 - cam_utm[i, 1]) / args.gsd)
            x0, x1 = max(cx - r_px, 0), min(cx + r_px + 1, w_px)
            y0, y1 = max(cy - r_px, 0), min(cy + r_px + 1, h_px)
            if x0 >= x1 or y0 >= y1:
                continue
            east = e0 + (np.arange(x0, x1) + 0.5) * args.gsd
            north = n1 - (np.arange(y0, y1) + 0.5) * args.gsd
            ee, nn = np.meshgrid(east, north)
            flat = np.stack([ee.ravel(), nn.ravel()], 1)
            d_cam = np.hypot(flat[:, 0] - cam_utm[i, 0], flat[:, 1] - cam_utm[i, 1])
            d_track, nearest = tree.query(flat, k=min(3, len(names)))
            ok = (d_cam <= args.max_range) & (d_cam >= args.min_range) & (d_track[:, 0] <= args.strip)
            if not ok.any():
                continue
            # ground height: inverse-distance mean over the three nearest cameras
            if offsets is not None and name in offsets:  # sample where this frame shows the cell (co-registration)
                o = offsets[name]
                if len(o) == 3:  # shift plus small rotation about the camera
                    rel = flat - cam_utm[i]
                    flat = flat + o[:2] + o[2] * np.column_stack([-rel[:, 1], rel[:, 0]])
                else:
                    flat = flat + o
            model_xy = (flat - trans2) @ inv_rot2.T
            if dem is not None:  # DEM (+ shift) at the cell where this frame shows it, in levelled model units
                g = dem_sample(dem, flat + dem_shift_xy)
                has_dem = np.isfinite(g)
                gz = (np.where(has_dem, g, dem_offset) - dem_offset) / scale
                ok &= has_dem
            elif surface is not None:
                coef, mxy = surface
                dx_, dy_ = model_xy[:, 0] - mxy[0], model_xy[:, 1] - mxy[1]
                terms = [np.ones_like(dx_), dx_, dy_] + ([dx_ * dx_, dx_ * dy_, dy_ * dy_] if len(coef) == 6 else [])
                gz = np.stack(terms, 1) @ coef - args.camera_height / scale
            elif ground_grid is None:
                wgt = 1.0 / np.maximum(d_track, 0.05)
                gz = (ground_z[nearest] * wgt).sum(1) / wgt.sum(1)
            else:
                gx0, gy0, gcell, gzz = ground_grid
                fx = (model_xy[:, 0] - gx0) / gcell - 0.5
                fy = (model_xy[:, 1] - gy0) / gcell - 0.5
                from scipy.ndimage import map_coordinates
                gz = map_coordinates(gzz, [fy, fx], order=1, mode="nearest")
            if lift is not None:
                gz = gz + lift[y0:y1, x0:x1].ravel() / scale
            world = np.column_stack([model_xy, gz])
            u, v = project(conv, world @ rot.T + t, width, height)
            img = cv2.imread(str(args.images / name), cv2.IMREAD_COLOR)
            shape = (y1 - y0, x1 - x0)
            mu = (u % width).astype(np.float32).reshape(shape)
            mv = np.clip(v, 0, height - 1).astype(np.float32).reshape(shape)
            patch = cv2.remap(img, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
            if mask is not None or args.mask_dir is not None:
                fmask, mask_sources[name] = frame_mask(name, args.mask_dir, mask, width, height)
                if fmask is not None:
                    ok &= cv2.remap(fmask, mu, mv, cv2.INTER_NEAREST, borderMode=cv2.BORDER_WRAP).ravel() > 127
            frame_patches.label = None
            if lut is not None:
                lpath = args.labels / (Path(name).stem + ".png")
                if not lpath.exists():
                    missing_labels.append(name)
                if lpath.exists():
                    lab = cv2.imread(str(lpath), cv2.IMREAD_GRAYSCALE)
                    sx, sy = lab.shape[1] / width, lab.shape[0] / height
                    raw = cv2.remap(lab, mu * sx, mv * sy, cv2.INTER_NEAREST, borderMode=cv2.BORDER_WRAP)
                    lg = lut[raw]
                    frame_patches.label = lg
                    if excluded:
                        ok &= ~np.isin(lg, excluded).ravel()
                    if ground_lut is not None:
                        ok &= ground_lut[raw].ravel() | (d_cam <= args.wall_range)
            ok = ok.reshape(shape)
            yield i, (slice(y0, y1), slice(x0, x1)), patch, ok, d_cam.reshape(shape)

    def accumulate(gains=None, want_texture=False):
        """Keep, per ground cell, the `layers` nearest frames' colours, sorted by distance."""
        colour = np.zeros((layers, h_px, w_px, 3), np.uint8)
        dist = np.full((layers, h_px, w_px), np.inf, np.float32)
        # fine texture of each frame (luminance minus its own blur), kept per layer for the
        # disagreement map; computed per frame so that mosaic seams do not count as texture
        texture = np.full((layers, h_px, w_px), np.nan, np.float16) if want_texture else None
        seen = np.zeros((h_px, w_px), np.uint16)
        winner = np.full((h_px, w_px), -1, np.int16)
        fid = np.full((layers, h_px, w_px), -1, np.int16)  # frame index per layer
        sigma = max(0.3 / args.gsd / 3, 1.0)
        for i, win, patch, ok, d_cam in frame_patches():
            if local_ref is not None:
                ref, rvalid = local_ref
                sig = max(args.gain_scale / args.gsd / 2, 1.0)
                both = (ok & rvalid[win]).astype(np.float32)
                pf = patch.astype(np.float32)
                wsum = np.maximum(cv2.GaussianBlur(both, (0, 0), sig), 1e-3)
                field = np.empty_like(pf)
                for c in range(3):
                    num = cv2.GaussianBlur(ref[win][..., c] * both, (0, 0), sig) / wsum
                    den = cv2.GaussianBlur(pf[..., c] * both, (0, 0), sig) / wsum
                    field[..., c] = np.where(cv2.GaussianBlur(both, (0, 0), sig) > 0.2, num / np.maximum(den, 1.0), gains[i][c])
                patch = np.clip(pf * np.clip(field, 0.5, 2.0), 0, 255).astype(np.uint8)
            elif gains is not None:
                patch = np.clip(patch.astype(np.float32) * gains[i], 0, 255).astype(np.uint8)
            seen[win] += ok
            d = np.where(ok, d_cam, np.inf).astype(np.float32)
            tex = None
            if want_texture:
                lum = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32)
                weight = cv2.GaussianBlur(ok.astype(np.float32), (0, 0), sigma)
                low = cv2.GaussianBlur(np.where(ok, lum, 0), (0, 0), sigma) / np.maximum(weight, 1e-3)
                tex = np.where(ok & (weight > 0.5), lum - low, np.nan).astype(np.float16)
            f_new = np.full(d.shape, i, np.int16)
            for layer in range(layers):
                cur_d = dist[(layer, *win)]
                cur_c = colour[(layer, *win)]
                cur_f = fid[(layer, *win)]
                closer = d < cur_d
                if layer == 0:
                    winner[win][closer] = i
                new_d, new_c = np.where(closer, cur_d, d), np.where(closer[..., None], cur_c, patch)
                new_f = np.where(closer, cur_f, f_new)
                cur_d[closer] = d[closer]
                cur_c[closer] = patch[closer]
                cur_f[closer] = f_new[closer]
                f_new = new_f
                if tex is not None:
                    cur_t = texture[(layer, *win)]
                    new_t = np.where(closer, cur_t, tex)
                    cur_t[closer] = tex[closer]
                    tex = new_t
                d, patch = new_d, new_c
            if (i + 1) % 20 == 0 or i + 1 == len(names):
                print(f"  {i + 1}/{len(names)} frames", flush=True)
        accumulate.winner = winner
        accumulate.fid = fid
        return colour, dist, seen, texture

    def median_of(colour, dist, max_range=None):
        import warnings
        out = np.zeros((h_px, w_px, 3), np.uint8)
        for r0 in range(0, h_px, 256):  # row blocks keep the float copy small
            rows = slice(r0, r0 + 256)
            use = np.isfinite(dist[:, rows])
            if max_range is not None:
                use &= (dist[:, rows] <= max_range) | (np.arange(dist.shape[0]) == 0)[:, None, None]
            stack = np.where(use[..., None], colour[:, rows].astype(np.float32), np.nan)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                out[rows] = np.nan_to_num(np.nanmedian(stack, axis=0)).astype(np.uint8)
        return out

    def line_tiles(out, colour, dist, fid, winner):
        """One frame per tile on line cells (kerb, marking): fewer seams across the line."""
        with rasterio.open(args.line_labels) as src:
            if (src.width, src.height) != (w_px, h_px) or abs(src.transform.c - e0) > 1e-6 or abs(src.transform.f - n1) > 1e-6:
                sys.exit("--line-labels is on a different grid")
            vote = src.read(2)
        groups = list(LABEL_GROUPS)
        core = np.isin(vote, [groups.index("curb") + 1, groups.index("marking") + 1]).astype(np.uint8)
        r = max(int(round(0.2 / args.gsd)), 1)
        line = cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))) > 0
        src_before = winner.copy()
        src_after = winner.copy()
        out = out.copy()
        t = max(int(round(args.line_tiles / args.gsd)), 1)
        changed = 0
        for y0 in range(0, h_px, t):
            for x0 in range(0, w_px, t):
                sl = (slice(y0, y0 + t), slice(x0, x0 + t))
                m = line[sl]
                if m.sum() < 50:
                    continue
                f = fid[:, sl[0], sl[1]][:, m]          # layers x cells
                dd = dist[:, sl[0], sl[1]][:, m]
                cands = np.unique(f[f >= 0])
                if cands.size == 0:
                    continue
                best, best_key = None, None
                for c in cands:
                    hit = f == c
                    cover = hit.any(0).sum()
                    md = float(dd[hit].mean())
                    key = (cover, -md)
                    if best_key is None or key > best_key:
                        best, best_key = c, key
                hit = f == best
                has = hit.any(0)
                layer = hit.argmax(0)
                ys, xs = np.nonzero(m)
                ys, xs = ys[has] + y0, xs[has] + x0
                out[ys, xs] = colour[layer[has], ys, xs]
                changed += int((src_after[ys, xs] != best).sum())
                src_after[ys, xs] = best
        accumulate.winner = src_after

        def seam_m(src):
            both_h = line[:, 1:] & line[:, :-1] & (src[:, 1:] >= 0) & (src[:, :-1] >= 0)
            both_v = line[1:] & line[:-1] & (src[1:] >= 0) & (src[:-1] >= 0)
            n = ((src[:, 1:] != src[:, :-1]) & both_h).sum() + ((src[1:] != src[:-1]) & both_v).sum()
            return round(float(n * args.gsd), 1)
        info = {"tile_m": args.line_tiles, "line_area_m2": round(float(line.sum() * args.gsd ** 2), 1),
                "cells_changed_share": round(changed / max(int(line.sum()), 1), 4),
                "seam_length_inside_line_zone_m_before_after": [seam_m(src_before), seam_m(src_after)]}
        print("line tiles", info)
        return out, info

    def object_frames(out, colour, dist, fid, winner):
        """One frame per connected object region: no patchwork of frames on a car."""
        from rasterio.warp import Resampling, reproject
        mask = np.zeros((h_px, w_px), np.uint8)
        with rasterio.open(args.object_regions) as src:
            reproject(src.read(1), mask, src_transform=src.transform, src_crs=src.crs,
                      dst_transform=from_origin(e0, n1, args.gsd, args.gsd), dst_crs=crs, resampling=Resampling.nearest)
        n, lab, cstats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
        out = out.copy()
        src_after = winner.copy()
        full, changed, cells = 0, 0, 0
        for r in range(1, n):
            bx, by, bw, bh = cstats[r, :4]
            ys, xs = np.nonzero(lab[by:by + bh, bx:bx + bw] == r)
            ys, xs = ys + by, xs + bx
            f = fid[:, ys, xs]
            dd = dist[:, ys, xs]
            cands = np.unique(f[f >= 0])
            if cands.size == 0:
                continue
            best, best_key = None, None
            for c in cands:
                hit = f == c
                key = (int(hit.any(0).sum()), -float(dd[hit].mean()))
                if best_key is None or key > best_key:
                    best, best_key = c, key
            hit = f == best
            has = hit.any(0)
            layer = hit.argmax(0)
            out[ys[has], xs[has]] = colour[layer[has], ys[has], xs[has]]
            changed += int((src_after[ys[has], xs[has]] != best).sum())
            src_after[ys[has], xs[has]] = best
            cells += len(ys)
            full += int(has.all())
        accumulate.winner = src_after
        info = {"objects": n - 1, "objects_from_one_frame_completely": full,
                "object_area_m2": round(cells * args.gsd ** 2, 1), "cells_changed_share": round(changed / max(cells, 1), 4)}
        print("object frames", info)
        return out, info

    if args.export_patches:
        args.export_patches.mkdir(parents=True, exist_ok=True)
        index = {"gsd": args.gsd, "e0": e0, "n1": n1, "w": w_px, "h": h_px, "frames": {}}
        for i, win, patch, ok, d_cam in frame_patches():
            name = names[i]
            np.savez_compressed(args.export_patches / f"{Path(name).stem}.npz", patch=patch, ok=ok,
                                d=np.clip(d_cam * 10, 0, 255).astype(np.uint8))
            index["frames"][name] = {"y0": win[0].start, "x0": win[1].start, "cam": [float(v) for v in cam_utm[i]]}
        (args.export_patches / "index.json").write_text(json.dumps(index))
        print(f"exported {len(index['frames'])} patches")
        mask_info()
        return 0

    gains = None
    gain_info = None
    local_ref = None
    if args.gain_compensation != "none":
        # Pass 1: plain median as reference. Pass 2: per-frame, per-channel gain = median ratio
        # reference / frame over the frame's valid cells. Pass 3 (below): blend with the gains.
        print("gain compensation: reference pass")
        colour, dist, _, _ = accumulate()
        reference = median_of(colour, dist).astype(np.float32)
        ref_valid = np.isfinite(dist).sum(0) >= 2
        del colour, dist
        gains = np.ones((len(names), 3), np.float32)
        if args.gain_compensation == "local":
            local_ref = (reference, ref_valid)
        for i, win, patch, ok, _ in frame_patches():
            sel = ok & ref_valid[win] & (patch.min(2) > 12) & (patch.max(2) < 245)
            if sel.sum() > 500:
                ratio = reference[win][sel] / np.maximum(patch[sel].astype(np.float32), 1)
                gains[i] = np.clip(np.median(ratio, axis=0), 0.5, 2.0)
        gain_info = {"mode": args.gain_compensation, "frames": len(names), "gain_p05_p50_p95": [round(float(x), 3) for x in np.percentile(gains.mean(1), [5, 50, 95])]}
        print("gains", gain_info)

    colour, dist, seen, texture = accumulate(gains, want_texture=not args.no_disagreement)
    count = np.isfinite(dist).sum(0)
    if args.blend == "nearest":
        out = colour[0]
    elif args.blend == "median":
        out = median_of(colour, dist, args.median_range)
    else:
        out = twoband(colour, dist, max(args.twoband_scale / args.gsd / 2, 1.0))
    alpha = np.where(count > 0, 255, 0).astype(np.uint8)
    line_info = None
    if args.line_tiles > 0:
        out, line_info = line_tiles(out, colour, dist, accumulate.fid, accumulate.winner)
    object_info = None
    if args.object_regions:
        out, object_info = object_frames(out, colour, dist, accumulate.fid, accumulate.winner)

    in_strip = tree.query(
        np.stack(np.meshgrid(e0 + (np.arange(0, w_px, 10) + 0.5) * args.gsd, n1 - (np.arange(0, h_px, 10) + 0.5) * args.gsd), -1).reshape(-1, 2)
    )[0].reshape(len(range(0, h_px, 10)), -1) <= args.strip
    coverage = float((alpha[::10, ::10] > 0)[in_strip].mean())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        args.output, "w", driver="GTiff", width=w_px, height=h_px, count=4, dtype="uint8", crs=crs,
        transform=from_origin(e0, n1, args.gsd, args.gsd), compress="deflate", tiled=True, photometric="RGB",
    ) as dst:
        for band in range(3):
            dst.write(out[..., 2 - band], band + 1)  # OpenCV is BGR
        dst.write(alpha, 4)
    # Alignment score independent of how many frames see a cell: fine-texture energy of the multi-frame
    # median relative to the nearest frame, over cells seen by at least 3 frames. 1.0 means the frames
    # line up perfectly (the median is as sharp as one frame); misaligned frames blur the median.
    sharpness = None
    if not args.no_disagreement:
        med = out if args.blend == "median" else median_of(colour, dist)
        near = colour[0]
        sel = (np.isfinite(dist).sum(0) >= 3)
        sub = np.zeros_like(sel)
        sub[::4, ::4] = True
        sel &= sub
        if sel.sum() > 1000:
            def hp(img):
                g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
                return g - cv2.GaussianBlur(g, (0, 0), max(0.1 / args.gsd, 1.0))
            e_med, e_near = float(np.std(hp(med)[sel])), float(np.std(hp(near)[sel]))
            e_out = float(np.std(hp(out)[sel]))
            sharpness = {"median_vs_nearest_highpass_ratio": round(e_med / max(e_near, 1e-6), 4),
                         "output_vs_nearest_highpass_ratio": round(e_out / max(e_near, 1e-6), 4),
                         "cells_used": int(sel.sum()), "highpass_scale_m": 0.1}
    if args.write_source:
        src = accumulate.winner
        with rasterio.open(args.output.with_suffix(".source.tif"), "w", driver="GTiff", width=w_px, height=h_px, count=1,
                           dtype="int16", crs=crs, transform=from_origin(e0, n1, args.gsd, args.gsd), compress="deflate",
                           tiled=True, nodata=-1) as dst:
            dst.write(src, 1)
    label_info = None
    if lut is not None:
        # band 1: group of the nearest usable frame; band 2: distance-weighted vote over all frames
        # (weight 1/d); band 3: share of the vote the winning group got, in percent
        ngroups = len(LABEL_GROUPS) + 1
        votes = np.zeros((ngroups, h_px, w_px), np.float32)
        nearest_lab = np.zeros((h_px, w_px), np.uint8)
        winner = accumulate.winner
        for i, win, _, ok, d_cam in frame_patches():
            lg = frame_patches.label
            if lg is None:
                continue
            wgt = np.where(ok, 1.0 / np.maximum(d_cam, 0.5), 0).astype(np.float32)
            for g in range(1, ngroups):
                votes[g][win] += np.where(lg == g, wgt, 0)
            mine = winner[win] == i
            nearest_lab[win][mine] = lg[mine]
        total = votes.sum(0)
        vote = votes.argmax(0).astype(np.uint8)
        share = np.where(total > 0, np.round(votes.max(0) / np.maximum(total, 1e-6) * 100), 0).astype(np.uint8)
        with rasterio.open(args.output.with_suffix(".labels.tif"), "w", driver="GTiff", width=w_px, height=h_px, count=3,
                           dtype="uint8", crs=crs, transform=from_origin(e0, n1, args.gsd, args.gsd), compress="deflate",
                           tiled=True, nodata=0) as dst:
            dst.write(nearest_lab, 1)
            dst.write(vote, 2)
            dst.write(share, 3)
            dst.update_tags(groups=json.dumps({i + 1: g for i, g in enumerate(LABEL_GROUPS)}))
        both = (nearest_lab > 0) & (vote > 0)
        label_info = {"groups": {i + 1: g for i, g in enumerate(LABEL_GROUPS)},
                      "nearest_equals_vote_share": round(float((nearest_lab == vote)[both].mean()), 4) if both.any() else None,
                      "vote_share_p50": float(np.median(share[total > 0])) if (total > 0).any() else None,
                      "excluded_groups": args.exclude_groups,
                      "note": "votes count only pixels left after --exclude-groups; use a run without exclusion for car_layer.py",
                      "frames_without_label_file": sorted(set(missing_labels))}
    disagreement_info = None
    if not args.no_disagreement:
        disagreement_info = write_disagreement(args.output, colour, dist, seen, texture, in_strip, crs, from_origin(e0, n1, args.gsd, args.gsd), args.gsd)
    report = {
        "model": str(args.model), "images": str(args.images), "crs": crs, "frames_used": len(names),
        "erp_convention": conv, "reprojection_px_median": round(reproj, 3),
        "utm_fit": {"scale": round(float(scale), 5), "gps_residual_mean_m": round(float(resid.mean()), 3),
                    "gps_residual_max_m": round(float(resid.max()), 3)},
        "levelling": level_info, "reference": ref_info, "camera_height_m": args.camera_height, "gsd_m": args.gsd, "strip_m": args.strip,
        "max_range_m": args.max_range, "min_range_m": args.min_range, "blend": args.blend,
        "size_px": [w_px, h_px], "coverage_in_strip": round(coverage, 4),
        "mean_frames_per_covered_pixel": round(float(count[count > 0].mean()), 2) if (count > 0).any() else 0,
        "gain_compensation": gain_info, "disagreement": disagreement_info, "alignment": sharpness, "labels": label_info, "line_tiles": line_info, "object_frames": object_info,
        "ground_model": str(args.ground_model) if args.ground_model else None, "ground_surface": args.ground_surface,
        "level_mode": args.level, "wall_range_m": args.wall_range, "ground_dem": dem_report,
        "mask": mask_info(),
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
