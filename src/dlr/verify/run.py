"""M5 orchestration: forecast DLR, layered verification, safety margins, report data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from dlr.thermal.run import LINE_REFERENCE_PATH, Inputs, open_span_weather
from dlr.verify.forecast import (
    FIRST_SCORED_LEAD_H,
    apply_margins,
    fit_margins,
    forecast_ratings,
    margin_report,
    paired,
    skill_by_lead,
)
from dlr.verify.stations import point_dlr_check, station_forecasts, weather_skill_by_lead
from dlr.weather.geomet import OBS_PATH, STATION_ELEVATION_M
from dlr.weather.hrdps import open_corridor

OUT_DIR = Path("data/processed/verification")
TRAIN_END = pd.Timestamp("2026-09-01")  # train: valid < Sep 1; test: init >= Sep 1
CONFIDENCE = 0.98


def _round(d: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def run_verification() -> dict:
    inputs = Inputs.load()
    reference = pd.read_parquet(LINE_REFERENCE_PATH)
    forecast = forecast_ratings(inputs, open_span_weather())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "train_period": f"valid < {TRAIN_END.date()}",
        "test_period": f"init >= {TRAIN_END.date()}",
        "confidence": CONFIDENCE,
        "first_scored_lead_h": FIRST_SCORED_LEAD_H,
        "variants": {},
    }
    variants = {"base": ("line_dlr", "dlr_a")}
    for theta in inputs.rating.min_attack_angle_sensitivity_deg:
        variants[f"min_attack_{theta:.0f}deg"] = (
            f"line_dlr_attack{theta:.0f}",
            f"dlr_attack{theta:.0f}_a",
        )
    for name, (fc_var, truth_col) in variants.items():
        df = paired(forecast, reference, variant=fc_var, truth_column=truth_col)
        skill = skill_by_lead(df)
        skill.to_csv(OUT_DIR / f"rating_skill_by_lead_{name}.csv")
        train, test = df[df["valid_time"] < TRAIN_END], df[df["init_time"] >= TRAIN_END]
        margins = fit_margins(train, CONFIDENCE)
        margins.to_csv(OUT_DIR / f"margins_{name}.csv")
        tested = apply_margins(test, margins)
        if name == "base":
            tested.to_parquet(OUT_DIR / "secure_forecast_test_base.parquet", index=False)
        scored = skill.loc[skill.index >= FIRST_SCORED_LEAD_H]
        summary["variants"][name] = {
            "rating_mae_rel_by_lead": {
                int(k): round(float(v), 4)
                for k, v in scored["mae_rel"].items()
                if k in (7, 12, 24, 36, 48)
            },
            "rating_bias_a_mean": round(float(scored["bias_a"].mean()), 1),
            "persistence_mae_over_forecast_mae": round(
                float((scored["persistence_mae_a"] / scored["mae_a"]).median()), 2
            ),
            "margin_by_lead": {
                int(k): round(float(v), 3) for k, v in margins.items() if k in (7, 12, 24, 36, 48)
            },
            "test": margin_report(tested),
        }

    obs = pd.read_parquet(OBS_PATH)
    st_fc = station_forecasts(open_corridor(), obs)
    from dlr.verify.stations import paired_weather

    weather = weather_skill_by_lead(paired_weather(st_fc, obs))
    weather.to_csv(OUT_DIR / "station_weather_skill_by_lead.csv")
    point = point_dlr_check(inputs, st_fc, obs, STATION_ELEVATION_M)
    point_skill = point.groupby("lead_h").agg(
        n=("error_a", "size"),
        bias_a=("error_a", "mean"),
        mae_a=("error_a", lambda e: float(np.abs(e).mean())),
        mae_rel=("rel_error", lambda e: float(np.abs(e).mean())),
        over_forecast_share=("error_a", lambda e: float((e > 0).mean())),
    )
    point_skill.to_csv(OUT_DIR / "station_point_dlr_skill_by_lead.csv")

    # station-calibrated margin: point errors are ~lead-independent, so one pooled value
    scored = point[point["lead_h"] >= FIRST_SCORED_LEAD_H]
    p_train = scored[scored["valid_time"] < TRAIN_END]
    p_test = scored[scored["valid_time"] >= TRAIN_END]
    station_margin = float(
        np.quantile(p_train["dlr_fc_a"] / p_train["dlr_obs_a"] - 1.0, CONFIDENCE)
    )
    p_secure = p_test["dlr_fc_a"] / (1.0 + station_margin)
    base_test = pd.read_parquet(OUT_DIR / "secure_forecast_test_base.parquet")
    base_test = base_test[base_test["lead_h"] >= FIRST_SCORED_LEAD_H]
    line_secure_station = base_test["line_dlr"] / (1.0 + station_margin)
    summary["station_calibrated_margin"] = {
        "margin": round(station_margin, 3),
        "point_test_exceedance_share": round(float((p_secure > p_test["dlr_obs_a"]).mean()), 4),
        "line_secure_over_static_median": round(
            float((line_secure_station / base_test["static_a"]).median()), 3
        ),
        "line_secure_above_static_share": round(
            float((line_secure_station > base_test["static_a"]).mean()), 3
        ),
    }

    w = weather.reset_index()
    early, late = w[w["lead_h"].between(1, 6)], w[w["lead_h"].between(24, 48)]
    summary["stations"] = {
        "stations": sorted(obs["station"].unique().tolist()),
        "hours_obs": int(len(obs)),
        "leads_1_6": _round(
            {
                c: float(early[c].mean())
                for c in ("temp_bias_c", "temp_mae_c", "wind_bias_ms", "wind_mae_ms", "dir_mae_deg")
            }
        ),
        "leads_24_48": _round(
            {
                c: float(late[c].mean())
                for c in ("temp_bias_c", "temp_mae_c", "wind_bias_ms", "wind_mae_ms", "dir_mae_deg")
            }
        ),
        "point_dlr": {
            "mae_rel_leads_1_6": round(
                float(point.loc[point["lead_h"] <= 6, "rel_error"].abs().mean()), 4
            ),
            "mae_rel_leads_24_48": round(
                float(point.loc[point["lead_h"] >= 24, "rel_error"].abs().mean()), 4
            ),
            "bias_rel_all": round(float(point["rel_error"].mean()), 4),
            "over_forecast_share": round(float((point["error_a"] > 0).mean()), 4),
        },
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
