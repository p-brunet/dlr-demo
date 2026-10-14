"""Resolve a candidate's OSM route into line/tower GeoParquet files and config/line.yaml.

The route is assembled from the candidate's explicit, ordered way list and then
validated: consecutive ways must join (gap <= MAX_JOIN_GAP_M) and the route must
start and end within MAX_ENDPOINT_DISTANCE_M of its two substations. Either
failure raises instead of writing a partial geometry -- the M1 output silently
dropped an untagged river-crossing way and never reached Quebec.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import numpy as np
import yaml
from pyproj import Geod, Transformer
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from dlr.config import LineConfig, Sourced, SourceKind
from dlr.line.candidates import CANDIDATES, LineCandidate, Substation
from dlr.line.overpass import build_route_query, query_overpass

STORAGE_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:32618"  # UTM 18N -- metric distances around 45N, 75W

RAW_CACHE_DIR = Path("data/raw/osm")
PROCESSED_DIR = Path("data/processed")
LINE_PATH = PROCESSED_DIR / "line.geoparquet"
TOWERS_PATH = PROCESSED_DIR / "towers.geoparquet"

TOWER_POWER_TAGS = {"tower", "pole", "portal", "terminal"}
WIRES_PER_PHASE = {"single": 1, "double": 2, "triple": 3, "quad": 4}
MAX_JOIN_GAP_M = 50.0
MAX_ENDPOINT_DISTANCE_M = 500.0

_GEOD = Geod(ellps="WGS84")
_TO_WORKING = Transformer.from_crs(STORAGE_CRS, WORKING_CRS, always_xy=True)


class RouteValidationError(RuntimeError):
    pass


@dataclass
class Route:
    way_ids: list[int]
    coords: list[tuple[float, float]]  # (lon, lat) per route node, in route order
    node_ids: list[int]
    towers: list[dict] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        lons, lats = zip(*self.coords, strict=True)
        return float(_GEOD.line_length(lons, lats))

    def reversed(self) -> Route:
        return Route(
            way_ids=self.way_ids[::-1],
            coords=self.coords[::-1],
            node_ids=self.node_ids[::-1],
            towers=self.towers[::-1],
        )


def _geodesic_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(_GEOD.inv(a[0], a[1], b[0], b[1])[2])


def _working(geom: BaseGeometry) -> BaseGeometry:
    from shapely.ops import transform

    return transform(_TO_WORKING.transform, geom)


def fetch_osm(cand: LineCandidate, cache_path: Path, refresh: bool = False) -> dict:
    """Return the Overpass response for the candidate's route, cached on disk."""
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    substation_ids = [
        s.osm_way_id for s in (cand.from_substation, cand.to_substation) if s.osm_way_id
    ]
    data = query_overpass(build_route_query(cand.route_way_ids, substation_ids), timeout=240)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def assemble_route(osm: dict, way_ids: list[int]) -> Route:
    """Chain `way_ids` head-to-tail (reversing ways as needed) into one route."""
    ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node"}
    missing = [w for w in way_ids if w not in ways]
    if missing:
        raise RouteValidationError(f"OSM response lacks route ways {missing}")

    def lonlat(node_id: int) -> tuple[float, float]:
        return (nodes[node_id]["lon"], nodes[node_id]["lat"])

    def gap(a: int, b: int) -> float:
        return 0.0 if a == b else _geodesic_m(lonlat(a), lonlat(b))

    sequence: list[int] = []
    for i, way_id in enumerate(way_ids):
        way_nodes = list(ways[way_id]["nodes"])
        if sequence:
            head_gap, tail_gap = gap(sequence[-1], way_nodes[0]), gap(sequence[-1], way_nodes[-1])
            if tail_gap < head_gap:
                way_nodes.reverse()
            if min(head_gap, tail_gap) > MAX_JOIN_GAP_M:
                raise RouteValidationError(
                    f"ways {way_ids[i - 1]} and {way_id} do not join "
                    f"(gap {min(head_gap, tail_gap):.0f} m > {MAX_JOIN_GAP_M:.0f} m)"
                )
        elif len(way_ids) > 1:
            following = ways[way_ids[1]]["nodes"]
            ends = (following[0], following[-1])
            if min(gap(way_nodes[0], n) for n in ends) < min(gap(way_nodes[-1], n) for n in ends):
                way_nodes.reverse()
        for node_id in way_nodes:
            if not sequence or sequence[-1] != node_id:
                sequence.append(node_id)

    towers = []
    for node_id in sequence:
        tags = nodes[node_id].get("tags", {})
        if tags.get("power") in TOWER_POWER_TAGS:
            lon, lat = lonlat(node_id)
            towers.append(
                {
                    "osm_node_id": node_id,
                    "lon": lon,
                    "lat": lat,
                    "power": tags["power"],
                    "ref": tags.get("ref"),
                }
            )
    return Route(
        way_ids=list(way_ids),
        coords=[lonlat(n) for n in sequence],
        node_ids=sequence,
        towers=towers,
    )


