import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlr.thermal.run import Inputs
from dlr.verify.stations import virtual_span_inputs
from dlr.verify.wind import (
    effective_ratings,
    fit_floor,
    hourly_summary,
    line_min,
    minute_matrix,
    vector_ratings,
)
from dlr.weather.swob import tidy

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "swob_cxoa_sample.json"
LON, LAT, ALT = -75.714, 45.382, 79.0


def minutes_frame(speeds: np.ndarray, directions: np.ndarray) -> pd.DataFrame:
    time = pd.date_range("2026-10-01 10:01", periods=len(speeds), freq="min")
    return pd.DataFrame(
        {
            "time": time,
            "ws_1min": speeds,
            "wd_1min": directions,
            "air_temperature": 15.0,
            "ws_2min": speeds,
            "wd_2min": directions,
        }
    )


def test_hourly_vector_mean_crosses_north_and_spread_is_small() -> None:
    directions = np.tile([350.0, 10.0], 60)
    hours, arrays = minute_matrix(minutes_frame(np.full(120, 4.0), directions))
    summary = hourly_summary(hours, arrays)
    d = summary["wd_vector"].iloc[0]
    assert min(d, 360 - d) < 0.5
    assert summary["dir_spread_deg"].iloc[0] == pytest.approx(10.0, abs=0.5)
    assert summary["ws_mean"].iloc[0] == pytest.approx(4.0)


def test_fixture_hours_match_the_stations_own_hourly_mean() -> None:
    minutes = tidy(json.loads(FIXTURE.read_text(encoding="utf-8")), "CXOA")
    hours, arrays = minute_matrix(minutes)
    summary = hourly_summary(hours, arrays)
    own = minutes.set_index("time").loc[hours, "ws_1h"]
    assert len(hours) >= 2
    np.testing.assert_allclose(summary["ws_mean"], own, atol=0.15)


@pytest.fixture(scope="module")
def inputs() -> Inputs:
    return Inputs.load()


def test_effective_equals_vector_rating_for_a_steady_wind(inputs: Inputs) -> None:
    azimuths = [0.0, 30.0, 75.0, 120.0]
    inp = virtual_span_inputs(inputs, LON, LAT, ALT, azimuths)
    inp3 = virtual_span_inputs(inputs, LON, LAT, ALT, azimuths, trailing_dims=1)
    hours = pd.DatetimeIndex(["2026-09-20 12:00", "2026-09-21 03:00"])
    speed, direction = np.array([2.5, 0.8]), np.array([200.0, 45.0])
    temp, ghi = np.array([20.0, 8.0]), np.array([600.0, 0.0])
    vector = vector_ratings(inp, speed, direction, temp, ghi, hours)
    minute_speed = np.repeat(speed[:, None], 60, axis=1)
    minute_dir = np.repeat(direction[:, None], 60, axis=1)
    effective = effective_ratings(inp3, minute_speed, minute_dir, temp, ghi, hours)
    # closed form vs linerate's bisection (tolerance 0.5 A)
    np.testing.assert_allclose(effective, vector, atol=1.0)


def test_gusty_veering_wind_cools_more_than_its_hourly_vector(inputs: Inputs) -> None:
    azimuths = [90.0]  # span east-west; mean wind blows along it
    inp = virtual_span_inputs(inputs, LON, LAT, ALT, azimuths)
    inp3 = virtual_span_inputs(inputs, LON, LAT, ALT, azimuths, trailing_dims=1)
    hours = pd.DatetimeIndex(["2026-09-21 03:00"])
    minute_dir = np.tile([60.0, 120.0], 30)[None, :]  # veers +/-30 deg around 90 deg
    minute_speed = np.full((1, 60), 2.0)
    temp, ghi = np.array([15.0]), np.array([0.0])
    vector = line_min(vector_ratings(inp, np.array([2.0]), np.array([90.0]), temp, ghi, hours))
    effective = line_min(effective_ratings(inp3, minute_speed, minute_dir, temp, ghi, hours))
    assert effective[0] > vector[0]


def test_fit_floor_interpolates_where_the_median_ratio_crosses_one() -> None:
    effective = np.full(10, 1000.0)
    by_floor = {float(t): effective * (0.95 + 0.006 * t) for t in range(0, 21)}
    theta, curve = fit_floor(by_floor, effective)
    assert theta == pytest.approx(0.05 / 0.006, abs=1e-6)
    assert curve.loc[0.0] == pytest.approx(0.95)
