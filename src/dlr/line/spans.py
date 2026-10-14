"""Span model: one span per pair of consecutive towers along the route.

Geometry only (length, azimuth, midpoint, river crossing / province); terrain
attributes are added by `dlr.terrain`. The ON/QC side comes from the Ottawa
River centreline, which is the provincial border along this corridor.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Geod
from shapely.geometry import LineString, Point

from dlr.config import BoundingBox
from dlr.line.overpass import query_overpass

SPANS_PATH = Path("data/processed/spans.geoparquet")
RIVER_CACHE = Path("data/raw/osm/ottawa_river.json")

_GEOD = Geod(ellps="WGS84")


def build_river_query(bbox: BoundingBox, timeout: int = 180) -> str:
    box = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
    return (
        f"[out:json][timeout:{timeout}];"
        f'way["waterway"="river"]["name"~"Ottawa|Outaouais"]({box});out geom;'
    )


def fetch_river(bbox: BoundingBox, cache_path: Path = RIVER_CACHE) -> list[LineString]:
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        data = query_overpass(build_river_query(bbox))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    return [
        LineString([(p["lon"], p["lat"]) for p in el["geometry"]])
        for el in data["elements"]
        if el["type"] == "way" and len(el.get("geometry", [])) >= 2
    ]


def build_spans(
    towers: gpd.GeoDataFrame, river: list[LineString], from_region: str, to_region: str
) -> gpd.GeoDataFrame:
    """Spans between consecutive towers, in route order (`tower_seq`)."""
    towers = towers.sort_values("tower_seq").reset_index(drop=True)
    lon, lat = towers.geometry.x.to_numpy(), towers.geometry.y.to_numpy()
    az, _, length = _GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
    mid_lon, mid_lat = [], []
    for a_lon, a_lat, azimuth, dist in zip(lon[:-1], lat[:-1], az, length, strict=True):
        m_lon, m_lat, _ = _GEOD.fwd(a_lon, a_lat, azimuth, dist / 2)
        mid_lon.append(m_lon)
        mid_lat.append(m_lat)

    geometries = [
        LineString([(lon[i], lat[i]), (lon[i + 1], lat[i + 1])]) for i in range(len(towers) - 1)
    ]
    crosses = np.array([any(g.intersects(r) for r in river) for g in geometries])
    if crosses.sum() != 1:
        raise ValueError(f"expected exactly one river-crossing span, found {int(crosses.sum())}")
    crossing = int(np.argmax(crosses))
    region = np.where(np.arange(len(geometries)) < crossing, from_region, to_region).astype(object)
    region[crossing] = f"{from_region}/{to_region}"

    chainage = towers["chainage_km"].to_numpy()
    return gpd.GeoDataFrame(
        {
            "line_id": towers["line_id"].iloc[0],
            "span_id": [f"S{i:03d}" for i in range(len(geometries))],
            "span_seq": np.arange(len(geometries)),
            "from_tower_seq": towers["tower_seq"].to_numpy()[:-1],
            "to_tower_seq": towers["tower_seq"].to_numpy()[1:],
            "from_osm_node_id": towers["osm_node_id"].to_numpy()[:-1],
            "to_osm_node_id": towers["osm_node_id"].to_numpy()[1:],
            "length_m": length,
            "azimuth_deg": np.mod(az, 360.0),
            "axis_deg": np.mod(az, 180.0),
            "chainage_mid_km": (chainage[:-1] + chainage[1:]) / 2,
            "mid_lon": mid_lon,
            "mid_lat": mid_lat,
            "crosses_river": crosses,
            "region": region,
        },
        geometry=geometries,
        crs=towers.crs,
    )


def spans_midpoints(spans: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return gpd.GeoSeries(
        [Point(x, y) for x, y in zip(spans["mid_lon"], spans["mid_lat"], strict=True)],
        crs=spans.crs,
    )
