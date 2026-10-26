"""Map CRS of a scene: configurable, with an automatic UTM default from GPS.

Default: ETRS89 / UTM (EPSG:258ZZ) for zones 28-38 (Europe), WGS 84 / UTM (EPSG:326ZZ north,
EPSG:327ZZ south) elsewhere. Berlin (13.4 E, 52.5 N) gives EPSG:25833, Alsace (7.49 E) EPSG:25832.
An override ("EPSG:25832", or just "25832") wins; it comes from `--crs` or the env var ORTHO_CRS.
"""
from __future__ import annotations

import os

ETRS89_ZONES = range(28, 39)  # EPSG:25828 .. EPSG:25838


def utm_epsg_for(lon: float, lat: float) -> str:
    zone = int((lon + 180.0) // 6) % 60 + 1
    if zone in ETRS89_ZONES and lat >= 0:
        return f"EPSG:258{zone:02d}"
    return f"EPSG:{326 if lat >= 0 else 327}{zone:02d}"


def normalise(crs: str) -> str:
    crs = crs.strip()
    return crs if ":" in crs else f"EPSG:{crs}"


def resolve_crs(lon: float, lat: float, override: str | None = None) -> str:
    """Override (argument), else env ORTHO_CRS, else the UTM zone at (lon, lat)."""
    chosen = override or os.environ.get("ORTHO_CRS")
    return normalise(chosen) if chosen else utm_epsg_for(lon, lat)


def read_local_to_map(tf: dict):
    """(rotation 2x2, translation 2, scale, crs) of a transform.json written by erp_to_pinhole_dataset.py.

    Reads "local_to_map", else the older "local_to_utm33n" (Berlin scenes). The CRS is the "crs"
    key (None for scenes without GPS: local metres), else EPSG:25833 for files from before it existed.
    """
    import numpy as np

    m = tf["local_to_map"] if "local_to_map" in tf else tf["local_to_utm33n"]
    return np.array(m["rotation"]), np.array(m["translation"]), m["scale"], tf.get("crs", "EPSG:25833")


def _map_coords(coords, fn):
    if coords and isinstance(coords[0], (int, float)):
        x, y = fn(coords[0], coords[1])
        return [x, y, *coords[2:]]
    return [_map_coords(c, fn) for c in coords]


def load_geojson_in_crs(path, crs) -> dict:
    """Read a GeoJSON file and return it with all coordinates in `crs` (a CRS string or rasterio CRS).

    The source CRS is the file's "crs" member (e.g. "urn:ogc:def:crs:EPSG::25833" from the Berlin WFS).
    Without one, lon/lat-looking coordinates are taken as EPSG:4326 (GeoJSON standard), anything else
    as already being in `crs`. Nothing is changed when source and target CRS are equal.
    """
    import json
    from pathlib import Path

    from pyproj import CRS, Transformer

    data = json.loads(Path(path).read_text())
    target = CRS.from_user_input(str(crs) if not isinstance(crs, CRS) else crs)
    named = (data.get("crs") or {}).get("properties", {}).get("name")
    if named:
        source = CRS.from_user_input(named)
    else:
        xs = []
        for f in data.get("features", []):
            _map_coords(f["geometry"]["coordinates"], lambda x, y: xs.append((x, y)) or (x, y))
            if len(xs) > 50:
                break
        lonlat = bool(xs) and all(abs(x) <= 180 and abs(y) <= 90 for x, y in xs)
        source = CRS.from_epsg(4326) if lonlat else target
    if source == target:
        return data
    tr = Transformer.from_crs(source, target, always_xy=True)
    for f in data.get("features", []):
        g = f["geometry"]
        g["coordinates"] = _map_coords(g["coordinates"], lambda x, y: tuple(tr.transform(x, y)))
    data["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:{target.to_string().replace(':', '::')}"}}
    return data
