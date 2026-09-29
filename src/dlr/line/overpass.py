"""Minimal Overpass API client with retry/backoff and mirror fallback.

The public Overpass instances 406 without a descriptive User-Agent and 504
under load, so both are handled here rather than in every call site.
"""

from __future__ import annotations

import time

import httpx

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

USER_AGENT = "dlr-demo/0.1 (+https://github.com/p-brunet/dlr-demo)"

RETRYABLE_STATUS = {429, 504}


def query_overpass(query: str, timeout: float = 60.0, max_attempts: int = 3) -> dict:
    """Run an Overpass QL query, retrying with backoff and mirror fallback."""
    last_error: Exception | None = None
    for mirror in MIRRORS:
        for attempt in range(max_attempts):
            try:
                resp = httpx.get(
                    mirror,
                    params={"data": query},
                    headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                    timeout=timeout,
                )
                if resp.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                delay = 5.0 * (2**attempt)
                time.sleep(delay)
        # exhausted retries on this mirror, try the next one
    raise RuntimeError(f"Overpass query failed on all mirrors: {last_error}") from last_error


def build_way_geometry_query(way_id: int, timeout: int = 180) -> str:
    return f"[out:json][timeout:{timeout}];way({way_id});out geom;"


def build_towers_on_way_query(way_id: int, timeout: int = 180) -> str:
    return f"[out:json][timeout:{timeout}];way({way_id});node(w)['power'='tower'];out skel qt;"


def build_towers_on_ways_query(way_ids: list[int], timeout: int = 180) -> str:
    id_list = ",".join(str(i) for i in way_ids)
    return f"[out:json][timeout:{timeout}];way(id:{id_list});node(w)['power'='tower'];out skel qt;"


def build_ref_search_query(
    bbox: tuple[float, float, float, float], ref: str, timeout: int = 180
) -> str:
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    return f"[out:json][timeout:{timeout}];way['power'='line']['ref'~'{ref}']({bbox_str});out geom;"
