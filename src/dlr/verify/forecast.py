"""Forecast DLR (0-48 h) and its verification against the reference DLR.

Truth for rating skill is the pseudo-analysis reference DLR (leads 1-6 h of
successive inits). A forecast at lead <= 6 h is partly verified against
itself, so skill is reported for leads >= 7 h; leads 1-6 are the reference
window.

Safety margin: a lead-dependent relative margin chosen on a training period so
that the "secure" forecast (forecast x (1 - margin)) exceeds the truth in at
most `1 - confidence` of hours, then evaluated on a held-out test period.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from dlr.thermal.rating import dlr_rating, sheltering_factor
from dlr.thermal.run import WX_FIELDS, Inputs, floor_attack_angle

FORECAST_STORE = Path("data/interim/ratings_forecast.zarr")
FIRST_SCORED_LEAD_H = 7


def forecast_ratings(inputs: Inputs, wx: xr.Dataset) -> xr.Dataset:
    """Span x (init, lead) forecast DLR for leads >= 1 h; line rating = min over spans."""
    wx = wx.where(wx["lead_time"] > np.timedelta64(0, "h"), drop=True)
    stacked = wx.stack(sample=("init_time", "lead_time")).transpose("span", "sample")
    times = stacked["valid_time"].values.astype("datetime64[m]")
    arrays = {f: stacked[f].values for f in WX_FIELDS}
    base = inputs.rating_inputs()
    shelter = sheltering_factor(inputs.spans, inputs.rating)
    data = {"dlr": (("span", "sample"), dlr_rating(base, arrays, times, shelter).astype("float32"))}
    for theta in inputs.rating.min_attack_angle_sensitivity_deg:
        floored = floor_attack_angle(arrays["wind_direction"], base.span_azimuth_rad, theta)
        rating = dlr_rating(base, {**arrays, "wind_direction": floored}, times, shelter)
        data[f"dlr_attack{theta:.0f}"] = (("span", "sample"), rating.astype("float32"))
    out = xr.Dataset(
        data, coords={"span": stacked["span"].values, "sample": stacked["sample"]}
    ).unstack("sample")
    complete = out["dlr"].notnull().all("span")
    out["line_dlr"] = out["dlr"].min("span").where(complete)
    for name in [str(v) for v in out.data_vars if str(v).startswith("dlr_attack")]:
        out[f"line_{name}"] = out[name].min("span").where(complete)
        out = out.drop_vars(name)
    out["limiting_span"] = out["dlr"].fillna(np.inf).argmin("span").where(complete)
    out = out.assign_coords(valid_time=wx["valid_time"])
    FORECAST_STORE.parent.mkdir(parents=True, exist_ok=True)
    out.to_zarr(FORECAST_STORE, mode="w", consolidated=True)
    return out


def paired(
    forecast: xr.Dataset,
    line_reference: pd.DataFrame,
    variant: str = "line_dlr",
    truth_column: str = "dlr_a",
) -> pd.DataFrame:
    """Long table of (init, lead) line forecasts joined to the reference DLR.

    `variant`/`truth_column` select matching forecast and truth definitions
    (e.g. the 20-degree attack-angle floor applied to both).
    """
    df = forecast[[variant]].rename({variant: "line_dlr"}).to_dataframe().reset_index()
    df["lead_h"] = (df["lead_time"] / np.timedelta64(1, "h")).astype(int)
    truth = line_reference.set_index("time")[[truth_column, "static_a", "aar_a"]].rename(
        columns={truth_column: "dlr_a"}
    )
    df = df.merge(truth, left_on="valid_time", right_index=True, how="inner")
    df = df.dropna(subset=["line_dlr", "dlr_a"])
    df["error_a"] = df["line_dlr"] - df["dlr_a"]
    df["rel_error"] = df["error_a"] / df["dlr_a"]
    # persistence baseline: the reference DLR at init time, held for all leads
    persist = truth["dlr_a"].rename("persistence_a")
    df = df.merge(persist, left_on="init_time", right_index=True, how="left")
    return df


def skill_by_lead(df: pd.DataFrame) -> pd.DataFrame:
    def stats(g: pd.DataFrame) -> pd.Series:
        e = g["error_a"]
        pe = g["persistence_a"] - g["dlr_a"]
        return pd.Series(
            {
                "n": len(g),
                "bias_a": e.mean(),
                "mae_a": e.abs().mean(),
                "rmse_a": np.sqrt((e**2).mean()),
                "mae_rel": g["rel_error"].abs().mean(),
                "over_forecast_share": (e > 0).mean(),
                "persistence_mae_a": pe.abs().mean(),
                "persistence_mae_rel": (pe.abs() / g["dlr_a"]).mean(),
                "static_over_truth_share": (g["static_a"] > g["dlr_a"]).mean(),
            }
        )

    return df.groupby("lead_h").apply(stats, include_groups=False)


def fit_margins(train: pd.DataFrame, confidence: float = 0.98, pool_leads: int = 2) -> pd.Series:
    """Relative margin per lead: the `confidence` quantile of forecast/truth - 1.

    One init per 6 h leaves ~200 training pairs per lead, too few for a 98 %
    quantile, so each lead pools the pairs within +/- `pool_leads` hours.
    """
    over = (train["line_dlr"] / train["dlr_a"] - 1.0).to_numpy()
    lead = train["lead_h"].to_numpy()
    leads = np.unique(lead)
    margin = [np.quantile(over[np.abs(lead - h) <= pool_leads], confidence) for h in leads]
    return pd.Series(np.clip(margin, 0.0, None), index=leads, name="margin")


def apply_margins(df: pd.DataFrame, margins: pd.Series) -> pd.DataFrame:
    out = df.copy()
    m = out["lead_h"].map(margins)
    out["secure_a"] = out["line_dlr"] / (1.0 + m)
    return out


def margin_report(test: pd.DataFrame) -> dict:
    """Exceedance and headroom of the secure forecast on the test period (leads >= 7 h)."""
    t = test[test["lead_h"] >= FIRST_SCORED_LEAD_H]
    exceed = t["secure_a"] > t["dlr_a"]
    gain = t["secure_a"] / t["static_a"]
    return {
        "hours_scored": int(len(t)),
        "secure_exceeds_truth_share": round(float(exceed.mean()), 4),
        "raw_forecast_exceeds_truth_share": round(float((t["line_dlr"] > t["dlr_a"]).mean()), 4),
        "static_exceeds_truth_share": round(float((t["static_a"] > t["dlr_a"]).mean()), 4),
        "aar_exceeds_truth_share": round(float((t["aar_a"] > t["dlr_a"]).mean()), 4),
        "secure_over_static_median": round(float(gain.median()), 3),
        "secure_above_static_share": round(float((gain > 1).mean()), 3),
        "secure_over_static_by_lead_median": {
            int(k): round(float(v), 3)
            for k, v in (t["secure_a"] / t["static_a"]).groupby(t["lead_h"]).median().items()
            if k in (7, 12, 24, 36, 48)
        },
    }


OPERATIONAL_MARGINS = Path("data/interim/margins_operational.json")


def operational_margins(
    forecast: xr.Dataset,
    line_reference: pd.DataFrame,
    today: str,
    confidence: float = 0.98,
    path: Path = OPERATIONAL_MARGINS,
) -> dict:
    """Margins for the live forecast, refit at most once per UTC day.

    Fitted on every (forecast, reference) pair verified so far -- an expanding
    window -- so a new forecast is always secured with margins from the past
    only. Leads below FIRST_SCORED_LEAD_H are partly their own reference and
    would get a ~0 margin; they take the lead-7 value instead.
    """
    import json

    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("fitted_on") == today:
            return cached
    pairs = paired(forecast, line_reference)
    margins = fit_margins(pairs, confidence)
    floor_value = float(margins.loc[FIRST_SCORED_LEAD_H])
    margins.loc[margins.index < FIRST_SCORED_LEAD_H] = floor_value
    result = {
        "fitted_on": today,
        "confidence": confidence,
        "n_pairs": int(len(pairs)),
        "period": [str(pairs["valid_time"].min()), str(pairs["valid_time"].max())],
        "margins": {str(int(k)): round(float(v), 4) for k, v in margins.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
