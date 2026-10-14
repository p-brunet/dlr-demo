"""Station layer of the verification: HRDPS vs observed weather, and the
rating impact of those weather errors at virtual spans placed at the stations.

Weather skill compares HRDPS 2 m temperature and 10 m wind with the hourly
observations, by lead time. The point check computes DLR twice at a virtual
span at each station -- once from observed weather, once from the forecast --
with identical treatment of everything the station does not measure: wind
lifted to conductor height with a fixed 1/7 power law (no 80 m observation),
and the HRDPS reference GHI for the sun. Four span orientations (0, 45, 90,
135 degrees) are pooled, since a station has no line of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import xarray as xr
from linerate import Span, Tower
from pyproj import Geod

from dlr.thermal.rating import RatingInputs, dlr_rating
from dlr.thermal.run import Inputs
from dlr.weather.sample import reference_series, sample_points

ORIENTATIONS_DEG = (0.0, 45.0, 90.0, 135.0)
HALF_SPAN_M = 150.0
_GEOD = Geod(ellps="WGS84")


def station_forecasts(corridor: xr.Dataset, obs: pd.DataFrame) -> xr.Dataset:
    """HRDPS sampled at each station: (init_time, lead_time, station)."""
    stations = obs.groupby("station")[["lon", "lat"]].first()
    variables = [
        "temperature_2m",
        "wind_speed_10m",
        "wind_direction_10m",
        "downward_short_wave_radiation_flux_surface",
    ]
    sampled = sample_points(
        corridor[variables],
        stations["lon"].to_numpy(),
        stations["lat"].to_numpy(),
        point_dim="station",
    ).load()
    return sampled.assign_coords(station=stations.index.to_numpy())


def paired_weather(fc: xr.Dataset, obs: pd.DataFrame) -> pd.DataFrame:
    df = fc.drop_vars(["station_lon", "station_lat"]).to_dataframe().reset_index()
    df = df.rename(columns={"temperature_2m": "air_temperature"})
    df["lead_h"] = (df["lead_time"] / np.timedelta64(1, "h")).astype(int)
    return df.merge(
        obs[["station", "time", "air_temperature", "wind_speed_10m", "wind_direction_10m"]],
        left_on=["station", "valid_time"],
        right_on=["station", "time"],
        suffixes=("_fc", "_obs"),
    )


def _circular_abs_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    d = np.abs(np.mod(a - b, 360.0))
    return np.minimum(d, 360.0 - d)


def weather_skill_by_lead(df: pd.DataFrame) -> pd.DataFrame:
    def stats(g: pd.DataFrame) -> pd.Series:
        t = g["air_temperature_fc"] - g["air_temperature_obs"]
        w = g["wind_speed_10m_fc"] - g["wind_speed_10m_obs"]
        windy = g["wind_speed_10m_obs"] > 1.5
        d = _circular_abs_diff(
            g.loc[windy, "wind_direction_10m_fc"], g.loc[windy, "wind_direction_10m_obs"]
        )
        return pd.Series(
            {
                "n": len(g),
                "temp_bias_c": t.mean(),
                "temp_mae_c": t.abs().mean(),
                "wind_bias_ms": w.mean(),
                "wind_mae_ms": w.abs().mean(),
                "wind_rmse_ms": np.sqrt((w**2).mean()),
                "dir_mae_deg": d.mean(),
            }
        )

    return df.groupby(["station", "lead_h"]).apply(stats, include_groups=False)


def virtual_span_inputs(
    inputs: Inputs,
    lon: float,
    lat: float,
    alt_m: float,
    orientations: Sequence[float] = ORIENTATIONS_DEG,
    trailing_dims: int = 0,
) -> RatingInputs:
    """Virtual spans centred on (lon, lat), one per azimuth in `orientations`.

    Tower arrays are shaped (n_orientations, 1) plus `trailing_dims` extra
    singleton axes, so weather can carry e.g. (hour, minute) dimensions.
    """
    base = inputs.rating_inputs()
    lons, lats = [], []
    for az in orientations:
        a_lon, a_lat, _ = _GEOD.fwd(lon, lat, az + 180.0, HALF_SPAN_M)
        b_lon, b_lat, _ = _GEOD.fwd(lon, lat, az, HALF_SPAN_M)
        lons.append((a_lon, b_lon))
        lats.append((a_lat, b_lat))
    shape = (len(lons), 1) + (1,) * trailing_dims
    lon_a, lat_a = np.array(lons), np.array(lats)
    height = inputs.conductor.conductor_height_m.value
    span = Span(
        conductor=base.span.conductor,
        start_tower=Tower(
            longitude=lon_a[:, 0].reshape(shape),
            latitude=lat_a[:, 0].reshape(shape),
            altitude=alt_m + height,
        ),
        end_tower=Tower(
            longitude=lon_a[:, 1].reshape(shape),
            latitude=lat_a[:, 1].reshape(shape),
            altitude=alt_m + height,
        ),
        num_conductors=base.sub_conductors,
    )
    return RatingInputs(
        span=span,
        span_azimuth_rad=np.asarray(span.conductor_azimuth),
        sub_conductors=base.sub_conductors,
        max_temp_c=base.max_temp_c,
        ground_albedo=base.ground_albedo,
    )


def _rate_virtual(inp: RatingInputs, g: pd.DataFrame, suffix: str, shear: float) -> np.ndarray:
    """DLR at every virtual-span orientation from the `_obs` or `_fc` weather columns."""
    n = len(ORIENTATIONS_DEG)
    wx = {
        "air_temperature": np.tile(g[f"air_temperature_{suffix}"].to_numpy(), (n, 1)),
        "wind_speed": np.tile(g[f"wind_speed_10m_{suffix}"].to_numpy() * shear, (n, 1)),
        "wind_direction": np.tile(g[f"wind_direction_10m_{suffix}"].fillna(0.0).to_numpy(), (n, 1)),
        "ghi": np.tile(g["ghi"].to_numpy(), (n, 1)),
    }
    return dlr_rating(inp, wx, g["valid_time"].to_numpy().astype("datetime64[m]"))


def point_dlr_check(
    inputs: Inputs, fc: xr.Dataset, obs: pd.DataFrame, station_alt_m: dict[str, float]
) -> pd.DataFrame:
    """Forecast vs observation-driven DLR at virtual spans at each station, by lead."""
    shear = (inputs.conductor.conductor_height_m.value / 10.0) ** (1 / 7)
    ghi_fc = fc[["downward_short_wave_radiation_flux_surface"]].rename(
        {"downward_short_wave_radiation_flux_surface": "ghi"}
    )
    ref_ghi = reference_series(ghi_fc)["ghi"]  # (station, time)
    rows = []
    for station, g in paired_weather(fc, obs).groupby("station"):
        g = g.dropna(subset=["air_temperature_obs", "wind_speed_10m_obs", "air_temperature_fc"])
        g = g[g["lead_h"] >= 1]
        ghi = ref_ghi.sel(station=station).to_series()
        g = g.merge(ghi.rename("ghi"), left_on="valid_time", right_index=True).dropna(
            subset=["ghi"]
        )
        lon, lat = obs.loc[obs["station"] == station, ["lon", "lat"]].iloc[0]
        inp = virtual_span_inputs(inputs, float(lon), float(lat), station_alt_m[station])
        observed = _rate_virtual(inp, g, "obs", shear)
        forecast = _rate_virtual(inp, g, "fc", shear)
        for k, az in enumerate(ORIENTATIONS_DEG):
            rows.append(
                pd.DataFrame(
                    {
                        "station": station,
                        "orientation_deg": az,
                        "lead_h": g["lead_h"].to_numpy(),
                        "valid_time": g["valid_time"].to_numpy(),
                        "dlr_obs_a": observed[k],
                        "dlr_fc_a": forecast[k],
                    }
                )
            )
    out = pd.concat(rows, ignore_index=True).dropna(subset=["dlr_obs_a", "dlr_fc_a"])
    out["error_a"] = out["dlr_fc_a"] - out["dlr_obs_a"]
    out["rel_error"] = out["error_a"] / out["dlr_obs_a"]
    return out
