"""Terrain attributes from NRCan's HRDEM (LiDAR-derived 1 m DTM/DSM).

Items are resolved through NRCan's STAC API (`hrdem-mosaic-1m`, cloud-optimized
GeoTIFFs in EPSG:3979, OGL-Canada-2.0) and only small windows around each span
are read over HTTP range requests; each window is cached under
`data/interim/hrdem/`.

Per tower: ground altitude (DTM median within 3 m) and, for QA only, the
highest DSM return within 8 m above ground (lattice towers are only partly
captured, so it under-estimates tower height).

Per span: obstacle heights (DSM - DTM: trees, buildings) in a ring 20-60 m
either side of the span axis -- beyond the ~40 m cleared right-of-way
(Hydro One 230 kV corridors are 40-45 m wide) -- summarised as
`shelter_index` = mean(min(obstacle height / conductor height, 1)), and
`exposure_index` = 1 - shelter_index (1 = open terrain or water).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import Point, shape

STAC_SEARCH = "https://datacube.services.geo.ca/stac/api/search"
COLLECTION = "hrdem-mosaic-1m"
HRDEM_CRS = "EPSG:3979"
CACHE_DIR = Path("data/interim/hrdem")
ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence - Canada "
    "(HRDEM, Natural Resources Canada)"
)

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "VSI_CACHE": "TRUE",
}

RING_INNER_M = 20.0
RING_OUTER_M = 60.0
TOWER_BASE_RADIUS_M = 3.0
TOWER_TOP_RADIUS_M = 8.0


def stac_items(bbox: tuple[float, float, float, float]) -> list[dict]:
    resp = httpx.post(
        STAC_SEARCH, json={"collections": [COLLECTION], "bbox": list(bbox), "limit": 50}, timeout=60
    )
    resp.raise_for_status()
    return resp.json()["features"]


def _item_for(point_lonlat: Point, items: list[dict]) -> dict:
    for item in items:
        if shape(item["geometry"]).contains(point_lonlat):
            return item
    raise LookupError(f"no {COLLECTION} item covers {point_lonlat.wkt}")


def _read_window(href: str, bounds: tuple[float, float, float, float], cache: Path):
    """Masked float32 array + transform for `bounds` (EPSG:3979), cached as GeoTIFF."""
    if not cache.exists():
        with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
            win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            data = src.read(1, window=win)
            profile = src.profile | {
                "driver": "GTiff",
                "width": data.shape[1],
                "height": data.shape[0],
                "transform": src.window_transform(win),
                "compress": "deflate",
                "tiled": False,
            }
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            cache.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(cache, "w", **profile) as dst:
                dst.write(data, 1)
    with rasterio.open(cache) as src:
        return src.read(1, masked=True).astype("float32"), src.transform


def _span_terrain(span_id: str, geom_3979, hrefs: dict[str, str], conductor_height_m: float):
    bounds = geom_3979.buffer(RING_OUTER_M + 5).bounds
    dtm, transform = _read_window(hrefs["dtm"], bounds, CACHE_DIR / f"{span_id}_dtm.tif")
    dsm, _ = _read_window(hrefs["dsm"], bounds, CACHE_DIR / f"{span_id}_dsm.tif")
    # water and voids are nodata in the DSM/DTM: treat them as open ground
    obstacle = np.ma.filled(np.clip(dsm - dtm, 0, None), 0.0)
    ring = geom_3979.buffer(RING_OUTER_M).difference(geom_3979.buffer(RING_INNER_M))
    inside = ~geometry_mask([ring], out_shape=obstacle.shape, transform=transform)
    heights = obstacle[inside]
    shelter = float(np.mean(np.minimum(heights / conductor_height_m, 1.0)))
    return {
        "span_id": span_id,
        "obstacle_frac_5m": float(np.mean(heights > 5.0)),
        "obstacle_frac_10m": float(np.mean(heights > 10.0)),
        "obstacle_p90_m": float(np.percentile(heights, 90)),
        "shelter_index": shelter,
        "exposure_index": 1.0 - shelter,
        "dtm_valid_frac": float(1.0 - np.ma.getmaskarray(dtm)[inside].mean()),
    }, (dtm, dsm, transform)


def _tower_terrain(points_3979: list[Point], dtm, dsm, transform) -> list[tuple[float, float]]:
    out = []
    for p in points_3979:
        base = ~geometry_mask(
            [p.buffer(TOWER_BASE_RADIUS_M)], out_shape=dtm.shape, transform=transform
        )
        top = ~geometry_mask(
            [p.buffer(TOWER_TOP_RADIUS_M)], out_shape=dtm.shape, transform=transform
        )
        ground = float(np.ma.median(dtm[base])) if dtm[base].count() else float("nan")
        highest = float(dsm[top].max()) if dsm[top].count() else float("nan")
        out.append((ground, highest - ground))
    return out


def add_terrain(
    towers: gpd.GeoDataFrame, spans: gpd.GeoDataFrame, conductor_height_m: float, workers: int = 8
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return copies of towers/spans with HRDEM-derived columns added."""
    items = stac_items(tuple(spans.total_bounds))
    hrefs_by_span = []
    for _, s in spans.iterrows():
        item = _item_for(Point(s["mid_lon"], s["mid_lat"]), items)
        hrefs_by_span.append({k: item["assets"][k]["href"] for k in ("dtm", "dsm")})

    spans_3979 = spans.to_crs(HRDEM_CRS)
    towers_3979 = towers.to_crs(HRDEM_CRS).set_index("tower_seq")

    def work(i: int):
        s = spans_3979.iloc[i]
        attrs, (dtm, dsm, transform) = _span_terrain(
            s["span_id"], s.geometry, hrefs_by_span[i], conductor_height_m
        )
        ends = [towers_3979.geometry[s["from_tower_seq"]], towers_3979.geometry[s["to_tower_seq"]]]
        return attrs, _tower_terrain(ends, dtm, dsm, transform)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, range(len(spans_3979))))

    span_attrs = gpd.pd.DataFrame([r[0] for r in results])
    spans = spans.merge(span_attrs, on="span_id", how="left")

    # each tower appears at the end of one span and the start of the next: average both reads
    ground: dict[int, list[float]] = {}
    tower_h: dict[int, list[float]] = {}
    for (_, (start, end)), (_, s) in zip(results, spans.iterrows(), strict=True):
        for seq, (g, h) in ((s["from_tower_seq"], start), (s["to_tower_seq"], end)):
            ground.setdefault(seq, []).append(g)
            tower_h.setdefault(seq, []).append(h)
    towers = towers.copy()
    nan = [float("nan")]
    towers["ground_alt_m"] = [float(np.nanmean(ground.get(q, nan))) for q in towers["tower_seq"]]
    towers["dsm_height_above_ground_m"] = [
        float(np.nanmax(tower_h.get(q, nan))) for q in towers["tower_seq"]
    ]

    alt = towers.set_index("tower_seq")["ground_alt_m"]
    spans["from_ground_alt_m"] = spans["from_tower_seq"].map(alt).to_numpy()
    spans["to_ground_alt_m"] = spans["to_tower_seq"].map(alt).to_numpy()
    spans["inclination_deg"] = np.degrees(
        np.arctan2(spans["to_ground_alt_m"] - spans["from_ground_alt_m"], spans["length_m"])
    )
    return towers, spans
