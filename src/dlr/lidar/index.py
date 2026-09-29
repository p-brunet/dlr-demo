"""Query NRCan's CanElevation LiDAR project index by bounding box.

Uses the public ESRI REST layer confirmed live during Milestone 1 research
(no API key, no sign-request needed) rather than downloading the full
448MB tile-index GeoPackage for a simple bbox lookup.
"""

from __future__ import annotations

import httpx

from dlr.config import BoundingBox

PROJECTS_LAYER_URL = (
    "https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/"
    "lidar_point_cloud_canelevation_en/MapServer/0/query"
)


def list_projects_for_bbox(bbox: BoundingBox) -> list[dict[str, object]]:
    geometry = {
        "xmin": bbox.min_lon,
        "ymin": bbox.min_lat,
        "xmax": bbox.max_lon,
        "ymax": bbox.max_lat,
        "spatialReference": {"wkid": 4326},
    }
    params: dict[str, str | int] = {
        "f": "json",
        "geometry": str(geometry).replace("'", '"'),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
    }
    resp = httpx.get(PROJECTS_LAYER_URL, params=params, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    return [f["attributes"] for f in data.get("features", [])]
