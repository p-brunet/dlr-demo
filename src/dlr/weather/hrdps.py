"""ECCC HRDPS forecast access via dynamical.org's `dynamical-catalog`.

Per https://dynamical.org/prompt.md: never hard-code buckets, asset hrefs or
version numbers — always resolve the dataset through the catalog, and select
variables/region/time window before loading anything.
"""

from __future__ import annotations

from dlr.config import StudyConfig

DATASET_ID = "eccc-hrdps-forecast"

ATTRIBUTION = (
    "ECCC HRDPS data processed by dynamical.org from Environment and Climate "
    "Change Canada, used under the ECCC Data Servers End-use Licence version 2.1"
)


def open_hrdps():
    import dynamical_catalog

    return dynamical_catalog.open(DATASET_ID, chunks=None)


def print_hrdps_schema(study: StudyConfig, dry_run: bool = True) -> None:
    ds = open_hrdps()

    print(f"--- {DATASET_ID} ---")
    print(f"dims: {dict(ds.sizes)}")
    print(f"data_vars: {sorted(ds.data_vars)}")
    print(f"coords: {sorted(ds.coords)}")
    print()
    for coord_name in sorted(ds.coords):
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

    if not dry_run:
        raise NotImplementedError("Bulk HRDPS extraction is out of scope for Milestone 1.")
