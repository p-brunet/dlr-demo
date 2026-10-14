"""Continuous forecast: one idempotent refresh, run on every new HRDPS run.

`refresh()` extracts any new HRDPS runs for the corridor and, only if there are
some (or when forced), recomputes everything downstream and rewrites
`site/data/`:

1. station observations (YOW/YND) for the whole archive period;
2. reference ratings from the extended pseudo-analysis;
3. forecast ratings and verification, including the backtest report;
4. the SWOB wind check, when its archive is present;
5. the site export, including the live 48 h forecast of the newest run with
   margins refit at most once per UTC day.

CI (`.github/workflows/forecast.yml`) calls it hourly and deploys `site/` to
GitHub Pages when it reports new runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from dlr.config import StudyConfig
from dlr.weather.geomet import OBS_PATH, fetch_stations
from dlr.weather.hrdps import extract_corridor, open_corridor

STUDY_CONFIG = Path("config/study.yaml")


def refresh_station_obs() -> int:
    """Re-fetch YOW/YND hourly observations from the first HRDPS run to today."""
    first = pd.Timestamp(open_corridor()["init_time"].values.min()).date().isoformat()
    obs = fetch_stations(first, datetime.now(UTC).date().isoformat())
    OBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(OBS_PATH, index=False)
    return len(obs)


def refresh(force: bool = False) -> dict:
    from dlr.export.site import build_site
    from dlr.thermal.run import run_reference
    from dlr.verify.run import run_verification
    from dlr.verify.wind import run_wind_check

    new_runs = extract_corridor(StudyConfig.from_yaml(STUDY_CONFIG))
    result: dict = {"new_runs": new_runs}
    if new_runs == 0 and not force:
        return result
    try:
        result["station_obs_hours"] = refresh_station_obs()
    except Exception as exc:  # noqa: BLE001 -- verification input only; keep the last file
        if not OBS_PATH.exists():
            raise
        result["station_obs_hours"] = f"kept previous file ({type(exc).__name__})"
    run_reference(rebuild_weather=True)
    run_verification()
    try:
        run_wind_check()
        result["wind_check"] = "updated"
    except FileNotFoundError:
        result["wind_check"] = "skipped: no SWOB archive"
    manifest = build_site()
    result["forecast_init_utc"] = manifest["forecast"]["init_utc"]
    result["margins_fitted_on"] = manifest["forecast"]["margins_fitted_on"]
    return result
