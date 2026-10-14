"""M6 exports: data behind the published story map, plus reusable open formats.

`site/` is a static site (GitHub Pages). `site/index.html` is hand-written;
everything under `site/data/` is generated here (by CI on every new HRDPS run,
or locally with `dlr export`):

- history viewer files: `spans.geojson`, `towers.geojson`, `line_hourly.json`,
  `span_ratio.bin` (Uint16, DLR / seasonal static x 1000, hour-major, span
  order of `spans.geojson`; 0 = missing), `span_wind.bin` (Uint8 pairs per hour
  and span: wind speed at conductor height x 10 m/s, direction / 2 degrees;
  255 = missing) and `wind_grid.bin` + `wind_grid.json` (Int8 u/v of the 10 m
  wind on a regular lon/lat grid, per hour, for the particle overlay);
- live forecast files in `forecast/`: the same three binaries per lead hour of
  the newest HRDPS run, plus `forecast.json` (line forecast, secure rating,
  margins, static);
- reuse/GeoLibre files: `line.geoparquet`, `spans.geoparquet`,
  `towers.geoparquet`, `span_ratings_hourly.parquet`,
  `line_ratings_hourly.parquet`, `forecast_line_dlr.zarr`;
- `manifest.json`: provenance, licences/attributions and headline numbers.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from dlr.config import StudyConfig
from dlr.thermal.rating import mw_per_circuit
from dlr.thermal.run import (
    LINE_REFERENCE_PATH,
    REFERENCE_RATINGS_STORE,
    SUMMARY_PATH,
    seasonal_span_static,
    seasonal_static,
)
from dlr.verify.forecast import FORECAST_STORE, operational_margins
from dlr.verify.run import OUT_DIR as VERIFY_DIR
from dlr.weather.hrdps import open_corridor
from dlr.weather.sample import SPAN_WEATHER_STORE, reference_series, sample_points

SITE_DIR = Path("site")
DATA_DIR = SITE_DIR / "data"
LIVE_DIR = DATA_DIR / "forecast"
PROCESSED = Path("data/processed")
DAY_AHEAD_LEADS_H = (24, 29)  # one init per valid hour with 6-hourly inits
LIVE_LEADS_H = np.arange(1, 49)
WIND_GRID_STEP_DEG = 0.02
WIND_SCALE_MS = 0.15  # m/s per Int8 unit, +/-19 m/s

ATTRIBUTIONS = {
    "line_towers_spans": "© OpenStreetMap contributors, ODbL 1.0 "
    "(derived span/tower data are shared under the same licence)",
    "weather_forecast": "ECCC HRDPS data processed by dynamical.org from Environment and "
    "Climate Change Canada, used under the ECCC Data Servers End-use Licence version 2.1 "
    "(https://eccc-msc.github.io/open-data/licence/readme_en/)",
    "station_observations": "Contains information licenced under the Data Server End-use "
    "Licence of Environment and Climate Change Canada.",
    "terrain": "Contains information licensed under the Open Government Licence - Canada "
    "(HRDEM, Natural Resources Canada)",
    "intertie_flows": "IESO Intertie Schedule and Flow public report "
    "(https://reports-public.ieso.ca/public/IntertieScheduleFlow/); terms TODO(verify)",
    "basemap": "OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors",
}


def _day_ahead(forecast: xr.Dataset, margins: pd.Series) -> pd.DataFrame:
    df = forecast[["line_dlr"]].to_dataframe().reset_index()
    df["lead_h"] = (df["lead_time"] / np.timedelta64(1, "h")).astype(int)
    df = df[df["lead_h"].between(*DAY_AHEAD_LEADS_H)].dropna(subset=["line_dlr"])
    df["secure_a"] = df["line_dlr"] / (1.0 + df["lead_h"].map(margins))
    return df.groupby("valid_time")[["line_dlr", "secure_a"]].first()


def _round_list(values: pd.Series | np.ndarray) -> list:
    arr = np.asarray(values, dtype=float)
    return [None if not np.isfinite(v) else int(round(v)) for v in arr]


def _iso(t: np.datetime64 | pd.Timestamp) -> str:
    return pd.Timestamp(t).isoformat() + "Z"


# ---------- packing helpers (shared by the history and live exports) ----------


def _pack_ratio(ratio: np.ndarray, path: Path) -> None:
    """(n_spans, n_steps) ratio -> Uint16 x1000, step-major; 0 = missing."""
    packed = np.where(np.isfinite(ratio), np.clip(np.round(ratio * 1000), 1, 65535), 0)
    packed.T.astype("<u2").tofile(path)


def _pack_span_wind(speed: np.ndarray, direction: np.ndarray, path: Path) -> None:
    """(n_steps, n_spans) speed (m/s) and direction (deg) -> Uint8 pairs; 255 = missing."""
    s = np.where(np.isfinite(speed), np.clip(np.round(speed * 10), 0, 254), 255)
    d = np.where(np.isfinite(direction), np.round(np.mod(direction, 360.0) / 2) % 180, 255)
    np.stack([s, d], axis=-1).astype("u1").tofile(path)


def _pack_wind(speed: np.ndarray, direction: np.ndarray, path: Path) -> None:
    """(n_steps, n_points) 10 m wind -> Int8 (u east, v north) pairs."""
    rad = np.deg2rad(direction)
    u, v = -speed * np.sin(rad), -speed * np.cos(rad)
    packed = np.stack([u, v], axis=-1) / WIND_SCALE_MS
    np.clip(np.nan_to_num(np.round(packed)), -127, 127).astype("i1").tofile(path)


def _wind_grid_points(spans: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """Regular lon/lat grid: the line's extent plus a margin, one step inside the bbox
    (the rotated HRDPS cells do not cover the lon/lat bbox corners)."""
    bbox = StudyConfig.from_yaml(Path("config/study.yaml")).bbox
    lon0, lat0, lon1, lat1 = spans.total_bounds
    m, inset = 0.04, WIND_GRID_STEP_DEG
    lon0, lat0 = max(lon0 - m, bbox.min_lon + inset), max(lat0 - m, bbox.min_lat + inset)
    lon1, lat1 = min(lon1 + m, bbox.max_lon - inset), min(lat1 + m, bbox.max_lat - inset)
    lons = np.arange(lon0, lon1 + 1e-9, WIND_GRID_STEP_DEG)
    lats = np.arange(lat0, lat1 + 1e-9, WIND_GRID_STEP_DEG)
    glon, glat = np.meshgrid(lons, lats)  # row-major: lat rows (south to north), lon columns
    meta = {
        "lon0": float(lons[0]),
        "lat0": float(lats[0]),
        "step": WIND_GRID_STEP_DEG,
        "nx": len(lons),
        "ny": len(lats),
        "scale_ms": WIND_SCALE_MS,
        "layout": "step-major, then lat rows (south to north), lon columns, then (u, v)",
        "height_m": 10,
    }
    return glon.ravel(), glat.ravel(), meta


# ---------- live forecast ----------


def newest_init(forecast: xr.Dataset, min_leads: int = 40) -> np.datetime64:
    """Newest init whose line forecast covers at least `min_leads` lead hours."""
    complete = forecast["line_dlr"].notnull().sum("lead_time") >= min_leads
    inits = forecast["init_time"].values[complete.values]
    if inits.size == 0:
        raise ValueError("no complete forecast run available")
    return inits.max()


def build_live(
    forecast: xr.Dataset,
    ratings: xr.Dataset,
    ref: pd.DataFrame,
    spans: gpd.GeoDataFrame,
    voltage: float,
    today: str,
) -> dict:
    """Write site/data/forecast/ for the newest run; return its summary."""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    init = newest_init(forecast)
    leads = LIVE_LEADS_H.astype("timedelta64[h]")
    run = forecast.sel(init_time=init, lead_time=leads)
    valid = (init + leads).astype("datetime64[ns]")
    span_order = spans["span_id"].tolist()

    margins = operational_margins(forecast, ref, today)
    margin = np.array([margins["margins"].get(str(int(h)), np.nan) for h in LIVE_LEADS_H])

    static_line = seasonal_static(ratings, "static", valid)
    static_span = seasonal_span_static(ratings.sel(span=span_order), valid)
    dlr = run["dlr"].sel(span=span_order).transpose("span", "lead_time").values
    line_dlr = run["line_dlr"].values
    finite = np.isfinite(dlr)
    limiting = np.where(finite.any(axis=0), np.where(finite, dlr, np.inf).argmin(axis=0), -1)
    _pack_ratio(dlr / static_span, LIVE_DIR / "span_ratio.bin")

    wx = xr.open_zarr(SPAN_WEATHER_STORE).sel(init_time=init, lead_time=leads)
    wx = wx.sel(span=span_order).transpose("lead_time", "span")
    _pack_span_wind(
        wx["wind_speed"].values, wx["wind_direction"].values, LIVE_DIR / "span_wind.bin"
    )

    lon, lat, grid = _wind_grid_points(spans)
    corridor = open_corridor()[["wind_speed_10m", "wind_direction_10m"]]
    grid_wx = sample_points(
        corridor.sel(init_time=init, lead_time=leads), lon, lat, point_dim="point"
    ).load()
    _pack_wind(
        grid_wx["wind_speed_10m"].transpose("lead_time", "point").values,
        grid_wx["wind_direction_10m"].transpose("lead_time", "point").values,
        LIVE_DIR / "wind_grid.bin",
    )

    def mw(a: np.ndarray) -> np.ndarray:
        return mw_per_circuit(np.asarray(a, dtype=float), voltage)

    secure = line_dlr / (1.0 + margin)
    summary = {
        "init_utc": _iso(init),
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "t0": _iso(valid[0]),
        "step_h": 1,
        "n": len(LIVE_LEADS_H),
        "lead_h": LIVE_LEADS_H.tolist(),
        "static_mw": _round_list(mw(static_line)),
        "dlr_mw": _round_list(mw(line_dlr)),
        "secure_mw": _round_list(mw(secure)),
        "margin": [None if not np.isfinite(m) else round(float(m), 4) for m in margin],
        "limiting_idx": [None if i < 0 else int(i) for i in limiting],
        "margins_fitted_on": margins["fitted_on"],
        "margins_n_pairs": margins["n_pairs"],
        "confidence": margins["confidence"],
        "grid": {
            **grid,
            "n_steps": len(LIVE_LEADS_H),
            "source": f"HRDPS run {_iso(init)}, dynamical.org",
        },
    }
    (LIVE_DIR / "forecast.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


# ---------- full site export ----------


def build_site() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    spans = gpd.read_parquet(PROCESSED / "spans.geoparquet")
    towers = gpd.read_parquet(PROCESSED / "towers.geoparquet")
    line = gpd.read_parquet(PROCESSED / "line.geoparquet")
    ref = pd.read_parquet(LINE_REFERENCE_PATH)
    ratings = xr.open_zarr(REFERENCE_RATINGS_STORE).load()
    forecast = xr.open_zarr(FORECAST_STORE).load()
    margins = pd.read_csv(VERIFY_DIR / "margins_base.csv", index_col=0).iloc[:, 0]
    rating_summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    verify_summary = json.loads((VERIFY_DIR / "summary.json").read_text(encoding="utf-8"))
    voltage = float(line["voltage_kv"].iloc[0])

    times = pd.DatetimeIndex(ref["time"])
    if not (times.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("reference series is not contiguous hourly")

    # per-span summary attributes; ratios use the seasonal (summer/winter) static
    span_order = spans["span_id"].tolist()
    dlr = ratings["dlr"].sel(span=span_order).transpose("span", "time").values
    ratio = dlr / seasonal_span_static(ratings.sel(span=span_order), times.values)
    limiting_share = ref["limiting_span"].value_counts(normalize=True)
    spans = spans.assign(
        limiting_share=spans["span_id"].map(limiting_share).fillna(0.0).round(4),
        ratio_median=np.round(np.nanmedian(ratio, axis=1), 3),
        ratio_p5=np.round(np.nanpercentile(ratio, 5, axis=1), 3),
        static_a=np.round(ratings["static"].sel(span=span_order).values, 0),
        static_winter_a=np.round(ratings["static_winter"].sel(span=span_order).values, 0),
    )
    keep = [
        "span_id",
        "span_seq",
        "region",
        "length_m",
        "axis_deg",
        "crosses_river",
        "exposure_index",
        "obstacle_p90_m",
        "inclination_deg",
        "limiting_share",
        "ratio_median",
        "ratio_p5",
        "static_a",
        "static_winter_a",
        "geometry",
    ]
    spans_out = spans[keep].copy()
    for col in ("length_m", "axis_deg", "exposure_index", "obstacle_p90_m", "inclination_deg"):
        spans_out[col] = spans_out[col].round(3)
    spans_out.to_parquet(DATA_DIR / "spans.geoparquet")
    (DATA_DIR / "spans.geojson").write_text(spans_out.to_json(drop_id=True), encoding="utf-8")
    towers_out = towers[
        [
            "tower_seq",
            "osm_node_id",
            "chainage_km",
            "ground_alt_m",
            "dsm_height_above_ground_m",
            "geometry",
        ]
    ].round(2)
    towers_out.to_parquet(DATA_DIR / "towers.geoparquet")
    (DATA_DIR / "towers.geojson").write_text(towers_out.to_json(drop_id=True), encoding="utf-8")
    line.to_parquet(DATA_DIR / "line.geoparquet")

    # hourly span ratios and wind for the history viewer
    _pack_ratio(ratio, DATA_DIR / "span_ratio.bin")
    _pack_span_wind(
        ratings["wx_wind_speed"].sel(span=span_order).transpose("time", "span").values,
        ratings["wx_wind_direction"].sel(span=span_order).transpose("time", "span").values,
        DATA_DIR / "span_wind.bin",
    )
    lon, lat, grid = _wind_grid_points(spans)
    corridor = open_corridor()[["wind_speed_10m", "wind_direction_10m"]]
    grid_ref = reference_series(sample_points(corridor, lon, lat, point_dim="point").load()).sel(
        time=times
    )
    _pack_wind(
        grid_ref["wind_speed_10m"].transpose("time", "point").values,
        grid_ref["wind_direction_10m"].transpose("time", "point").values,
        DATA_DIR / "wind_grid.bin",
    )
    grid_meta = {**grid, "n_hours": len(times), "source": "HRDPS reference (leads 1-6 h)"}
    (DATA_DIR / "wind_grid.json").write_text(json.dumps(grid_meta), encoding="utf-8")

    # long tables for reuse
    long = (
        ratings[["dlr", "aar"]]
        .to_dataframe()
        .reset_index()
        .rename(columns={"dlr": "dlr_a", "aar": "aar_a", "span": "span_id"})
    )
    static_long = (
        pd.DataFrame(
            seasonal_span_static(ratings, times.values),
            index=ratings["span"].values,
            columns=times,
        )
        .stack()
        .rename("static_a")
    )
    long = long.join(static_long, on=["span_id", "time"])
    long = long.astype({"dlr_a": "float32", "aar_a": "float32", "static_a": "float32"})
    long.to_parquet(DATA_DIR / "span_ratings_hourly.parquet", index=False)
    ref.to_parquet(DATA_DIR / "line_ratings_hourly.parquet", index=False)
    zarr_path = DATA_DIR / "forecast_line_dlr.zarr"
    if zarr_path.exists():
        shutil.rmtree(zarr_path)
    forecast[[v for v in forecast.data_vars if str(v).startswith("line_")]].to_zarr(
        zarr_path, mode="w", consolidated=True
    )

    # line-level hourly series for the history chart (MW per circuit)
    day_ahead = _day_ahead(forecast, margins).reindex(times)
    # the margin is fitted before the test period: show the secure series out-of-sample only
    test_start = pd.Timestamp(verify_summary["test_period"].split(">= ")[-1])
    day_ahead.loc[day_ahead.index < test_start, "secure_a"] = np.nan
    flow_per_circuit = ref["PQ.AT_flow_mw"].abs() / 2 if "PQ.AT_flow_mw" in ref else None
    limiting_idx = ref["limiting_span"].map({s: i for i, s in enumerate(span_order)})

    def mw(a):
        return mw_per_circuit(np.asarray(a, dtype=float), voltage)

    hourly = {
        "t0": _iso(times[0]),
        "step_h": 1,
        "n": len(times),
        "static_mw": _round_list(mw(ref["static_a"])),
        "aar_mw": _round_list(mw(ref["aar_a"])),
        "dlr_mw": _round_list(mw(ref["dlr_a"])),
        "dlr_attack20_mw": _round_list(mw(ref["dlr_attack20_a"]))
        if "dlr_attack20_a" in ref
        else None,
        "dayahead_mw": _round_list(mw(day_ahead["line_dlr"])),
        "dayahead_secure_mw": _round_list(mw(day_ahead["secure_a"])),
        "flow_per_circuit_mw": _round_list(flow_per_circuit)
        if flow_per_circuit is not None
        else None,
        "limiting_idx": [None if pd.isna(v) else int(v) for v in limiting_idx],
        "dominant_driver": [None if pd.isna(v) else str(v) for v in ref["dominant_driver"]],
        "test_start": verify_summary["test_period"].split(">= ")[-1],
    }
    (DATA_DIR / "line_hourly.json").write_text(json.dumps(hourly), encoding="utf-8")

    live = build_live(forecast, ratings, ref, spans, voltage, datetime.now(UTC).date().isoformat())

    manifest = {
        "title": "Dynamic Line Rating demonstrator: A41T/A42T, Hawthorne TS - Poste de l'Outaouais",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "repository": "https://github.com/p-brunet/dlr-demo",
        "software": {
            pkg: version(pkg)
            for pkg in ("linerate", "xarray", "geopandas", "dynamical-catalog", "dlr")
        },
        "line": {
            "osm_way_ids": line["route_osm_way_ids"].iloc[0],
            "osm_timestamp": line["osm_timestamp"].iloc[0],
            "length_km": float(line["length_km"].iloc[0]),
            "towers": int(line["tower_count"].iloc[0]),
            "spans": len(spans_out),
            "voltage_kv": voltage,
        },
        "assumptions": {
            "conductor": rating_summary["conductor"],
            "max_operating_temp_c": rating_summary["max_operating_temp_c"],
            "static": "summer (May-Oct): 35 °C, winter: 10 °C; 1.11 m/s perpendicular wind; "
            "clear sky at solar noon on 21 June / 21 December",
            "note": "Conductor and static assumptions are typical public Ontario values, "
            "not the operator's; ratings are indicative.",
        },
        "ratings": rating_summary,
        "verification": verify_summary,
        "forecast": {
            k: live[k]
            for k in (
                "init_utc",
                "generated_utc",
                "margins_fitted_on",
                "margins_n_pairs",
                "confidence",
            )
        },
        "files": sorted(p.name for p in DATA_DIR.iterdir()),
        "attributions": ATTRIBUTIONS,
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
