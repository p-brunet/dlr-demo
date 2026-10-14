"""Hourly station observations from ECCC's MSC GeoMet OGC API (`climate-hourly`).

Used as the weather "truth" for forecast verification (M5). Records carry an
explicit `UTC_DATE` (`LOCAL_DATE` is local standard time); wind speed is in
km/h and wind direction in tens of degrees, converted here at the boundary to
m/s and degrees ("from", clockwise from true north; 0 with zero speed = calm).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ITEMS_URL = "https://api.weather.gc.ca/collections/climate-hourly/items"
USER_AGENT = "dlr-demo/0.1 (+https://github.com/p-brunet/dlr-demo)"
OBS_PATH = Path("data/processed/station_obs.parquet")
ATTRIBUTION = (
    "Contains information licenced under the Data Server End-use Licence of "
    "Environment and Climate Change Canada."
)

# Stations with current hourly records, ~9-10 km from the line (searched 2026-10-13).
# OTTAWA GATINEAU A has two climate ids; 7032685 is the complete hourly series.
STATIONS = {
    "YOW": "6106001",  # OTTAWA INTL A
    "YND": "7032685",  # OTTAWA GATINEAU A
}
# from the climate-stations collection (ELEVATION, m)
STATION_ELEVATION_M = {"YOW": 114.9, "YND": 64.3}


def fetch_hourly(climate_id: str, start: str, end: str, page_size: int = 5000) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120.0) as client:
        while True:
            resp = client.get(
                ITEMS_URL,
                params={
                    "CLIMATE_IDENTIFIER": climate_id,
                    "datetime": f"{start}T00:00:00/{end}T23:59:59",
                    "f": "json",
                    "limit": page_size,
                    "offset": offset,
                    "sortby": "UTC_DATE",
                },
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            rows.extend(f["properties"] for f in features)
            if len(features) < page_size:
                break
            offset += page_size
    return pd.DataFrame(rows)


def tidy(raw: pd.DataFrame, station: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    speed = pd.to_numeric(raw["WIND_SPEED"], errors="coerce") / 3.6
    direction = pd.to_numeric(raw["WIND_DIRECTION"], errors="coerce") * 10.0
    direction = direction.where(speed > 0)  # calm: direction undefined
    return pd.DataFrame(
        {
            "station": station,
            "climate_id": raw["CLIMATE_IDENTIFIER"],
            "station_name": raw["STATION_NAME"],
            "time": pd.to_datetime(raw["UTC_DATE"], utc=True).dt.tz_localize(None),
            "lat": pd.to_numeric(raw["LATITUDE_DECIMAL_DEGREES"]),
            "lon": pd.to_numeric(raw["LONGITUDE_DECIMAL_DEGREES"]),
            "air_temperature": pd.to_numeric(raw["TEMP"], errors="coerce"),
            "wind_speed_10m": speed,
            "wind_direction_10m": np.mod(direction, 360.0),
            "dew_point_temperature": pd.to_numeric(raw["DEW_POINT_TEMP"], errors="coerce"),
        }
    ).sort_values("time")


def fetch_stations(start: str, end: str, stations: dict[str, str] | None = None) -> pd.DataFrame:
    frames = [
        tidy(fetch_hourly(cid, start, end), name) for name, cid in (stations or STATIONS).items()
    ]
    return pd.concat([f for f in frames if not f.empty], ignore_index=True)
