# Data sources

Every input is free and public. For each source: what it provides, how the
project reads it, its licence, coverage and freshness, and the caveats that
matter. All of it was checked live between 2026-09-22 and 2026-10-14.

| Source | Used for | Refreshed |
|---|---|---|
| [OpenStreetMap](#openstreetmap-line-towers-substations-river) | Line route, towers, substations, river border | On demand (`dlr select-line`, `dlr spans`) |
| [NRCan HRDEM](#nrcan-hrdem-terrain) | Ground altitude, obstacles beside each span | On demand (`dlr spans`) |
| [ECCC HRDPS via dynamical.org](#eccc-hrdps-weather-forecasts) | Weather at every span, 0–48 h ahead | Every new run (CI, hourly check) |
| [ECCC GeoMet `climate-hourly`](#eccc-hourly-station-observations-geomet-climate-hourly) | Weather "truth" at two airports | Every refresh |
| [ECCC SWOB `swob-realtime`](#eccc-minute-observations-swob) | Minute wind for the wind check | Archived daily (CI) |
| [IESO Intertie Schedule and Flow](#ieso-intertie-flows) | Power actually flowing on the tie | Archived daily (CI) |
| [Public documents](#public-documents-behind-the-assumptions) | Conductor data, static rating rules | Manual |

---

## OpenStreetMap: line, towers, substations, river

- **Access:** public Overpass API mirrors (`overpass-api.de`,
  `overpass.kumi.systems`, `overpass.openstreetmap.fr`).
  - A descriptive `User-Agent` is required, or the request gets HTTP 406.
  - Mirrors return 504 under load; `src/dlr/line/overpass.py` retries with
    backoff and falls back to the next mirror.
  - Responses are cached in `data/raw/osm/`.
- **What is used:**
  - **Route:** ways `76608622` → `998555319` → `87817211`. The last one crosses
    the river and carries no `ref` tag.
  - **Substations:** Hawthorne TS (way `76734082`) and Poste de l'Outaouais
    (way `168607246`).
  - **Towers and river:** the 111 tower nodes along the route, and the Ottawa
    River centreline, which is the Ontario/Québec border here.
  - **Line tags:** `voltage=230000`, `circuits=2`, `cables=6`, `wires=double`
    (twin-conductor phases).
- **Licence:** ODbL 1.0, "© OpenStreetMap contributors". Data derived from it
  (towers, spans) is shared under the same licence.
- **Caveats:**
  - Many features come from CanVec or imagery tracing, not surveys.
  - Border-crossing ways are often untagged, so routes are given as explicit
    way lists and validated (connected, and ending at both substations).
  - Tower positions are accurate to a few metres.

## NRCan HRDEM: terrain

- **Access:** NRCan datacube STAC API
  `https://datacube.services.geo.ca/stac/api`, collection `hrdem-mosaic-1m`.
  - One item (`9_2-mosaic-1m`, 2025-05-05) covers the whole corridor on both
    banks.
  - Assets: `dtm` (bare ground) and `dsm` (surface including trees and
    buildings), cloud-optimized GeoTIFFs in `EPSG:3979` at 1 m; nodata −32767.
- **Reading:** only small windows around each span, through HTTP range
  requests with rasterio. Use `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` and
  `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif`.
  - A 200 m window takes about 2.4 s; all 110 spans take about 75 s with 8
    threads.
  - Windows are cached in `data/interim/hrdem/` (76 MB, not tracked).
- **Licence:** Open Government Licence – Canada 2.0. Attribution: "Contains
  information licensed under the Open Government Licence – Canada (HRDEM,
  Natural Resources Canada)".
- **Also checked:** the CanElevation LiDAR point-cloud index lists 4 projects
  over the corridor (Riviere_Outaouais_2019, Ottawa_Gatineau_2019/2020,
  Eastern_Ontario_Part2_2021). Point clouds are not needed; the rasters are
  derived from them.

## ECCC HRDPS: weather forecasts

- **Model:** Environment and Climate Change Canada's High Resolution
  Deterministic Prediction System.
  - Grid: 2.5 km, rotated-pole.
  - Runs at 00, 06, 12 and 18 UTC, each with hourly leads from 0 to 48 h.
- **Access:** dynamical.org's cloud-native archive, through
  `dynamical_catalog.open("eccc-hrdps-forecast")` (`dynamical-catalog` ≥ 1).
  - The catalog resolves the dataset; never hard-code its storage URL.
  - The study corridor falls in **one native storage chunk** (258 × 254
    cells), so each run costs one chunk per variable: ≈5 s per run, and about
    30–60 min for the full archive from scratch.
- **Variables used:**
  - `temperature_2m`, `dew_point_temperature_2m`;
  - `wind_speed_10m`, `wind_direction_10m`, `wind_speed_80m`,
    `wind_direction_80m`;
  - `downward_short_wave_radiation_flux_surface`, `pressure_surface`.
- **Coverage:** archive starts 2026-07-09T00Z and grows by 4 runs a day.
- **Latency:** a run appears about **5–10 h after it starts** (checked
  2026-10-14).
- **Licence:** CC BY 4.0 and the ECCC Data Servers End-use Licence v2.1.
  Required attribution: *"ECCC HRDPS data processed by dynamical.org from
  Environment and Climate Change Canada, used under the ECCC Data Servers
  End-use Licence version 2.1
  (https://eccc-msc.github.io/open-data/licence/readme_en/)"*.
- **Caveats:**
  - Wind directions are already relative to true north (the dataset says so);
    don't rotate them again.
  - Shortwave radiation is the **average over the previous hour**, so it is
    missing at lead 0.
  - `ingested_forecast_length` is empty in the archive. Whether the newest run
    is complete is checked by reading its last lead hour at one corridor cell.
  - The grid has 2D `latitude`/`longitude` and a `spatial_ref` coordinate with
    the rotated-pole definition, which is used for exact sampling.

## ECCC hourly station observations (GeoMet `climate-hourly`)

- **Access:** MSC GeoMet OGC API,
  `https://api.weather.gc.ca/collections/climate-hourly/items`.
- **Stations:**
  - OTTAWA INTL A (YOW, `6106001`, 114.9 m);
  - OTTAWA GATINEAU A (YND, `7032685`; id `7032682` is a partial duplicate);
  - both about 9 km from the line;
  - the history goes back decades.
- **Conventions:**
  - Use `UTC_DATE`; `LOCAL_DATE` is local *standard* time.
  - `WIND_SPEED` is km/h and `WIND_DIRECTION` is in tens of degrees; both are
    converted in `src/dlr/weather/geomet.py`.
  - The hourly wind is a **2-minute average at the top of the hour**, a
    snapshot rather than an hourly mean.
- **Licence:** ECCC Data Server End-use Licence. Attribution: "Contains
  information licenced under the Data Server End-use Licence of Environment
  and Climate Change Canada."

## ECCC minute observations (SWOB)

- **Access:** MSC GeoMet OGC API,
  `https://api.weather.gc.ca/collections/swob-realtime/items`.
  - Filter by `msc_id-value`, `datetime` and `sortby=date_tm-value`.
  - `properties=` keeps only the fields needed (20× smaller responses).
- **Station:** OTTAWA CDA RCS (CXOA, `6105978`), an automatic station 8.9 km
  from the line at the Central Experimental Farm. It reports **every minute**:
  1-, 2-, 10-minute and 1-hour average wind speed and direction at 10 m, plus
  temperature.
  - The airports above report only hourly and special observations.
  - No other station is closer to the line.
- **Retention:** about **30 days**. `dlr fetch-swob` stores each complete UTC
  day once in `data/obs/swob/CXOA/<day>.parquet`, and the daily archive
  workflow runs it.
- **Conventions:**
  - `date_tm-value` is UTC, at the end of the averaging minute.
  - Speeds are km/h.
  - A 1-minute value is kept only if its `-qa` flag is 100 (passed).
- **Licence:** as above.
- **Caveat:** one urban-edge field site at 10 m height. It represents open
  ground near Ottawa, not a forested corridor at conductor height.

## IESO intertie flows

- **Access:** `https://reports-public.ieso.ca/public/IntertieScheduleFlow/`,
  one XML file per day (`PUB_IntertieScheduleFlow_YYYYMMDD.xml`).
  - Each file holds hourly schedules (import/export) and 5-minute actual flows
    per intertie zone.
  - Positive flow means export from Ontario.
- **Retention:** a rolling ~3 months. `dlr fetch-ieso` archives every complete
  day for the Québec (`PQ.*`) zones as monthly CSVs in `data/ieso/`, and the
  daily archive workflow runs it. The archive starts 2026-07-14.
- **Zone used:** `PQ.AT`, the HVDC tie through A41T/A42T, with a 1,250 MW
  converter limit. The mapping to these circuits is *inferred* from the IESO
  planning outlook, the naming and the flow magnitudes; no IESO document lists
  circuits per zone.
- **Caveats:**
  - Hours are hour-ending and are assumed to be Eastern Standard Time all year
    (to be confirmed).
  - The redistribution terms of IESO public reports are not yet confirmed.

## Public documents behind the assumptions

| Document | Used for |
|---|---|
| Prysmian TransPowr ACSR datasheet (ASTM B232) | Conductor diameter, strands, resistance (Drake, Grosbeak, Cardinal) |
| Hydro One IMO-FORM-1538 v9.0; IESO CAA 2017-623 | Ontario continuous rating at 93 °C; summer 35 °C / winter 10 °C; wind 0–4 km/h; long-term emergency 127 °C |
| OEB East-West Tie design criteria (2011) | Emissivity 0.6, absorptivity 0.8; clearances |
| Hydro One project documents (St. Clair, Lake Superior Link) | 230 kV double-circuit towers 30–60 m tall |
| IESO 2022 Annual Planning Outlook, *Transmission Interfaces and Interties* | Ottawa-zone ties: one HVDC tie (two 230 kV circuits, 1,250 MW converter limit) and two radial points |
| Hitachi Energy, Outaouais case study | Converter: two 625 MW back-to-back blocks (2009) |
| CIGRE TB 601 (via `linerate`'s tests) | Thermal model and worked examples |

The exact values and links are in `config/conductor.yaml` and
`config/rating.yaml`.

## Sources considered and not used

- **Hydro-Québec open data and the Ontario GeoHub "Utility Line" layer:** no
  usable transmission-line geometry; the GeoHub layer is marked unmaintained.
- **NOAA HRRR:** covers the continental US only, not Ottawa.
- **The D5A/H9A circuits to Poste Masson,** the first candidate line. They are
  radial ties that carried no flow (see [METHODOLOGY.md](METHODOLOGY.md#the-line)).
- **The Beauharnois tie (`PQ.B5D.B31L`):** its Ontario-side route was never
  identified.