def _substation_geometry(osm: dict, substation: Substation) -> BaseGeometry:
    for el in osm["elements"]:
        if el["type"] == "way" and el["id"] == substation.osm_way_id:
            ring = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(ring) >= 4 and ring[0] == ring[-1]:
                return _working(Polygon(ring))
    return _working(Point(substation.lon, substation.lat))


def orient_and_validate(route: Route, osm: dict, cand: LineCandidate) -> tuple[Route, float, float]:
    """Orient the route from -> to and check both ends reach their substations.

    Returns the oriented route and the start/end distances (m) to the substations.
    """
    from_geom = _substation_geometry(osm, cand.from_substation)
    to_geom = _substation_geometry(osm, cand.to_substation)

    def dist(lonlat: tuple[float, float], geom: BaseGeometry) -> float:
        return float(_working(Point(lonlat)).distance(geom))

    start, end = route.coords[0], route.coords[-1]
    if dist(start, to_geom) + dist(end, from_geom) < dist(start, from_geom) + dist(end, to_geom):
        route = route.reversed()
        start, end = end, start

    d_start, d_end = dist(start, from_geom), dist(end, to_geom)
    errors = []
    if d_start > MAX_ENDPOINT_DISTANCE_M:
        errors.append(f"route starts {d_start:.0f} m from {cand.from_substation.name}")
    if d_end > MAX_ENDPOINT_DISTANCE_M:
        errors.append(f"route ends {d_end:.0f} m from {cand.to_substation.name}")
    if len(route.towers) < 2:
        errors.append(f"only {len(route.towers)} tower node(s) on the route")
    if errors:
        raise RouteValidationError("; ".join(errors))
    return route, d_start, d_end


def _tag_consensus(ways: list[dict], key: str) -> tuple[str | None, list[int]]:
    """Most common value of `key` over the route ways, and the ways carrying it."""
    values = Counter(w["tags"][key] for w in ways if key in w.get("tags", {}))
    if not values:
        return None, []
    value = values.most_common(1)[0][0]
    return value, [w["id"] for w in ways if w.get("tags", {}).get(key) == value]


def _line_config(cand: LineCandidate, osm: dict, route: Route, verified_on: str) -> LineConfig:
    by_id = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    ways = [by_id[i] for i in route.way_ids]

    def from_osm[T](value: T, key: str, way_ids: list[int]) -> Sourced[T]:
        ids = ",".join(str(i) for i in way_ids)
        reference = f"OSM {key} on way(s) {ids}"
        return Sourced(value=value, source=SourceKind.OPEN_DATA, reference=reference)

    voltage, voltage_ways = _tag_consensus(ways, "voltage")
    circuits, circuit_ways = _tag_consensus(ways, "circuits")
    wires, wire_ways = _tag_consensus(ways, "wires")
    if voltage is None or circuits is None:
        raise RouteValidationError("route ways carry no voltage/circuits tags")
    if wires in WIRES_PER_PHASE:
        sub_conductors = from_osm(WIRES_PER_PHASE[wires], "wires", wire_ways)
    else:
        sub_conductors = Sourced(
            value=1,
            source=SourceKind.INDUSTRY_TYPICAL,
            reference="no OSM wires tag; assumed single",
        )

    return LineConfig(
        line_id=cand.candidate_id,
        line_name=f"{cand.from_substation.name} <-> {cand.to_substation.name}",
        operator=f"{cand.from_substation.operator} / {cand.to_substation.operator}",
        voltage_kv=from_osm(float(voltage.split(";")[0]) / 1000.0, "voltage", voltage_ways),
        circuits=from_osm(int(circuits), "circuits", circuit_ways),
        sub_conductors=sub_conductors,
        from_substation=cand.from_substation.name,
        to_substation=cand.to_substation.name,
        ieso_zones=cand.ieso_zones,
        route_osm_way_ids=route.way_ids,
        length_km=round(route.length_m / 1000.0, 3),
        tower_count=len(route.towers),
        osm_timestamp=osm.get("osm3s", {}).get("timestamp_osm_base", "unknown"),
        verified_on=verified_on,
        notes=cand.notes,
    )


