"""ECCC HRDPS forecast access via dynamical.org's `dynamical-catalog`.

Per https://dynamical.org/prompt.md: never hard-code buckets, asset hrefs or
version numbers — always resolve the dataset through the catalog, and select
variables/region/time window before loading anything.

The corridor (study bbox + a 1-cell margin for bilinear sampling) falls inside
one native spatial chunk, so extracting it costs one chunk read per variable
per init. `extract_corridor` appends complete inits to a small local Zarr store
and is resumable: re-running only fetches inits not yet stored.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from dlr.config import BoundingBox, StudyConfig

DATASET_ID = "eccc-hrdps-forecast"

ATTRIBUTION = (
    "ECCC HRDPS data processed by dynamical.org from Environment and Climate "
    "Change Canada, used under the ECCC Data Servers End-use Licence version 2.1"
)

# What the thermal model and the wind-profile/solar handling need.
THERMAL_VARIABLES = [
    "temperature_2m",
    "dew_point_temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_speed_80m",
    "wind_direction_80m",
    "downward_short_wave_radiation_flux_surface",
    "pressure_surface",
]

CORRIDOR_STORE = Path("data/interim/hrdps_corridor.zarr")

# The local store is only ever read back by xarray; consolidated metadata is fine.
warnings.filterwarnings("ignore", message="Consolidated metadata is currently not part")


def open_hrdps() -> xr.Dataset:
    import dynamical_catalog

    return dynamical_catalog.open(DATASET_ID, chunks=None)


def corridor_slices(ds: xr.Dataset, bbox: BoundingBox, margin: int = 1) -> tuple[slice, slice]:
    """(y, x) index slices covering every cell centre inside `bbox`, plus `margin` cells."""
    lat, lon = ds["latitude"].values, ds["longitude"].values
    inside = (
        (lat >= bbox.min_lat)
        & (lat <= bbox.max_lat)
        & (lon >= bbox.min_lon)
        & (lon <= bbox.max_lon)
    )
    ys, xs = np.nonzero(inside)
    if ys.size == 0:
        raise ValueError(f"no HRDPS cell centre inside {bbox}")
    return (
        slice(max(int(ys.min()) - margin, 0), int(ys.max()) + margin + 1),
        slice(max(int(xs.min()) - margin, 0), int(xs.max()) + margin + 1),
    )


def _complete_inits(ds: xr.Dataset, probe: xr.DataArray | None = None) -> np.ndarray:
    """Init times whose full forecast length has been ingested.

    `ingested_forecast_length` is all NaT in the archive (checked 2026-10-13),
    so when it is unusable the newest init is kept only if `probe` (one corridor
    cell of one variable) already has its last lead hour -- a single chunk read.
    Skipping the newest run blindly would delay the live forecast by 6 h.
    """
    inits = ds["init_time"].values
    ingested = ds.coords.get("ingested_forecast_length")
    if ingested is not None and not np.isnat(ingested.values).all():
        return inits[(ingested == ds["expected_forecast_length"]).values]
    if probe is not None and np.isfinite(probe.isel(init_time=-1, lead_time=-1).values):
        return inits
    return inits[:-1]


def extract_corridor(
    study: StudyConfig,
    store: Path = CORRIDOR_STORE,
    variables: list[str] | None = None,
    period_only: bool = False,
    batch_size: int = 8,
) -> int:
    """Append not-yet-stored complete inits for the corridor to `store`; return how many."""
    variables = variables or THERMAL_VARIABLES
    ds = open_hrdps()
    y_slice, x_slice = corridor_slices(ds, study.bbox)
    probe = ds[variables[0]].isel(y=y_slice.start, x=x_slice.start)
    inits = _complete_inits(ds, probe)
    if period_only:
        # inits whose 0-48 h forecasts reach into the study period
        start = np.datetime64(pd.Timestamp(study.period.start)) - np.timedelta64(2, "D")
        end = np.datetime64(pd.Timestamp(study.period.end) + pd.Timedelta(days=1))
        inits = inits[(inits >= start) & (inits < end)]
    done: set[np.datetime64] = set()
    if store.exists():
        done = set(xr.open_zarr(store)["init_time"].values)
    todo = np.array(sorted(t for t in inits if t not in done))
    if todo.size == 0:
        print("corridor store already up to date")
        return 0

    subset = ds[variables].isel(y=y_slice, x=x_slice)
    subset = subset.drop_vars(
        [c for c in ("expected_forecast_length", "ingested_forecast_length") if c in subset.coords]
    )
    subset.attrs = {
        **ds.attrs,
        "corridor_bbox": str(study.bbox.model_dump()),
        "corridor_y_slice": f"{y_slice.start}:{y_slice.stop}",
        "corridor_x_slice": f"{x_slice.start}:{x_slice.stop}",
    }
    print(
        f"extracting {todo.size} init(s), {len(variables)} variables, "
        f"y {y_slice.start}:{y_slice.stop}, x {x_slice.start}:{x_slice.stop} -> {store}"
    )
    store.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    for i in range(0, todo.size, batch_size):
        batch = subset.sel(init_time=todo[i : i + batch_size]).load()
        if store.exists():
            batch.drop_vars(["latitude", "longitude", "spatial_ref"], errors="ignore").to_zarr(
                store, mode="a", append_dim="init_time", consolidated=True
            )
        else:
            encoding = {
                v: {
                    "chunks": (
                        batch_size,
                        batch.sizes["lead_time"],
                        batch.sizes["y"],
                        batch.sizes["x"],
                    )
                }
                for v in variables
            }
            batch.to_zarr(store, mode="w", encoding=encoding, consolidated=True)
        n = min(i + batch_size, todo.size)
        rate = (time.monotonic() - t0) / n
        print(f"  {n}/{todo.size} inits ({str(todo[n - 1])[:16]}), {rate:.1f} s/init", flush=True)
    return int(todo.size)


def open_corridor(store: Path = CORRIDOR_STORE) -> xr.Dataset:
    return xr.open_zarr(store).sortby("init_time")


def print_hrdps_schema(study: StudyConfig) -> None:
    ds = open_hrdps()

    print(f"--- {DATASET_ID} ---")
    print(f"dims: {dict(ds.sizes)}")
    print(f"data_vars: {sorted(map(str, ds.data_vars))}")
    print(f"coords: {sorted(map(str, ds.coords))}")
    print()
    for coord_name in sorted(map(str, ds.coords)):
        coord = ds.coords[coord_name]
        print(f"coord {coord_name!r}: dims={coord.dims} shape={coord.shape} dtype={coord.dtype}")
    print()
    print("dataset attrs:")
    for k, v in ds.attrs.items():
        print(f"  {k}: {v}")
    print()
    for var_name in ("wind_direction_10m", "temperature_2m"):
        if var_name in ds.data_vars:
            print(f"{var_name} attrs: {dict(ds[var_name].attrs)}")
    y_slice, x_slice = corridor_slices(ds, study.bbox)
    print(f"corridor slices for the study bbox: y {y_slice}, x {x_slice}")
