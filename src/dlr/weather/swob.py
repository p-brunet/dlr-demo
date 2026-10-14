"""Minute observations from ECCC SWOB (MSC GeoMet `swob-realtime`).

Ottawa CDA RCS (CXOA, msc_id 6105978) is an automatic ECCC station 8.9 km from
the line that reports every minute: 1-minute mean wind speed and direction at
10 m, plus the station's own 2-minute and 1-hour means. It is the only station
near the corridor with sub-hourly wind (YOW/YND report hourly 2-minute
snapshots), which is what the wind study in `dlr.verify.wind` needs.

SWOB keeps only ~30 days, so `archive()` writes one Parquet file per complete
UTC day under `data/obs/swob/<station>/` and never rewrites a day; it runs
daily alongside the IESO archive. Units are converted at the boundary
(km/h -> m/s); a 1-minute value is kept only when its `-qa` flag is 100
(passed quality control).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ITEMS_URL = "https://api.weather.gc.ca/collections/swob-realtime/items"
USER_AGENT = "dlr-demo/0.1 (+https://github.com/p-brunet/dlr-demo)"
ARCHIVE_DIR = Path("data/obs/swob")
ATTRIBUTION = (
    "Contains information licenced under the Data Server End-use Licence of "
    "Environment and Climate Change Canada."
)

STATIONS = {"CXOA": "6105978"}  # OTTAWA CDA RCS
STATION_COORDS = {"CXOA": (-75.714017, 45.382369, 79.17)}  # lon, lat, elevation (m)

# SWOB property -> column; speeds in km/h until converted
FIELDS = {
    "avg_wnd_spd_10m_pst1mt": "ws_1min",
    "avg_wnd_dir_10m_pst1mt": "wd_1min",
    "max_wnd_spd_10m_pst1mt": "ws_max_1min",
    "avg_wnd_spd_10m_pst2mts": "ws_2min",
    "avg_wnd_dir_10m_pst2mts": "wd_2min",
    "avg_wnd_spd_10m_pst1hr": "ws_1h",
    "avg_wnd_dir_10m_pst1hr": "wd_1h",
    "air_temp": "air_temperature",
}
QA_CHECKED = (
    "avg_wnd_spd_10m_pst1mt",
    "avg_wnd_dir_10m_pst1mt",
    "max_wnd_spd_10m_pst1mt",
    "air_temp",
)
SPEED_COLUMNS = ("ws_1min", "ws_max_1min", "ws_2min", "ws_1h")
QA_PASSED = 100


def fetch_swob(msc_id: str, start: str, end: str, page_size: int = 5000) -> list[dict]:
    """Raw SWOB property records for one station between two ISO datetimes."""
    properties = ",".join(["date_tm-value", *FIELDS, *(f"{f}-qa" for f in QA_CHECKED)])
    rows: list[dict] = []
    offset = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=180.0) as client:
        while True:
            resp = client.get(
                ITEMS_URL,
                params={
                    "msc_id-value": msc_id,
                    "datetime": f"{start}/{end}",
                    "f": "json",
                    "limit": page_size,
                    "offset": offset,
                    "sortby": "date_tm-value",
                    "properties": properties,
                    "skipGeometry": "true",
                },
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            rows.extend(f["properties"] for f in features)
            if len(features) < page_size:
                return rows
            offset += page_size


def tidy(records: list[dict], station: str) -> pd.DataFrame:
    """One row per minute (minute-end UTC), SI units, failed-QA values set to NaN."""
    columns = ["time", "station", *FIELDS.values()]
    if not records:
        return pd.DataFrame(columns=columns)
    raw = pd.DataFrame(records)
    for prop in QA_CHECKED:
        qa = raw.get(f"{prop}-qa")
        if prop in raw and qa is not None:
            raw.loc[pd.to_numeric(qa, errors="coerce") != QA_PASSED, prop] = np.nan
    out = pd.DataFrame(
        {
            col: pd.to_numeric(raw[prop], errors="coerce") if prop in raw else np.nan
            for prop, col in FIELDS.items()
        }
    )
    for col in SPEED_COLUMNS:
        out[col] = out[col] / 3.6
    out.insert(0, "station", station)
    out.insert(0, "time", pd.to_datetime(raw["date_tm-value"], utc=True).dt.tz_localize(None))
    return out.drop_duplicates("time").sort_values("time").reset_index(drop=True)[columns]


def _day_path(archive_dir: Path, station: str, day: date) -> Path:
    return archive_dir / station / f"{day.isoformat()}.parquet"


def archive(
    archive_dir: Path = ARCHIVE_DIR,
    stations: dict[str, str] | None = None,
    today: date | None = None,
    retention_days: int = 31,
) -> list[Path]:
    """Write every complete UTC day still available from SWOB and not yet on disk."""
    today = today or datetime.now(UTC).date()
    written = []
    for station, msc_id in (stations or STATIONS).items():
        for back in range(retention_days, 0, -1):
            day = today - timedelta(days=back)
            path = _day_path(archive_dir, station, day)
            if path.exists():
                continue
            records = fetch_swob(
                msc_id,
                f"{day.isoformat()}T00:00:01Z",
                f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z",
            )
            frame = tidy(records, station)
            if frame.empty:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
            written.append(path)
    return written


def load_minutes(station: str = "CXOA", archive_dir: Path = ARCHIVE_DIR) -> pd.DataFrame:
    paths = sorted((archive_dir / station).glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(
            f"no SWOB archive for {station} in {archive_dir}; run dlr fetch-swob"
        )
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True).sort_values("time")
