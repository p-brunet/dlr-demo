"""Assemble a candidate's OSM way geometry into data/processed/line.geoparquet."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

from dlr.config import ConductorSpec, LineConfig, Sourced, SourceKind
from dlr.line.candidates import CANDIDATES
from dlr.line.overpass import build_towers_on_ways_query, query_overpass

STORAGE_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:32618"  # UTM 18N -- covers both candidate corridors near 45N, 75W

RAW_CACHE_DIR = Path("data/raw/osm")
PROCESSED_PATH = Path("data/processed/line.geoparquet")


def _load_cached_elements(cache_path: Path) -> list[dict]:
    return json.loads(cache_path.read_text())["elements"]


def _load_way_geometries(elements: list[dict], way_ids: list[int]) -> dict[int, LineString]:
    geoms: dict[int, LineString] = {}
    for el in elements:
        if el["id"] not in way_ids:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) >= 2:
            geoms[el["id"]] = LineString(coords)
    missing = set(way_ids) - set(geoms)
    if missing:
        raise RuntimeError(f"missing cached geometry for way ids: {sorted(missing)}")
    return geoms


def _route_way_ids(
    elements: list[dict], all_way_ids: list[int], primary_ref: str | None
) -> list[int]:
    """Way ids to use for geometry/length: all of them, unless `primary_ref`
    is set, in which case only ways whose `ref` tag contains it -- this drops
    parallel same-corridor circuits mapped as separate ways (see
    `LineCandidate.primary_ref`) while still including every combined-ref way.
    """
    if primary_ref is None:
        return all_way_ids
    by_id = {el["id"]: el for el in elements}
    return [i for i in all_way_ids if primary_ref in (by_id[i]["tags"].get("ref") or "")]


def _tower_count(way_ids: list[int]) -> int:
    result = query_overpass(build_towers_on_ways_query(way_ids), timeout=180, max_attempts=3)
    return sum(1 for el in result.get("elements", []) if el.get("type") == "node")


def select_line(candidate: str, line_config_out: Path) -> None:
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate {candidate!r}; known: {sorted(CANDIDATES)}")
    cand = CANDIDATES[candidate]

    if not cand.osm_way_ids:
        raise RuntimeError(
            f"candidate {candidate!r} has no resolved OSM geometry yet "
            f"(see notes: {cand.notes}) -- refusing to write a partial/wrong geometry."
        )

    cache_path = RAW_CACHE_DIR / f"{candidate.replace('-', '_')}_ways.json"
    if not cache_path.exists():
        raise RuntimeError(f"expected a cached Overpass response at {cache_path}; fetch it first")

    elements = _load_cached_elements(cache_path)
    route_way_ids = _route_way_ids(elements, cand.osm_way_ids, cand.primary_ref)
    geoms = _load_way_geometries(elements, route_way_ids)
    merged = linemerge(unary_union(list(geoms.values())))

    gdf = gpd.GeoDataFrame(
        {
            "line_id": [candidate],
            "name": [f"{cand.from_substation.name} <-> {cand.to_substation.name}"],
            "refs": [",".join(cand.osm_refs)],
            "primary_ref": [cand.primary_ref],
            "ieso_zones": [",".join(cand.ieso_zones)],
            "from_substation": [cand.from_substation.name],
            "to_substation": [cand.to_substation.name],
            "from_region": [cand.from_substation.region],
            "to_region": [cand.to_substation.region],
            "route_osm_way_ids": [",".join(str(i) for i in route_way_ids)],
            "source_osm_way_ids": [",".join(str(i) for i in cand.osm_way_ids)],
            "verified_on": ["2026-09-25"],
        },
        geometry=[merged],
        crs=STORAGE_CRS,
    )

    working = gdf.to_crs(WORKING_CRS)
    length_km = float(working.length.iloc[0]) / 1000.0
    gdf["length_km"] = [round(length_km, 2)]

    print("Resolving tower count along the full traced corridor (one batched Overpass query)...")
    tower_count = _tower_count(cand.osm_way_ids)
    gdf["tower_count_min"] = [tower_count]

    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(PROCESSED_PATH)

    print(f"Wrote {PROCESSED_PATH}")
    route_label = cand.primary_ref or "all refs"
    print(f"  length_km ({route_label} route, {len(route_way_ids)} ways): {length_km:.2f}")
    print(f"  tower_count (all {len(cand.osm_way_ids)} traced ways, deduplicated): {tower_count}")
    print(f"  from: {cand.from_substation.name} ({cand.from_substation.region})")
    print(f"  to:   {cand.to_substation.name} ({cand.to_substation.region})")

    draft_line_config = LineConfig(
        line_name=gdf["name"].iloc[0],
        operator=f"{cand.from_substation.operator} / {cand.to_substation.operator}",
        osm_way_ids=cand.osm_way_ids,
        conductor=ConductorSpec(
            name="TBD",
            diameter_m=Sourced(value=0.0, source=SourceKind.USER_PROVIDED, reference="placeholder"),
            resistance_dc_20c_ohm_per_m=Sourced(
                value=0.0, source=SourceKind.USER_PROVIDED, reference="placeholder"
            ),
            absorptivity=Sourced(
                value=0.8,
                source=SourceKind.INDUSTRY_TYPICAL,
                reference="CIGRE TB 601 typical value",
            ),
            emissivity=Sourced(
                value=0.8,
                source=SourceKind.INDUSTRY_TYPICAL,
                reference="CIGRE TB 601 typical value",
            ),
            max_operating_temp_c=Sourced(
                value=75,
                source=SourceKind.INDUSTRY_TYPICAL,
                reference="typical ACSR continuous rating",
            ),
        ),
        notes=cand.notes,
    )
    line_config_out.write_text(_to_yaml(draft_line_config))
    print(f"Wrote draft {line_config_out}")


def _to_yaml(model) -> str:
    import yaml

    return yaml.safe_dump(json.loads(model.model_dump_json()), sort_keys=False)
