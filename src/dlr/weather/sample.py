"""Sample HRDPS corridor fields at span midpoints and derive span-level weather.

Sampling is bilinear in the native rotated-pole grid (regular in rotated
degrees): points are transformed with the dataset's own `spatial_ref` WKT, so
no hand-rolled rotation is involved. Wind direction is interpolated through
its u/v components (an angle cannot be averaged across the 0/360 wrap), and
speed is interpolated as a scalar so averaging does not damp it.

Derived span weather (`span_weather`):
- `wind_speed`: wind at conductor height from a per-timestep power law between
  the 10 m and 80 m winds (shear exponent clipped to [0.05, 0.5]);
- `wind_direction`: 10 m direction ("from", degrees clockwise from true north);
- `air_temperature`: 2 m temperature;
- `ghi`: downward shortwave flux, a mean over the hour ending at valid_time
  (NaN at lead 0, where there is no previous step).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

SPAN_WEATHER_STORE = Path("data/interim/span_weather.zarr")
SHEAR_LIMITS = (0.05, 0.5)
DEFAULT_SHEAR = 1 / 7


def rotated_xy(ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    crs = CRS.from_wkt(ds["spatial_ref"].attrs["crs_wkt"])
    to_rotated = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = to_rotated.transform(np.asarray(lon), np.asarray(lat))
    return np.asarray(x), np.asarray(y)


def _bilinear(ds: xr.Dataset, x: np.ndarray, y: np.ndarray, point_dim: str) -> xr.Dataset:
    """Bilinear interpolation on a regular (x, y) grid, vectorised over points."""
    xs, ys = ds["x"].values, ds["y"].values
    fx = (x - xs[0]) / (xs[1] - xs[0])
    fy = (y - ys[0]) / (ys[1] - ys[0])
    if fx.min() < 0 or fy.min() < 0 or fx.max() > len(xs) - 1 or fy.max() > len(ys) - 1:
        raise ValueError("points fall outside the extracted corridor grid")
    i0 = np.clip(np.floor(fx).astype(int), 0, len(xs) - 2)
    j0 = np.clip(np.floor(fy).astype(int), 0, len(ys) - 2)
    wx = xr.DataArray(fx - i0, dims=point_dim)
    wy = xr.DataArray(fy - j0, dims=point_dim)

    def corner(dj: int, di: int) -> xr.Dataset:
        picked = ds.isel(
            x=xr.DataArray(i0 + di, dims=point_dim), y=xr.DataArray(j0 + dj, dims=point_dim)
        )
        return picked.drop_vars(["x", "y"])

    return (
        corner(0, 0) * (1 - wx) * (1 - wy)
        + corner(0, 1) * wx * (1 - wy)
        + corner(1, 0) * (1 - wx) * wy
        + corner(1, 1) * wx * wy
    )


def sample_points(
    ds: xr.Dataset, lon: np.ndarray, lat: np.ndarray, point_dim: str = "span"
) -> xr.Dataset:
    """Bilinearly interpolate every gridded variable of `ds` at (lon, lat) points."""
    x, y = rotated_xy(ds, lon, lat)

    ds = ds.copy()
    for level in ("10m", "80m"):
        speed, direction = ds.get(f"wind_speed_{level}"), ds.get(f"wind_direction_{level}")
        if speed is None or direction is None:
            continue
        rad = np.deg2rad(direction)
        ds[f"_u_{level}"] = -speed * np.sin(rad)
        ds[f"_v_{level}"] = -speed * np.cos(rad)
        ds = ds.drop_vars(f"wind_direction_{level}")

    data_vars = [v for v in ds.data_vars if {"y", "x"} <= set(ds[v].dims)]
    gridded = ds[data_vars].drop_vars(["latitude", "longitude", "spatial_ref"], errors="ignore")
    out = _bilinear(gridded, x, y, point_dim)
    for level in ("10m", "80m"):
        if f"_u_{level}" in out:
            u, v = out[f"_u_{level}"], out[f"_v_{level}"]
            out[f"wind_direction_{level}"] = np.mod(np.rad2deg(np.arctan2(-u, -v)), 360.0)
            out = out.drop_vars([f"_u_{level}", f"_v_{level}"])
    return out.assign_coords(
        {
            f"{point_dim}_lon": (point_dim, np.asarray(lon)),
            f"{point_dim}_lat": (point_dim, np.asarray(lat)),
        }
    )


def wind_at_height(ws10: xr.DataArray, ws80: xr.DataArray, height_m: float) -> xr.DataArray:
    """Power-law wind at `height_m` using the shear between 10 m and 80 m."""
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.log(ws80 / ws10) / np.log(80.0 / 10.0)
    alpha = alpha.where(np.isfinite(alpha) & (ws10 > 0.1), DEFAULT_SHEAR).clip(*SHEAR_LIMITS)
    return ws10 * (height_m / 10.0) ** alpha


def span_weather(sampled: xr.Dataset, conductor_height_m: float) -> xr.Dataset:
    out = xr.Dataset(
        {
            "air_temperature": sampled["temperature_2m"],
            "wind_speed": wind_at_height(
                sampled["wind_speed_10m"], sampled["wind_speed_80m"], conductor_height_m
            ),
            "wind_direction": sampled["wind_direction_10m"],
            "ghi": sampled["downward_short_wave_radiation_flux_surface"].clip(min=0),
            "wind_speed_10m": sampled["wind_speed_10m"],
        }
    )
    out["wind_speed"].attrs = {"units": "m s-1", "height_m": conductor_height_m}
    out["wind_direction"].attrs = {
        "units": "degree",
        "convention": "from, clockwise from true north",
    }
    out["ghi"].attrs = {"units": "W m-2", "note": "mean over the hour ending at valid_time"}
    return out


def reference_series(span_wx: xr.Dataset, leads_h: tuple[int, int] = (1, 6)) -> xr.Dataset:
    """Pseudo-analysis: stitch leads 1-6 h of successive 6-hourly inits into one series.

    Each valid hour is covered exactly once when inits are 6 h apart; where an
    init is missing the gap stays NaN (no longer leads are borrowed).
    """
    lead_hours = (span_wx["lead_time"] / np.timedelta64(1, "h")).astype(int)
    short = span_wx.where((lead_hours >= leads_h[0]) & (lead_hours <= leads_h[1]), drop=True)
    stacked = short.stack(sample=("init_time", "lead_time"))
    valid = stacked["valid_time"].values
    stacked = (
        stacked.drop_vars(["sample", "init_time", "lead_time", "valid_time"])
        .assign_coords(time=("sample", valid))
        .swap_dims({"sample": "time"})
    )
    _, first = np.unique(stacked["time"].values, return_index=True)
    return stacked.isel(time=np.sort(first)).sortby("time")
