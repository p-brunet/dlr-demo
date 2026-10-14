"""Wind-forecast check with minute observations (SWOB, Ottawa CDA RCS).

Three questions the hourly station check (`dlr.verify.stations`) cannot answer:

1. **Fair wind skill.** Hourly climate observations are 2-minute snapshots;
   HRDPS winds are hourly model values. Verify against the centred 60-minute
   mean as well as the snapshot, to separate sampling noise from forecast
   error.
2. **Intra-hour direction spread.** The circular standard deviation of the
   1-minute directions in each hour, by wind speed.
3. **Effective cooling.** At a "virtual copy of the line" at the station (the
   110 real span azimuths, same conductor and conductor-height shear), compare
   - `R_vector`: the rating from the hourly-mean wind vector (what an hourly
     model represents), and
   - `R_effective`: the rating with CIGRE convective cooling computed minute by
     minute and averaged over the hour (thermal inertia smooths minute-scale
     changes), solved in closed form `I = n * sqrt((Pc + Pr - Ps) / R(Tmax))`
     (valid because the magnetic-effect terms are neutral);
   and fit the attack-angle floor that makes `R_vector` match `R_effective`.

The station's 98 % margin is then recomputed with `R_effective` as truth.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from linerate import Weather

from dlr.thermal.rating import Cigre601MeasuredGHI, RatingInputs, dlr_rating
from dlr.thermal.run import Inputs, floor_attack_angle
from dlr.verify.forecast import FIRST_SCORED_LEAD_H
from dlr.verify.stations import station_forecasts, virtual_span_inputs
from dlr.weather.hrdps import open_corridor
from dlr.weather.sample import reference_series
from dlr.weather.swob import STATION_COORDS, load_minutes

OUT_DIR = Path("data/processed/verification")
STATION = "CXOA"
MIN_MINUTES = 45
CALM_MS = 0.1
LOW_WIND_MS = 2.0
SPEED_BINS = [0.0, 1.0, 2.0, 3.0, 5.0, 30.0]
FLOOR_GRID_DEG = np.arange(0.0, 61.0, 1.0)
CONFIDENCE = 0.98


def _uv(speed: np.ndarray, direction_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rad = np.deg2rad(direction_deg)
    return -speed * np.sin(rad), -speed * np.cos(rad)


def _direction(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.mod(np.rad2deg(np.arctan2(-u, -v)), 360.0)


def circular_std_deg(direction_deg: np.ndarray, axis: int = -1) -> np.ndarray:
    """Circular standard deviation (degrees), ignoring NaN."""
    rad = np.deg2rad(direction_deg)
    with warnings.catch_warnings():  # hours with no moving-air minute give NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        r = np.hypot(np.nanmean(np.sin(rad), axis=axis), np.nanmean(np.cos(rad), axis=axis))
    return np.rad2deg(np.sqrt(-2.0 * np.log(np.clip(r, 1e-12, 1.0))))


def line_min(ratings: np.ndarray) -> np.ndarray:
    """Minimum over spans (axis 0); NaN where no span has a value."""
    finite = np.isfinite(ratings)
    return np.where(finite.any(axis=0), np.where(finite, ratings, np.inf).min(axis=0), np.nan)


def minute_matrix(minutes: pd.DataFrame, window: str = "ending") -> tuple[pd.DatetimeIndex, dict]:
    """(hours, 60) arrays of 1-minute observations for each full hour.

    `window="ending"`: minutes in (H-60, H]; `"centred"`: (H-30, H+30].
    Hours with fewer than MIN_MINUTES valid minutes are dropped.
    """
    m = minutes.set_index("time").sort_index()
    m = m[~m.index.duplicated()]
    # every candidate hour; incomplete ones are dropped by the MIN_MINUTES rule below
    hours = pd.date_range(
        m.index.min().floor("h") + pd.Timedelta(hours=1), m.index.max().ceil("h"), freq="h"
    )
    offset = 0 if window == "ending" else 30
    grid = hours.values[:, None] + (np.arange(-59, 1) + offset).astype("timedelta64[m]")
    full = m.reindex(pd.DatetimeIndex(grid.ravel()))
    arrays = {
        col: full[col].to_numpy(dtype=float).reshape(len(hours), 60)
        for col in ("ws_1min", "wd_1min", "air_temperature")
    }
    # calm minutes have no meaningful direction; any value works for the physics
    calm = arrays["ws_1min"] <= CALM_MS
    arrays["wd_1min"] = np.where(calm & np.isnan(arrays["wd_1min"]), 0.0, arrays["wd_1min"])
    valid = np.isfinite(arrays["ws_1min"]) & np.isfinite(arrays["wd_1min"])
    keep = valid.sum(axis=1) >= MIN_MINUTES
    for col in arrays:
        arrays[col] = np.where(valid, arrays[col], np.nan)[keep]
    snapshot = m.reindex(hours[keep])[["ws_2min", "wd_2min"]]
    arrays["ws_snapshot"] = snapshot["ws_2min"].to_numpy(dtype=float)
    arrays["wd_snapshot"] = snapshot["wd_2min"].to_numpy(dtype=float)
    return hours[keep], arrays


def hourly_summary(hours: pd.DatetimeIndex, a: dict) -> pd.DataFrame:
    u, v = _uv(a["ws_1min"], a["wd_1min"])
    moving = np.where(a["ws_1min"] >= 0.5, a["wd_1min"], np.nan)
    return pd.DataFrame(
        {
            "ws_mean": np.nanmean(a["ws_1min"], axis=1),
            "wd_vector": _direction(np.nanmean(u, axis=1), np.nanmean(v, axis=1)),
            "dir_spread_deg": circular_std_deg(moving),
            "air_temperature": np.nanmean(a["air_temperature"], axis=1),
            "ws_snapshot": a["ws_snapshot"],
            "wd_snapshot": a["wd_snapshot"],
        },
        index=hours,
    )


def _shear(inputs: Inputs) -> float:
    return (inputs.conductor.conductor_height_m.value / 10.0) ** (1 / 7)


def _mid_hour(hours: pd.DatetimeIndex) -> np.ndarray:
    return (hours.values - np.timedelta64(30, "m")).astype("datetime64[m]")


def vector_ratings(
    inp: RatingInputs,
    speed: np.ndarray,
    direction: np.ndarray,
    temperature: np.ndarray,
    ghi: np.ndarray,
    hours: pd.DatetimeIndex,
) -> np.ndarray:
    """(n_spans, n_hours) ratings from one wind vector per hour (speed at conductor height)."""
    n = inp.span_azimuth_rad.shape[0]
    tile = lambda x: np.tile(np.asarray(x, dtype=float), (n, 1))  # noqa: E731
    wx = {
        "air_temperature": tile(temperature),
        "wind_speed": tile(speed),
        "wind_direction": np.broadcast_to(direction, (n, len(hours))).copy()
        if np.ndim(direction) == 2
        else tile(direction),
        "ghi": tile(ghi),
    }
    return dlr_rating(inp, wx, hours.values)


def effective_ratings(
    inp3: RatingInputs,
    speed_min: np.ndarray,
    dir_min: np.ndarray,
    temperature: np.ndarray,
    ghi: np.ndarray,
    hours: pd.DatetimeIndex,
    block_minutes: int | None = None,
) -> np.ndarray:
    """(n_spans, n_hours) ratings with convective cooling averaged over the minutes.

    `inp3` must carry tower arrays shaped (n_spans, 1, 1). With `block_minutes`,
    cooling is averaged per block and the weakest block is kept (conservative).
    """
    weather = Weather(
        air_temperature=temperature[None, :, None],
        wind_direction=np.deg2rad(np.nan_to_num(dir_min))[None],
        wind_speed=np.nan_to_num(speed_min)[None],
        ground_albedo=inp3.ground_albedo,
        clearness_ratio=1.0,
    )
    model = Cigre601MeasuredGHI(
        inp3.span, weather, _mid_hour(hours)[None, :, None], ghi[None, :, None]
    )
    t_max = inp3.max_temp_c
    pc = np.asarray(model.compute_convective_cooling(t_max), dtype=float)
    pc = np.where(np.isfinite(speed_min)[None], pc, np.nan)
    if block_minutes:
        blocks = pc.reshape(pc.shape[0], pc.shape[1], -1, block_minutes)
        pc_hour = np.nanmin(np.nanmean(blocks, axis=-1), axis=-1)
    else:
        pc_hour = np.nanmean(pc, axis=-1)
    pr = np.asarray(model.compute_radiative_cooling(t_max), dtype=float)[..., 0]
    ps = np.asarray(model.compute_solar_heating(), dtype=float)[..., 0]
    resistance = float(np.asarray(model.compute_resistance(t_max, np.nan)).ravel()[0])
    heat = np.clip(pc_hour + pr - ps, 0.0, None)
    return inp3.sub_conductors * np.sqrt(heat / resistance)


def fit_floor(
    vector_line_by_floor: dict[float, np.ndarray],
    effective_line: np.ndarray,
    mask: np.ndarray | None = None,
) -> tuple[float, pd.Series]:
    """Attack-angle floor whose hourly-vector line rating matches the effective one in median."""
    mask = np.ones_like(effective_line, dtype=bool) if mask is None else mask
    ratios = pd.Series(
        {
            theta: float(np.nanmedian(line[mask] / effective_line[mask]))
            for theta, line in vector_line_by_floor.items()
        }
    )
    thetas, values = ratios.index.to_numpy(), ratios.to_numpy()
    if values[0] >= 1.0:
        return 0.0, ratios
    above = np.nonzero(values >= 1.0)[0]
    if above.size == 0:
        return float(thetas[-1]), ratios
    k = above[0]
    t0, t1, r0, r1 = thetas[k - 1], thetas[k], values[k - 1], values[k]
    return float(t0 + (1.0 - r0) * (t1 - t0) / (r1 - r0)), ratios


def _skill(err: pd.Series) -> dict:
    return {
        "n": int(err.notna().sum()),
        "bias": round(float(err.mean()), 3),
        "mae": round(float(err.abs().mean()), 3),
    }


def _circ_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    d = np.abs(np.mod(a - b, 360.0))
    return pd.Series(np.minimum(d, 360.0 - d), index=a.index)


def run_wind_check() -> dict:
    inputs = Inputs.load()
    minutes = load_minutes(STATION)
    lon, lat, alt = STATION_COORDS[STATION]
    shear = _shear(inputs)
    azimuths = inputs.spans["azimuth_deg"].to_numpy()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # HRDPS at the station, and its reference (pseudo-analysis) GHI for the sun term
    corridor = open_corridor()
    obs_point = pd.DataFrame({"station": [STATION], "lon": [lon], "lat": [lat]})
    fc = station_forecasts(corridor, obs_point)
    ghi_ref = (
        reference_series(
            fc[["downward_short_wave_radiation_flux_surface"]].rename(
                {"downward_short_wave_radiation_flux_surface": "ghi"}
            )
        )["ghi"]
        .sel(station=STATION)
        .to_series()
    )

    hours, a = minute_matrix(minutes, "ending")
    hourly = hourly_summary(hours, a)
    hourly["ghi"] = ghi_ref.reindex(hours).to_numpy()
    ok = hourly["ghi"].notna().to_numpy()
    hours, hourly = hours[ok], hourly[ok]
    a = {k: v[ok] for k, v in a.items()}

    # 1. fair weather skill: forecast vs centred 60-min mean and vs the 2-min snapshot
    c_hours, c = minute_matrix(minutes, "centred")
    centred = hourly_summary(c_hours, c)[["ws_mean", "wd_vector"]]
    pairs = fc[["wind_speed_10m", "wind_direction_10m"]].sel(station=STATION).to_dataframe()
    pairs = pairs.reset_index()
    pairs["lead_h"] = (pairs["lead_time"] / np.timedelta64(1, "h")).astype(int)
    pairs = (
        pairs[pairs["lead_h"] >= 1]
        .merge(centred, left_on="valid_time", right_index=True)
        .merge(hourly[["ws_snapshot", "wd_snapshot"]], left_on="valid_time", right_index=True)
    )
    groups = {"1-6 h": (1, 6), "7-24 h": (7, 24), "25-48 h": (25, 48)}
    skill: dict = {}
    for label, (lo, hi) in groups.items():
        g = pairs[pairs["lead_h"].between(lo, hi)]
        windy = g["ws_mean"] > 1.5
        low = g["ws_mean"] < LOW_WIND_MS
        skill[label] = {
            "speed_vs_60min_mean": _skill(g["wind_speed_10m"] - g["ws_mean"]),
            "speed_vs_2min_snapshot": _skill(g["wind_speed_10m"] - g["ws_snapshot"]),
            "speed_vs_60min_mean_low_wind": _skill((g["wind_speed_10m"] - g["ws_mean"])[low]),
            "direction_mae_vs_60min_mean": round(
                float(_circ_diff(g["wind_direction_10m"], g["wd_vector"])[windy].mean()), 1
            ),
            "direction_mae_vs_2min_snapshot": round(
                float(_circ_diff(g["wind_direction_10m"], g["wd_snapshot"])[windy].mean()), 1
            ),
        }

    # 2. intra-hour direction spread by speed
    bins = pd.cut(hourly["ws_mean"], SPEED_BINS)
    spread = hourly.groupby(bins, observed=True)["dir_spread_deg"].describe(
        percentiles=[0.25, 0.5, 0.75]
    )[["count", "25%", "50%", "75%"]]
    spread.index = spread.index.astype(str)
    spread.to_csv(OUT_DIR / "wind_direction_spread.csv")
    hourly.to_csv(OUT_DIR / "wind_hourly_cxoa.csv")

    # 3. effective cooling on the virtual line
    inp = virtual_span_inputs(inputs, lon, lat, alt, azimuths)
    inp3 = virtual_span_inputs(inputs, lon, lat, alt, azimuths, trailing_dims=1)
    temp = hourly["air_temperature"].to_numpy()
    ghi = hourly["ghi"].to_numpy()
    speed_h = hourly["ws_mean"].to_numpy() * shear
    r_eff = effective_ratings(inp3, a["ws_1min"] * shear, a["wd_1min"], temp, ghi, hours)
    r_eff10 = effective_ratings(
        inp3, a["ws_1min"] * shear, a["wd_1min"], temp, ghi, hours, block_minutes=10
    )
    line_eff, line_eff10 = line_min(r_eff), line_min(r_eff10)
    wd_tiled = np.tile(hourly["wd_vector"].to_numpy(), (len(azimuths), 1))
    by_floor = {}
    for theta in FLOOR_GRID_DEG:
        direction = floor_attack_angle(wd_tiled, inp.span_azimuth_rad, theta) if theta else wd_tiled
        by_floor[float(theta)] = line_min(vector_ratings(inp, speed_h, direction, temp, ghi, hours))
    line_vec = by_floor[0.0]
    low = hourly["ws_mean"].to_numpy() < LOW_WIND_MS
    theta_all, curve_all = fit_floor(by_floor, line_eff)
    theta_low, curve_low = fit_floor(by_floor, line_eff, low)
    theta_high, curve_high = fit_floor(by_floor, line_eff, ~low)
    theta_10, curve_10 = fit_floor(by_floor, line_eff10)
    curves = pd.DataFrame(
        {
            "all": curve_all,
            "low_wind": curve_low,
            "high_wind": curve_high,
            "all_10min_blocks": curve_10,
        }
    )
    curves.index.name = "floor_deg"
    curves.to_csv(OUT_DIR / "wind_floor_fit.csv")
    ratio = line_eff / line_vec

    # 4. station margin with the effective rating as truth
    fc_wx = fc.sel(station=STATION).to_dataframe().reset_index()
    fc_wx["lead_h"] = (fc_wx["lead_time"] / np.timedelta64(1, "h")).astype(int)
    fc_wx = fc_wx[fc_wx["lead_h"] >= FIRST_SCORED_LEAD_H]
    truth = pd.DataFrame(
        {
            "effective": line_eff,
            "ghi": ghi,
            "temp": temp,
            "ws_snap": hourly["ws_snapshot"].to_numpy() * shear,
            "wd_snap": hourly["wd_snapshot"].to_numpy(),
        },
        index=hours,
    )
    truth["snapshot"] = line_min(
        vector_ratings(
            inp,
            truth["ws_snap"].to_numpy(),
            np.nan_to_num(truth["wd_snap"].to_numpy()),
            temp,
            ghi,
            hours,
        )
    )
    f = fc_wx.merge(truth, left_on="valid_time", right_index=True)
    f_hours = pd.DatetimeIndex(f["valid_time"])
    f["forecast"] = line_min(
        vector_ratings(
            inp,
            f["wind_speed_10m"].to_numpy() * shear,
            f["wind_direction_10m"].to_numpy(),
            f["temperature_2m"].to_numpy(),
            f["ghi"].to_numpy(),
            f_hours,
        )
    )
    f = f.merge(hourly[["ws_mean"]], left_on="valid_time", right_index=True)
    margins = {}
    for name in ("snapshot", "effective"):
        rel = f["forecast"] / f[name] - 1.0
        low_rel = rel[f["ws_mean"] < LOW_WIND_MS].dropna()
        rel = rel.dropna()
        margins[name] = {
            "mae_rel": round(float(rel.abs().mean()), 4),
            "bias_rel": round(float(rel.mean()), 4),
            "margin_98": round(float(np.quantile(rel, CONFIDENCE)), 3),
            "margin_98_low_wind": round(float(np.quantile(low_rel, CONFIDENCE)), 3),
            "bias_rel_low_wind": round(float(low_rel.mean()), 4),
        }

    summary = {
        "station": f"OTTAWA CDA RCS ({STATION}), 8.9 km from the line",
        "period": [str(hours.min()), str(hours.max())],
        "hours": int(len(hours)),
        "minutes": int(np.isfinite(a["ws_1min"]).sum()),
        "weather_skill": skill,
        "direction_spread_median_deg": {
            str(k): round(float(v), 1) for k, v in spread["50%"].items()
        },
        "effective_over_vector": {
            "median": round(float(np.nanmedian(ratio)), 3),
            "p5": round(float(np.nanpercentile(ratio, 5)), 3),
            "p95": round(float(np.nanpercentile(ratio, 95)), 3),
            "median_low_wind": round(float(np.nanmedian(ratio[low])), 3),
            "median_high_wind": round(float(np.nanmedian(ratio[~low])), 3),
            "median_10min_blocks": round(float(np.nanmedian(line_eff10 / line_vec)), 3),
        },
        "fitted_floor_deg": {
            "all_hours": round(theta_all, 1),
            "low_wind": round(theta_low, 1),
            "high_wind": round(theta_high, 1),
            "all_hours_10min_blocks": round(theta_10, 1),
        },
        "station_margin": margins,
        "notes": "one station 8.9 km from the line at 10 m (urban edge); in-sample "
        "(no train/test split) because only ~4 weeks of minute data exist",
    }
    (OUT_DIR / "wind_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
