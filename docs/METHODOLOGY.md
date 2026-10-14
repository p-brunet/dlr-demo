# Methodology

How the project turns public data into hourly line ratings and a 48 h
forecast, and every assumption it makes along the way. Results are in
[RESULTS.md](RESULTS.md), inputs in [DATA_SOURCES.md](DATA_SOURCES.md), and
the continuous service in [OPERATIONS.md](OPERATIONS.md).

## In one paragraph

A conductor's safe current is set by its temperature. Joule heating and sun
warm it; wind and air carry heat away. The steady state is solved with the
CIGRE TB 601 heat balance ([linerate](https://github.com/statnett/linerate))
for each of the line's 110 spans, every hour. Weather comes from
Environment Canada's 2.5 km HRDPS model at each span's midpoint, and terrain
from 1 m LiDAR elevation models. The line's rating is that of its weakest
span. The result is compared with the **static** rating (one cautious set of
weather assumptions per season) and an **ambient-adjusted** one (static, but
with the real air temperature). Forecasts 1–48 h ahead get a statistical
**safety margin** so that the "secure" rating is above the true one in at most
2 % of hours.

```
OSM route ──► towers ──► 110 spans ──► + HRDEM terrain (altitude, sheltering)
HRDPS runs ──► corridor extract ──► weather at each span (per run, per lead hour)
                                         │
              CIGRE TB 601 per span ◄────┘──► line rating = weakest span
                                         │
     reference (leads 1–6 h stitched) ───┼──► history: static vs AAR vs DLR
     forecast (leads 1–48 h of each run) ┴──► skill, margins, live 48 h forecast
```

## The line

**A41T/A42T** is a Hydro One 230 kV double circuit, 34.4 km long, with 111
towers. It runs from Hawthorne TS (Ottawa) across the Ottawa River to
Hydro-Québec's Poste de l'Outaouais, a 1,250 MW HVDC back-to-back converter.
It carries the main Ontario–Québec tie (IESO zone `PQ.AT`), which often runs
near 1,250 MW.

- **Why this line:** the first candidate, circuits D5A and H9A to Poste
  Masson, matched IESO zone names. IESO's planning outlook lists them as
  *radial* interconnection points, and their reported flow was 0 MW
  throughout the period.
- **The transfer limit:** with both circuits in service, the converter, not
  the line, limits the transfer. Thermal headroom on the line matters most
  when one circuit is out (see the N-1 context in RESULTS.md).
- **Route assembly** (`src/dlr/line/`):
  - The route is an explicit, ordered list of OSM ways. It includes the
    untagged way that crosses the river.
  - It is rejected unless it is connected (gaps ≤ 50 m) and both ends lie
    within 500 m of their substation polygons.
  - Inferring the route from `ref` tags had once silently dropped the
    untagged crossing.
- **Tags read from OSM:** 230 kV, 2 circuits, and `wires=double`, i.e. two
  sub-conductors per phase.

## Spans and terrain

- **Spans:** one per pair of consecutive towers, 110 in total, with median
  length 314 m and a 686 m river crossing (`src/dlr/line/spans.py`).
  - Each span records its azimuth (clockwise from true north), midpoint and
    chainage.
  - Its side (ON/QC) comes from where it crosses the Ottawa River centreline.
- **Terrain** (`src/dlr/terrain/hrdem.py`, NRCan HRDEM 1 m):
  - **Ground altitude at each tower:** the DTM median within 3 m. It gives the
    conductor altitude (ground + 20 m) and the span inclination.
  - **Exposure index:** obstacle heights (DSM − DTM: trees, buildings) are
    measured in a ring 20–60 m either side of the span axis, beyond the
    ~40 m cleared right-of-way. Then

    `shelter = mean(min(obstacle height / 20 m, 1))`,
    `exposure = 1 − shelter` (1 = open ground or water).
  - **Tower-height check:** the highest DSM return within 8 m of each tower is
    26–87 m above ground, tallest at the river crossing. It is used only as a
    sanity check (lattice towers are partly captured).

## Weather at each span

- **Extraction** (`src/dlr/weather/hrdps.py`): every HRDPS run's corridor
  subset is appended to a local Zarr store. The newest run is used once its
  last lead hour is present.
- **Sampling** (`src/dlr/weather/sample.py`):
  - Span midpoints are transformed into the model's rotated-pole grid using
    the dataset's own CRS, then bilinearly interpolated.
  - Wind direction is interpolated through its east/north components, so it
    never breaks at the 0°/360° wrap.
- **Wind at conductor height (20 m):** a power law between the 10 m and 80 m
  winds of the same hour. The shear exponent is clipped to [0.05, 0.5].
- **Sun:** HRDPS shortwave is an hourly mean. Sun geometry is taken at
  mid-hour, and CIGRE's clear-sky irradiance *on the conductor* (which depends
  on span orientation) is scaled by HRDPS GHI ÷ clear-sky GHI.
- **Reference ("truth") weather:** leads 1–6 h of each run, stitched into one
  continuous hourly series (a pseudo-analysis).

## Thermal ratings

- **Model:** CIGRE TB 601 steady state via `linerate`, per span and hour.
  - The bundle current is the per-conductor ampacity × 2.
  - MW per circuit is √3 × 230 kV × I (unity power factor).
- **Three tiers:**

  | Tier | Air temperature | Wind | Sun |
  |---|---|---|---|
  | Static (seasonal) | 35 °C (May–Oct) / 10 °C (Nov–Apr) | 1.11 m/s (4 km/h), perpendicular to every span | Clear sky at solar noon, 21 June / 21 December |
  | Ambient-adjusted (AAR) | Hourly HRDPS | As static | Clear sky at the actual hour |
  | Dynamic (DLR) | Hourly HRDPS | HRDPS at 20 m, real direction, reduced by sheltering | HRDPS cloudiness |

- **Line rating:** the minimum over spans each hour. The limiting span is
  recorded.
- **Driver attribution:** for the limiting span, each input (wind speed, wind
  angle, air temperature, sun) is replaced in turn by its static value. The
  input that moves the rating most is the hour's dominant driver.
- **Sheltering:** wind is multiplied by `1 − 0.3 × (1 − exposure)`. That is a
  heuristic, so results are also given without it.

## Forecasts and the safety margin

- **Forecast ratings:** every run, every lead 1–48 h, same code as above
  (`src/dlr/verify/forecast.py`).
- **Truth, in three layers:**
  1. **Rating skill:** forecast vs the reference rating. Only leads ≥ 7 h are
     scored, because shorter leads are partly their own reference.
  2. **Weather skill:** HRDPS vs the YOW/YND airport observations.
  3. **Point check:** ratings from observed vs forecast weather at virtual
     spans placed at the stations.
- **Safety margin:**
  - For each lead hour, the margin is the 98th percentile of forecast ÷ truth
    − 1, pooling ±2 lead hours.
  - The **secure** rating is forecast ÷ (1 + margin).
  - **Backtest:** margins fitted on July–August, tested on
    September–October.
  - **Live forecast:** margins refit once a day on every verified pair so far
    (expanding window). Leads under 7 h use the 7 h value.

## Wind check with minute observations

Hourly model wind is a single vector per hour, while real wind changes minute
by minute. A conductor responds over several minutes, so the cooling that
matters is the cooling **averaged over the hour** (`src/dlr/verify/wind.py`).
The check:

- builds a **virtual copy of the line** at Ottawa CDA RCS: the 110 real span
  azimuths, the same conductor, and wind lifted to 20 m;
- computes, each hour, the rating from the hourly-mean wind vector, and the
  rating from minute-by-minute cooling averaged over the hour. The second is
  solved in closed form, `I = n × √((P_conv + P_rad − P_sun) / R(T_max))`;
- finds the **minimum angle of attack** that, applied to hourly wind, gives
  the same median line rating as the minute-resolved one;
- verifies hourly model wind against the 60-minute mean, not the airports'
  2-minute snapshot.

## Assumptions

| Assumption | Value | Basis | Sensitivity tested |
|---|---|---|---|
| Conductor | 2 × ACSR *Drake* 795 kcmil per phase | Typical size; real conductor not public; twin bundle from OSM `wires=double` | Grosbeak (one size down), Cardinal (one size up) |
| Conductor data | Ø 28.14 mm, R_ac 7.185e-5 Ω/m at 25 °C, 8.596e-5 Ω/m at 75 °C | Prysmian datasheet | — |
| Maximum conductor temperature | 93 °C | Ontario continuous rating rule | 75 °C (sag-limited design), 127 °C (emergency) |
| Emissivity / absorptivity | 0.6 / 0.8 | OEB East-West Tie design criteria | — |
| Mean conductor height | 20 m | Hydro One 230 kV towers are 30–60 m; sag | — |
| Static rating weather | 35 °C summer / 10 °C winter, 1.11 m/s perpendicular, clear sky | Hydro One / IESO forms; perpendicular wind as in PJM / IEEE 738 practice | 30 °C summer |
| Seasons | Summer = May–October | Assumption | — |
| Ground albedo | 0.15 | CIGRE TB 601 typical | — |
| Wind at 20 m | Power law between 10 m and 80 m | HRDPS gives both heights | — |
| Wind direction | Taken as exact each hour | Conservative choice | Floors of 9° (measured) and 20° |
| Sheltering | Up to 30 % less wind beside tall obstacles | Heuristic | With and without |
| Safety margin | 98 % confidence, per lead hour | Common DLR practice | Station-based margins |
| Power factor and voltage | 1.0 at 230 kV | For MW conversion only | — |

## Validation in the test suite

- **CIGRE TB 601 Annex E examples A and B** reproduced through `linerate`:
  convective, radiative and solar terms, resistance and ampacity, within the
  published tolerances (`tests/test_thermal.py`).
- **Project extensions:**
  - measured-sun scaling;
  - bundle current;
  - missing-weather handling;
  - the attack-angle floor;
  - closed-form vs solver ratings;
  - route validation (the dropped-crossing regression);
  - sampler exactness on the real grid;
  - margins and seasonal static logic.
