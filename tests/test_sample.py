import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from dlr.weather.sample import reference_series, sample_points, wind_at_height

GRID = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "hrdps_corridor_grid.json").read_text("utf-8")
)


def grid_dataset(**fields: np.ndarray) -> xr.Dataset:
    ds = xr.Dataset(
        {k: (("y", "x"), v) for k, v in fields.items()},
        coords={
            "x": GRID["x"],
            "y": GRID["y"],
            "latitude": (("y", "x"), np.array(GRID["latitude"])),
            "longitude": (("y", "x"), np.array(GRID["longitude"])),
            "spatial_ref": ((), 0, {"crs_wkt": GRID["crs_wkt"]}),
        },
    )
    return ds


def test_sampling_at_cell_centres_returns_cell_values() -> None:
    rng = np.random.default_rng(0)
    field = rng.normal(size=(len(GRID["y"]), len(GRID["x"])))
    ds = grid_dataset(temperature_2m=field)
    lat, lon = np.array(GRID["latitude"]), np.array(GRID["longitude"])
    j, i = np.array([2, 9, 15]), np.array([3, 7, 12])
    out = sample_points(ds, lon[j, i], lat[j, i])
    np.testing.assert_allclose(out["temperature_2m"].values, field[j, i], atol=1e-3)


def test_bilinear_is_exact_for_a_linear_field() -> None:
    xx, yy = np.meshgrid(GRID["x"], GRID["y"])
    ds = grid_dataset(temperature_2m=2.0 * xx - 3.0 * yy)
    lon, lat = np.array([-75.5, -75.45, -75.42]), np.array([45.42, 45.50, 45.58])
    out = sample_points(ds, lon, lat)
    from dlr.weather.sample import rotated_xy

    x, y = rotated_xy(ds, lon, lat)
    np.testing.assert_allclose(out["temperature_2m"].values, 2.0 * x - 3.0 * y, atol=1e-9)


def test_wind_direction_is_averaged_across_north_not_through_south() -> None:
    shape = (len(GRID["y"]), len(GRID["x"]))
    direction = np.where(np.arange(shape[1])[None, :] % 2 == 0, 350.0, 10.0) * np.ones(shape)
    ds = grid_dataset(wind_speed_10m=np.full(shape, 5.0), wind_direction_10m=direction)
    xs, ys = np.array(GRID["x"]), np.array(GRID["y"])
    # midway between columns 4 (350 deg) and 5 (10 deg) on row 8
    from pyproj import CRS, Transformer

    to_geo = Transformer.from_crs(CRS.from_wkt(GRID["crs_wkt"]), "EPSG:4326", always_xy=True)
    lon, lat = to_geo.transform((xs[4] + xs[5]) / 2, ys[8])
    out = sample_points(ds, np.array([lon]), np.array([lat]))
    d = float(out["wind_direction_10m"].values[0])
    assert min(d, 360 - d) < 0.5
    assert float(out["wind_speed_10m"].values[0]) == pytest.approx(5.0)


def test_power_law_wind_at_conductor_height() -> None:
    ws10, ws80 = xr.DataArray([2.0, 2.0, 0.0]), xr.DataArray([4.0, 100.0, 3.0])
    w20 = wind_at_height(ws10, ws80, 20.0).values
    assert w20[0] == pytest.approx(2.0 * 2 ** (np.log(2) / np.log(8)))
    assert w20[1] == pytest.approx(2.0 * 2**0.5)  # shear clipped at 0.5
    assert w20[2] == pytest.approx(0.0)


def test_reference_series_covers_each_hour_once_from_leads_1_to_6() -> None:
    inits = pd.date_range("2026-09-01", periods=3, freq="6h").values
    leads = pd.to_timedelta(np.arange(0, 13), unit="h").values
    valid = inits[:, None] + leads[None, :]
    lead_h = np.broadcast_to(np.arange(13), (3, 13)).astype(float)
    ds = xr.Dataset(
        {"lead_h": (("init_time", "lead_time", "span"), lead_h[..., None])},
        coords={
            "init_time": inits,
            "lead_time": leads,
            "valid_time": (("init_time", "lead_time"), valid),
            "span": [0],
        },
    )
    ref = reference_series(ds)
    assert len(ref.time) == 18 and pd.Index(ref.time.values).is_unique
    assert set(np.unique(ref["lead_h"].values)) == set(range(1, 7))
