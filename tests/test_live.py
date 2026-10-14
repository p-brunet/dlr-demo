import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from dlr.export.site import newest_init
from dlr.thermal.run import seasonal_span_static, seasonal_static
from dlr.verify.forecast import operational_margins


def ratings() -> xr.Dataset:
    return xr.Dataset(
        {
            "static": ("span", [2140.0, 2134.0]),
            "static_winter": ("span", [2700.0, 2690.0]),
        },
        coords={"span": ["S000", "S001"]},
        attrs={"summer_months": [5, 6, 7, 8, 9, 10]},
    )


def test_static_switches_to_winter_values_outside_summer_months() -> None:
    times = pd.to_datetime(["2026-10-31 23:00", "2026-11-01 00:00", "2027-05-01 00:00"]).values
    np.testing.assert_allclose(seasonal_static(ratings(), "static", times), [2134, 2690, 2134])
    span = seasonal_span_static(ratings(), times)
    assert span.shape == (2, 3)
    np.testing.assert_allclose(span[:, 1], [2700, 2690])


def synthetic_forecast(n_inits: int = 40) -> tuple[xr.Dataset, pd.DataFrame]:
    rng = np.random.default_rng(0)
    inits = pd.date_range("2026-09-01", periods=n_inits, freq="6h").values
    leads = pd.to_timedelta(np.arange(1, 49), unit="h").values
    valid = inits[:, None] + leads[None, :]
    times = pd.DatetimeIndex(np.unique(valid.ravel()))
    truth = pd.Series(rng.uniform(1800, 2600, len(times)), index=times)
    forecast = truth.reindex(valid.ravel()).to_numpy().reshape(valid.shape)
    forecast = forecast * (1 + rng.normal(0, 0.03, valid.shape))
    ds = xr.Dataset(
        {"line_dlr": (("init_time", "lead_time"), forecast)},
        coords={
            "init_time": inits,
            "lead_time": leads,
            "valid_time": (("init_time", "lead_time"), valid),
        },
    )
    reference = pd.DataFrame(
        {"time": times, "dlr_a": truth.to_numpy(), "static_a": 2134.0, "aar_a": 2600.0}
    )
    return ds, reference


def test_operational_margins_refit_once_per_day(tmp_path: Path) -> None:
    ds, reference = synthetic_forecast()
    path = tmp_path / "margins.json"
    first = operational_margins(ds, reference, "2026-10-14", path=path)
    assert first["fitted_on"] == "2026-10-14" and first["n_pairs"] > 1000
    # short leads take the lead-7 margin instead of a self-referential ~0
    assert first["margins"]["1"] == first["margins"]["7"] > 0
    # same day: cached, even if the inputs change
    second = operational_margins(
        ds.isel(init_time=slice(0, 10)), reference, "2026-10-14", path=path
    )
    assert second == first
    third = operational_margins(ds.isel(init_time=slice(0, 10)), reference, "2026-10-15", path=path)
    assert third["fitted_on"] == "2026-10-15" and third["n_pairs"] < first["n_pairs"]
    assert json.loads(path.read_text())["fitted_on"] == "2026-10-15"


def test_newest_init_skips_a_run_with_missing_leads() -> None:
    ds, _ = synthetic_forecast(4)
    partial = ds["line_dlr"].values.copy()
    partial[-1, 20:] = np.nan  # newest run only has 20 lead hours
    ds["line_dlr"] = (("init_time", "lead_time"), partial)
    assert newest_init(ds) == ds["init_time"].values[-2]
    with pytest.raises(ValueError):
        newest_init(ds.isel(init_time=[-1]))
