# dlr-demo

**How much more could a power line carry if its rating followed the weather?**

A *sensorless* dynamic line rating (DLR) of a real Ontario–Québec
transmission line, built only from public data (Environment Canada weather
forecasts, NRCan LiDAR terrain and OpenStreetMap). It publishes a 48-hour
rating forecast that refreshes itself with every new weather run.

- **Live dashboard:** https://p-brunet.github.io/dlr-demo/ (forecast for the
  next 48 h, plus the history since July 2026)
- **Write-up:** https://p-brunet.github.io/posts/dlr-ottawa-outaouais

![Where the bottleneck sits](docs/figures/bottleneck_spans.png)

## The line

Hydro One's **A41T/A42T** is a 230 kV double circuit, 34.4 km long, with 111
towers and 110 spans. It runs from Hawthorne TS in Ottawa across the Ottawa
River to Hydro-Québec's Outaouais converter, and carries the main
1,250 MW Ontario–Québec tie (IESO zone `PQ.AT`).

## Results so far

Snapshot from 2026-07-09 to 2026-10-14, assuming a typical conductor.

- **Static rating:** about 850 MW per circuit in summer.
- **The weather-based rating is a median +8 % higher.** That rises to +12 %
  once minute-by-minute wind is accounted for.
- **The static rating is optimistic in 30 % of hours.** The line turns, so
  some span almost always has the wind along it. 95 of the 110 spans are the
  weakest link at some hour.
- **Forecasts:** a day ahead the rating is forecast within about 5 %. A 98 %
  "secure" forecast was too high in 1 % of test hours, against 19 % for the
  static rating.

Details in [docs/RESULTS.md](docs/RESULTS.md).

## Documentation

| Document | What it covers |
|---|---|
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | How it works, step by step, and every assumption with its source |
| [docs/RESULTS.md](docs/RESULTS.md) | Ratings, sensitivities, forecast skill, checks against observations, limitations |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Each data source: access, licence, coverage, caveats |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | The continuous forecast: schedule, latency, refits, failure modes |

## Run it

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run dlr select-line          # OSM route (validated) -> line and towers
uv run dlr spans                # 110 spans + HRDEM terrain (altitude, exposure)
uv run dlr fetch-ieso           # IESO intertie flows (archived daily in CI)
uv run dlr fetch-swob           # ECCC minute observations (archived daily in CI)
uv run dlr refresh --force      # weather -> ratings -> verification -> wind check -> site data
uv run dlr figures              # docs/figures/*.png
uv run dlr publish              # preview the dashboard at http://localhost:8000
uv run pytest
```

`dlr refresh` is what the hourly CI job runs. The first run downloads the
HRDPS archive for the corridor (30–60 min); later runs only fetch new model
runs. The individual steps are also available on their own: `dlr fetch`,
`dlr rate`, `dlr verify`, `dlr verify-wind`, `dlr export`, `dlr fetch-obs`.

## Repository layout

```
config/          line (generated), conductor and rating assumptions, study area
src/dlr/         line, terrain, weather, thermal, verify, export, pipeline
data/processed/  small derived datasets (line, towers, spans, ratings, verification)
data/ieso/       IESO intertie flow archive (daily, CI)
data/obs/swob/   ECCC minute observation archive (daily, CI)
docs/            documentation, figures and the blog post source
site/index.html  the dashboard; site/data/ is generated, not committed
.github/         forecast-and-publish (hourly) and open-data-archive (daily)
```

## Licences and attribution

The code licence is not chosen yet. Data keeps its sources' terms:

- line, towers and spans: © OpenStreetMap contributors, ODbL 1.0;
- weather: ECCC HRDPS processed by dynamical.org, ECCC Data Servers End-use
  Licence v2.1;
- station observations: ECCC Data Server End-use Licence;
- terrain: NRCan HRDEM, Open Government Licence – Canada;
- intertie flows: IESO public reports.

The conductor and static-rating assumptions are typical public values, not
the operator's. The ratings are indicative and are not for operational use.