def _towers_frame(route: Route, line_id: str) -> gpd.GeoDataFrame:
    # chainage of each tower along the route geometry, not tower-to-tower
    node_chainage = np.concatenate(
        [[0.0], np.cumsum([_geodesic_m(a, b) for a, b in pairwise(route.coords)])]
    )
    chainage_by_node = dict(zip(route.node_ids, node_chainage, strict=True))
    return gpd.GeoDataFrame(
        {
            "line_id": line_id,
            "tower_seq": np.arange(len(route.towers)),
            "osm_node_id": [t["osm_node_id"] for t in route.towers],
            "power": [t["power"] for t in route.towers],
            "osm_ref": [t["ref"] for t in route.towers],
            "chainage_km": [chainage_by_node[t["osm_node_id"]] / 1000.0 for t in route.towers],
        },
        geometry=[Point(t["lon"], t["lat"]) for t in route.towers],
        crs=STORAGE_CRS,
    )


def select_line(candidate: str, line_config_out: Path, refresh: bool = False) -> LineConfig:
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate {candidate!r}; known: {sorted(CANDIDATES)}")
    cand = CANDIDATES[candidate]
    if not cand.route_way_ids:
        raise RouteValidationError(
            f"candidate {candidate!r} has no registered route -- refusing to write a "
            f"partial/wrong geometry. Notes: {cand.notes}"
        )

    cache_path = RAW_CACHE_DIR / f"{candidate.replace('-', '_')}.json"
    osm = fetch_osm(cand, cache_path, refresh=refresh)
    route = assemble_route(osm, cand.route_way_ids)
    route, d_start, d_end = orient_and_validate(route, osm, cand)

    verified_on = datetime.now(UTC).date().isoformat()
    config = _line_config(cand, osm, route, verified_on)

    line = gpd.GeoDataFrame(
        {
            "line_id": [config.line_id],
            "name": [config.line_name],
            "refs": [",".join(cand.osm_refs)],
            "ieso_zones": [",".join(cand.ieso_zones)],
            "from_substation": [cand.from_substation.name],
            "to_substation": [cand.to_substation.name],
            "from_region": [cand.from_substation.region],
            "to_region": [cand.to_substation.region],
            "route_osm_way_ids": [",".join(str(i) for i in route.way_ids)],
            "voltage_kv": [config.voltage_kv.value],
            "circuits": [config.circuits.value],
            "sub_conductors": [config.sub_conductors.value],
            "length_km": [config.length_km],
            "tower_count": [config.tower_count],
            "osm_timestamp": [config.osm_timestamp],
            "verified_on": [verified_on],
        },
        geometry=[LineString(route.coords)],
        crs=STORAGE_CRS,
    )
    towers = _towers_frame(route, config.line_id)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    line.to_parquet(LINE_PATH)
    towers.to_parquet(TOWERS_PATH)
    header = "# GENERATED by `dlr select-line` -- do not edit by hand.\n"
    payload = yaml.safe_dump(
        json.loads(config.model_dump_json()), sort_keys=False, allow_unicode=True
    )
    line_config_out.write_text(header + payload, encoding="utf-8")

    print(f"Wrote {LINE_PATH}, {TOWERS_PATH} and {line_config_out}")
    print(f"  {config.line_name}: {config.length_km:.2f} km, {config.tower_count} towers")
    print(
        f"  ends: {d_start:.0f} m from {cand.from_substation.name}, "
        f"{d_end:.0f} m from {cand.to_substation.name}"
    )
    print(
        f"  {config.voltage_kv.value:.0f} kV, {config.circuits.value} circuits, "
        f"{config.sub_conductors.value} sub-conductor(s)/phase; OSM {config.osm_timestamp}"
    )
    return config
