# /// script
# requires-python = ">=3.12"
# dependencies = ["geolibre==3.4.0", "numpy>=2"]
# ///
"""Build the GeoLibre version of the story map from `site/data/` (after `dlr export`).

Standalone (PEP 723) because the `geolibre` package bundles the whole web app
(~200 MB) and is only needed to author these two files:

- `site/dlr.geolibre.json`: a GeoLibre project (layers inlined, time slider);
- `site/geolibre.html`: a standalone page embedding the hosted GeoLibre app
  (https://web.geolibre.app) with that project -- no account needed to view.

Layers: weakest-span share and wind exposure (static), and the daily
worst-hour span rating / static rating as a time-bound layer driven by
GeoLibre's time slider plugin.

Usage: uv run scripts/build_geolibre.py [site_dir]
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from geolibre import Map

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
DATA = SITE / "data"
RATIO_STOPS = [
    (0.7, "#a62a2a"),
    (0.85, "#e66767"),
    (1.0, "#9a9993"),
    (1.15, "#3987e5"),
    (1.35, "#104281"),
]
BLUES = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]


def stops(values: list[float], colors: list[str]) -> list[dict]:
    return [{"value": v, "color": c} for v, c in zip(values, colors, strict=True)]


spans = json.loads((DATA / "spans.geojson").read_text(encoding="utf-8"))
hourly = json.loads((DATA / "line_hourly.json").read_text(encoding="utf-8"))
n_spans = len(spans["features"])
ratio = np.fromfile(DATA / "span_ratio.bin", dtype="<u2").reshape(hourly["n"], n_spans) / 1000.0
ratio[ratio == 0] = np.nan

# daily worst hour per span (UTC days)
t0 = datetime.fromisoformat(hourly["t0"].replace("Z", "+00:00"))
day0 = t0.replace(hour=0, minute=0, second=0, microsecond=0)
day_index = np.array([((t0 + timedelta(hours=h)) - day0).days for h in range(hourly["n"])])
features = []
for d in np.unique(day_index):
    daily_min = np.nanmin(ratio[day_index == d], axis=0)
    stamp = (day0 + timedelta(days=int(d))).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f, value in zip(spans["features"], daily_min, strict=True):
        if np.isfinite(value):
            features.append(
                {
                    "type": "Feature",
                    "geometry": f["geometry"],
                    "properties": {
                        "span_id": f["properties"]["span_id"],
                        "day": stamp,
                        "worst_hour_ratio": round(float(value), 3),
                    },
                }
            )
daily = {"type": "FeatureCollection", "features": features}
n_days = int(day_index.max()) + 1

lons = [c[0] for f in spans["features"] for c in f["geometry"]["coordinates"]]
lats = [c[1] for f in spans["features"] for c in f["geometry"]["coordinates"]]
m = Map(center=[(min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2], zoom=11)

share_max = max(f["properties"]["limiting_share"] for f in spans["features"])
m.add_choropleth(
    spans,
    column="limiting_share",
    name="Share of hours as weakest span",
    vectorStyleStops=stops(list(np.linspace(0, share_max, 5).round(4)), BLUES),
    strokeWidth=5,
)
m.add_choropleth(
    spans,
    column="exposure_index",
    name="Wind exposure index (1 = open)",
    vectorStyleStops=stops([0.45, 0.57, 0.69, 0.81, 0.93], BLUES),
    strokeWidth=5,
    visible=False,
)
m.add_choropleth(
    daily,
    column="worst_hour_ratio",
    name="Worst hour of the day: span rating ÷ static",
    vectorStyleStops=stops([v for v, _ in RATIO_STOPS], [c for _, c in RATIO_STOPS]),
    strokeWidth=6,
)

start = day0
end = day0 + timedelta(days=n_days - 1)
iso = "%Y-%m-%dT%H:%M:%S.000Z"
m.set_plugin_state(
    "maplibre-gl-time-slider",
    {
        "startDate": start.strftime(iso),
        "endDate": end.strftime(iso),
        "interval": 1,
        "granularity": "day",
        "granularities": ["day"],
        "currentDate": start.strftime(iso),
        "speed": 600,
        "loop": True,
        "autoPlay": False,
        "collapsed": False,
        "sources": [],
    },
    position="bottom-left",
)

project_path = SITE / "dlr.geolibre.json"
m.save_project(str(project_path))
project = json.loads(project_path.read_text(encoding="utf-8"))
project["name"] = "A41T/A42T dynamic line rating"
start_ms = int(start.replace(tzinfo=UTC).timestamp() * 1000)
for layer in project["layers"]:
    if layer["name"].startswith("Worst hour"):
        layer.setdefault("metadata", {})["timeBinding"] = {
            "property": "day",
            "valueKind": "isoDateTime",
            "min": start_ms,
            "max": start_ms + (n_days - 1) * 86_400_000,
            "granularity": "day",
            "window": {"unit": "day", "before": 0, "after": 1},
        }
project_path.write_text(json.dumps(project), encoding="utf-8")

# re-load the edited project so the HTML export carries the time binding
m.load_project(str(project_path))
m.to_html(str(SITE / "geolibre.html"), title="A41T/A42T line rating in GeoLibre", height="100vh")
print(
    f"wrote {project_path} ({project_path.stat().st_size / 1e6:.1f} MB, {len(features)} "
    f"daily features) and {SITE / 'geolibre.html'}"
)
