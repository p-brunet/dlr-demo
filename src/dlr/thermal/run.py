"""Reference ratings: span weather, static/AAR/DLR per span and hour, attribution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from dlr.config import ConductorConfig, LineConfig, RatingConfig
from dlr.thermal.rating import (
    RatingInputs,
    aar_rating,
    build_inputs,
    dlr_rating,
    mw_per_circuit,
    sheltering_factor,
    static_rating,
)
from dlr.weather.hrdps import CORRIDOR_STORE, open_corridor
from dlr.weather.sample import SPAN_WEATHER_STORE, reference_series, sample_points, span_weather

REFERENCE_RATINGS_STORE = Path("data/interim/ratings_reference.zarr")
LINE_REFERENCE_PATH = Path("data/processed/line_ratings_reference.parquet")
SUMMARY_PATH = Path("data/processed/rating_summary.json")
WX_FIELDS = ("air_temperature", "wind_speed", "wind_direction", "ghi")
DRIVERS = ("wind_speed", "wind_angle", "ambient", "solar")


@dataclass
class Inputs:
    spans: gpd.GeoDataFrame
    towers: gpd.GeoDataFrame
    line: LineConfig
    conductor: ConductorConfig
    rating: RatingConfig

    @classmethod
    def load(cls, config_dir: Path = Path("config"), processed: Path = Path("data/processed")):
        return cls(
            spans=gpd.read_parquet(processed / "spans.geoparquet"),
            towers=gpd.read_parquet(processed / "towers.geoparquet"),
            line=LineConfig.from_yaml(config_dir / "line.yaml"),
            conductor=ConductorConfig.from_yaml(config_dir / "conductor.yaml"),
            rating=RatingConfig.from_yaml(config_dir / "rating.yaml"),
        )

    def rating_inputs(self, conductor: str | None = None, max_temp_c: float | None = None):
        return build_inputs(
            self.spans, self.towers, self.line, self.conductor, self.rating, conductor, max_temp_c
        )


def build_span_weather(inputs: Inputs, store: Path = CORRIDOR_STORE) -> xr.Dataset:
    """Forecast cube (init_time, lead_time, span) of span-level weather; cached to Zarr."""
    ds = open_corridor(store)
    s = inputs.spans
    sampled = sample_points(ds, s["mid_lon"].to_numpy(), s["mid_lat"].to_numpy()).load()
    wx = span_weather(sampled, inputs.conductor.conductor_height_m.value)
    wx = wx.assign_coords(span=s["span_id"].to_numpy())
    SPAN_WEATHER_STORE.parent.mkdir(parents=True, exist_ok=True)
    wx.to_zarr(SPAN_WEATHER_STORE, mode="w", consolidated=True)
    return wx


def open_span_weather() -> xr.Dataset:
    return xr.open_zarr(SPAN_WEATHER_STORE).load()


def _arrays(ds: xr.Dataset) -> dict[str, np.ndarray]:
    return {f: ds[f].transpose("span", "time").values for f in WX_FIELDS}


def _attribution(
    inp: RatingInputs,
    wx: dict[str, np.ndarray],
    times: np.ndarray,
    wind_factor: np.ndarray,
    rating_cfg: RatingConfig,
) -> dict[str, np.ndarray]:
    """Rating with one input at a time replaced by its static assumption."""
    a = rating_cfg.summer
    perpendicular = np.mod(np.rad2deg(inp.span_azimuth_rad) + a.wind_angle_deg.value, 360.0)
    out = {}
    swaps: dict[str, tuple[str, np.ndarray, np.ndarray | float]] = {
        "wind_speed": ("wind_speed", np.full_like(wx["wind_speed"], a.wind_speed_ms.value), 1.0),
        "wind_angle": (
            "wind_direction",
            np.broadcast_to(perpendicular, wx["wind_direction"].shape),
            wind_factor,
        ),
        "ambient": (
            "air_temperature",
            np.full_like(wx["air_temperature"], a.ambient_c.value),
            wind_factor,
        ),
    }
    for driver, (field, value, factor) in swaps.items():
        out[driver] = dlr_rating(inp, {**wx, field: np.asarray(value)}, times, factor)
    # solar: clear sky at the actual hour instead of the HRDPS cloudiness
    out["solar"] = dlr_rating(inp, wx, times, wind_factor, clear_sky=True)
    return out


def floor_attack_angle(
    direction_deg: np.ndarray, span_azimuth_rad: np.ndarray, min_deg: float
) -> np.ndarray:
    """Wind direction moved so its angle of attack on each span is at least `min_deg`."""
    azimuth = np.rad2deg(span_azimuth_rad)
    diff = np.mod(direction_deg - azimuth, 180.0)
    attack = np.minimum(diff, 180.0 - diff)
    return np.where(attack < min_deg, np.mod(azimuth + min_deg, 360.0), direction_deg)


def hourly_intertie_flow(zone: str = "PQ.AT") -> pd.Series:
    """Hourly mean flow (MW, + = export from Ontario), indexed by UTC hour ending."""
    from dlr.grid.ieso import load_flows

    flows = load_flows()
    flows = flows[flows["zone"] == zone]
    hour_ending = flows["time_utc"].dt.ceil("h").dt.tz_localize(None)
    return flows.groupby(hour_ending)["flow_mw"].mean().rename(f"{zone}_flow_mw")


def reference_ratings(inputs: Inputs, wx: xr.Dataset) -> xr.Dataset:
    """Span x hour ratings for every tier and sensitivity, from the pseudo-analysis."""
    ref = reference_series(wx).transpose("span", "time")
    times = ref["time"].values
    arrays = _arrays(ref)
    shelter = sheltering_factor(inputs.spans, inputs.rating)
    base = inputs.rating_inputs()

    data: dict[str, tuple] = {}
    dims = ("span", "time")
    data["static"] = (("span",), static_rating(base, inputs.rating))
    data["static_winter"] = (("span",), static_rating(base, inputs.rating, season="winter"))
    data["aar"] = (dims, aar_rating(base, inputs.rating, arrays["air_temperature"], times))
    data["dlr"] = (dims, dlr_rating(base, arrays, times, shelter))
    data["dlr_no_sheltering"] = (dims, dlr_rating(base, arrays, times, 1.0))
    for name, arr in _attribution(base, arrays, times, shelter, inputs.rating).items():
        data[f"dlr_swap_{name}"] = (dims, arr)

    # sensitivities: conductor size, maximum operating temperature, summer ambient
    for name in inputs.conductor.sensitivity:
        inp = inputs.rating_inputs(conductor=name)
        data[f"static_{name}"] = (("span",), static_rating(inp, inputs.rating))
        data[f"dlr_{name}"] = (dims, dlr_rating(inp, arrays, times, shelter))
    for temp in inputs.conductor.max_operating_temp_sensitivity_c:
        inp = inputs.rating_inputs(max_temp_c=temp)
        data[f"static_mot{temp:.0f}"] = (("span",), static_rating(inp, inputs.rating))
        data[f"static_mot{temp:.0f}_winter"] = (
            ("span",),
            static_rating(inp, inputs.rating, season="winter"),
        )
        data[f"dlr_mot{temp:.0f}"] = (dims, dlr_rating(inp, arrays, times, shelter))
    for amb in inputs.rating.summer_ambient_sensitivity_c:
        data[f"static_amb{amb:.0f}"] = (
            ("span",),
            static_rating(base, inputs.rating, ambient_c=amb),
        )
    for theta in inputs.rating.min_attack_angle_sensitivity_deg:
        floored = floor_attack_angle(arrays["wind_direction"], base.span_azimuth_rad, theta)
        data[f"dlr_attack{theta:.0f}"] = (
            dims,
            dlr_rating(base, {**arrays, "wind_direction": floored}, times, shelter),
        )

    out = xr.Dataset(data, coords={"span": ref["span"].values, "time": times})
    for f in WX_FIELDS:
        out[f"wx_{f}"] = ref[f]
    out.attrs = {
        "units": "A per circuit (phase bundle)",
        "summer_months": list(inputs.rating.summer_months),
        "conductor": inputs.conductor.base,
        "max_operating_temp_c": base.max_temp_c,
        "sub_conductors": base.sub_conductors,
    }
    REFERENCE_RATINGS_STORE.parent.mkdir(parents=True, exist_ok=True)
    out.to_zarr(REFERENCE_RATINGS_STORE, mode="w", consolidated=True)
    return out


def seasonal_static(
    r: xr.Dataset, key: str, times: np.ndarray, summer_months: list[int] | None = None
) -> np.ndarray:
    """Line static rating per hour: the summer or winter value by calendar month."""
    months = pd.DatetimeIndex(times).month
    summer = list(summer_months or r.attrs.get("summer_months", range(5, 11)))
    summer_line = float(r[key].min())
    winter_key = f"{key}_winter"
    winter_line = float(r[winter_key].min()) if winter_key in r else summer_line
    return np.where(np.isin(months, summer), summer_line, winter_line)


def seasonal_span_static(r: xr.Dataset, times: np.ndarray) -> np.ndarray:
    """(n_spans, n_times) static rating per span, summer or winter by month."""
    summer = r["static"].values[:, None]
    winter = r["static_winter"].values[:, None] if "static_winter" in r else summer
    months = pd.DatetimeIndex(times).month
    is_summer = np.isin(months, list(r.attrs.get("summer_months", range(5, 11))))[None, :]
    return np.where(is_summer, summer, winter)


def line_summary(r: xr.Dataset, voltage_kv: float) -> pd.DataFrame:
    """Hourly line rating (min over spans) with limiting span and dominant driver."""
    dlr = r["dlr"].values
    valid = np.isfinite(dlr).all(axis=0)
    limiting = np.argmin(np.where(np.isfinite(dlr), dlr, np.inf), axis=0)
    cols = np.arange(dlr.shape[1])
    at_limit = dlr[limiting, cols]

    # dominant driver: the input whose static substitution moves the limiting span the most
    deltas = np.stack([r[f"dlr_swap_{d}"].values[limiting, cols] - at_limit for d in DRIVERS])
    dominant = np.array(DRIVERS)[np.nanargmax(np.abs(np.nan_to_num(deltas)), axis=0)]

    times = r["time"].values
    df = pd.DataFrame(
        {
            "time": times,
            "static_a": seasonal_static(r, "static", times),
            "aar_a": r["aar"].min("span").values,
            "dlr_a": np.where(valid, at_limit, np.nan),
            "dlr_no_sheltering_a": r["dlr_no_sheltering"].min("span").values,
            "limiting_span": pd.Series(r["span"].values[limiting]).where(valid),
            "dominant_driver": pd.Series(dominant).where(valid),
        }
    )
    for d in DRIVERS:
        df[f"delta_{d}_a"] = np.where(valid, -deltas[DRIVERS.index(d)], np.nan)
    for name in [v for v in r.data_vars if str(v).startswith("dlr_attack")]:
        df[f"{name}_a"] = r[name].min("span").values
    if "static_mot127" in r:
        df["static_lte_a"] = seasonal_static(r, "static_mot127", times)
        df["dlr_lte_a"] = r["dlr_mot127"].min("span").values
    for col in [c for c in df.columns if c.endswith("_a") and not c.startswith("delta_")]:
        df[col[:-2] + "_mw_per_circuit"] = mw_per_circuit(df[col], voltage_kv)
    flow = hourly_intertie_flow()
    df = df.merge(flow, left_on="time", right_index=True, how="left")
    return df


def summarize(r: xr.Dataset, line_df: pd.DataFrame, inputs: Inputs) -> dict:
    ok = line_df["dlr_a"].notna()
    ratio = (line_df.loc[ok, "dlr_a"] / line_df.loc[ok, "static_a"]).to_numpy()
    ratio_ns = (line_df.loc[ok, "dlr_no_sheltering_a"] / line_df.loc[ok, "static_a"]).to_numpy()
    aar_ratio = (line_df["aar_a"] / line_df["static_a"]).to_numpy()
    below = line_df.loc[ok & (line_df["dlr_a"] < line_df["static_a"])]

    def q(a: np.ndarray, p: float) -> float:
        return round(float(np.percentile(a, p)), 3)

    sens = {}
    for name in inputs.conductor.sensitivity:
        s_line = float(r[f"static_{name}"].min())
        d_line = r[f"dlr_{name}"].min("span").values[ok.to_numpy()]
        sens[name] = {
            "static_a": round(s_line),
            "dlr_over_static_median": q(d_line / s_line, 50),
            "dlr_below_static_share": round(float(np.mean(d_line < s_line)), 3),
        }
    for temp in inputs.conductor.max_operating_temp_sensitivity_c:
        key = f"mot{temp:.0f}"
        s_line = float(r[f"static_{key}"].min())
        d_line = r[f"dlr_{key}"].min("span").values[ok.to_numpy()]
        sens[key] = {
            "static_a": round(s_line),
            "dlr_over_static_median": q(d_line / s_line, 50),
            "dlr_below_static_share": round(float(np.mean(d_line < s_line)), 3),
        }
    for amb in inputs.rating.summer_ambient_sensitivity_c:
        sens[f"static_ambient_{amb:.0f}c"] = {
            "static_a": round(float(r[f"static_amb{amb:.0f}"].min()))
        }

    for theta in inputs.rating.min_attack_angle_sensitivity_deg:
        key = f"attack{theta:.0f}"
        d_line = r[f"dlr_{key}"].min("span").values[ok.to_numpy()]
        s_line = float(r["static"].min())
        sens[f"min_attack_angle_{theta:.0f}deg"] = {
            "dlr_over_static_median": q(d_line / s_line, 50),
            "dlr_below_static_share": round(float(np.mean(d_line < s_line)), 3),
        }

    aar_vs_dlr = {
        "share_aar_above_dlr": round(
            float(np.mean(line_df.loc[ok, "aar_a"] > line_df.loc[ok, "dlr_a"])), 3
        ),
    }
    n1 = {}
    if "dlr_lte_mw_per_circuit" in line_df and "PQ.AT_flow_mw" in line_df:
        f = line_df.dropna(subset=["PQ.AT_flow_mw", "dlr_lte_mw_per_circuit"])
        flow = f["PQ.AT_flow_mw"].abs()
        static_lte = f["static_lte_mw_per_circuit"]
        n1 = {
            "hours_with_flow": int(len(f)),
            "static_lte_mw_single_circuit": round(float(static_lte.iloc[0])),
            "dlr_lte_mw_single_circuit_median": round(float(f["dlr_lte_mw_per_circuit"].median())),
            "share_flow_above_static_lte": round(float(np.mean(flow > static_lte)), 3),
            "share_flow_above_dlr_lte": round(
                float(np.mean(flow > f["dlr_lte_mw_per_circuit"])), 3
            ),
            "share_covered_by_dlr_not_static": round(
                float(np.mean((flow > static_lte) & (flow <= f["dlr_lte_mw_per_circuit"]))), 3
            ),
            "flow_abs_median_mw": round(float(flow.median())),
        }

    limiting_share = line_df.loc[ok, "limiting_span"].value_counts(normalize=True)
    voltage = inputs.line.voltage_kv.value
    static_a = float(r["static"].min())  # summer value; winter is reported separately
    static_winter_a = float(r["static_winter"].min()) if "static_winter" in r else static_a
    return {
        "period": [str(line_df["time"].min()), str(line_df["time"].max())],
        "hours": int(ok.sum()),
        "conductor": f"2 x {inputs.conductor.spec().code_word} per phase",
        "max_operating_temp_c": inputs.conductor.max_operating_temp_c.value,
        "static_line_a": round(static_a),
        "static_line_mw_per_circuit": round(float(mw_per_circuit(static_a, voltage))),
        "static_line_winter_a": round(static_winter_a),
        "static_line_winter_mw_per_circuit": round(float(mw_per_circuit(static_winter_a, voltage))),
        "static_span_range_a": [round(float(r["static"].min())), round(float(r["static"].max()))],
        "aar_over_static": {
            "median": q(aar_ratio, 50),
            "p5": q(aar_ratio, 5),
            "min": q(aar_ratio, 0),
        },
        "dlr_over_static": {
            "median": q(ratio, 50),
            "p5": q(ratio, 5),
            "p95": q(ratio, 95),
            "share_above_1": round(float(np.mean(ratio > 1)), 3),
            "share_above_1_2": round(float(np.mean(ratio > 1.2)), 3),
        },
        "dlr_no_sheltering_over_static": {
            "median": q(ratio_ns, 50),
            "p5": q(ratio_ns, 5),
            "share_below_1": round(float(np.mean(ratio_ns < 1)), 3),
        },
        "dlr_below_static": {
            "share_of_hours": round(float(len(below) / ok.sum()), 3),
            "dominant_driver_share": below["dominant_driver"]
            .value_counts(normalize=True)
            .round(3)
            .to_dict(),
            "median_wind_at_limiting_span_ms": None,
        },
        "aar_vs_dlr": aar_vs_dlr,
        "n_minus_1_pq_at": n1,
        "limiting_span_top5": limiting_share.head(5).round(3).to_dict(),
        "spans_ever_limiting": int(limiting_share.size),
        "sensitivity": sens,
    }


def run_reference(store: Path = CORRIDOR_STORE, rebuild_weather: bool = True) -> dict:
    inputs = Inputs.load()
    wx = build_span_weather(inputs, store) if rebuild_weather else open_span_weather()
    r = reference_ratings(inputs, wx)
    line_df = line_summary(r, inputs.line.voltage_kv.value)
    # wind actually seen by the limiting span in hours below static
    lim = line_df["limiting_span"]
    below = line_df["dlr_a"] < line_df["static_a"]
    span_index = {s: i for i, s in enumerate(r["span"].values)}
    idx = [(span_index[s], t) for t, s in enumerate(lim) if s is not None and below.iloc[t]]
    summary = summarize(r, line_df, inputs)
    if idx:
        ws = r["wx_wind_speed"].transpose("span", "time").values
        summary["dlr_below_static"]["median_wind_at_limiting_span_ms"] = round(
            float(np.median([ws[i, t] for i, t in idx])), 2
        )
    LINE_REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line_df.to_parquet(LINE_REFERENCE_PATH, index=False)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
