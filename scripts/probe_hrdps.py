# /// script
# requires-python = ">=3.12"
# dependencies = ["dynamical-catalog>=1"]
# ///
"""One-off probe: open HRDPS live and print its schema.

Standalone (PEP 723) so it can be run without the full `dlr` project via
`uv run scripts/probe_hrdps.py`, e.g. for a quick sanity check on another
machine.
"""

import dynamical_catalog

ds = dynamical_catalog.open("eccc-hrdps-forecast", chunks=None)
print(f"dims: {dict(ds.sizes)}")
print(f"data_vars: {sorted(ds.data_vars)}")
print(f"coords: {sorted(ds.coords)}")
print(f"attribution: {ds.attrs.get('attribution')}")
