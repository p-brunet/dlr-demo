# Data Sources

> Live-verified 2026-09-22 through 2026-09-25 (dates noted per source). Re-verify
> before each milestone that depends on a source, since dynamical.org's archive
> and OSM/IESO data are both live and growing.

## 1. Weather forecast — ECCC HRDPS via dynamical.org

- **Endpoints:** `https://stac.dynamical.org/catalog.json` (source of truth for
  dataset IDs — never hard-code buckets/hrefs/versions per dynamical.org's own
  agent guidance at `https://dynamical.org/prompt.md`); dataset opened via
  `dynamical_catalog.open("eccc-hrdps-forecast", chunks=None)`.
- **Client requirement:** `dynamical-catalog>=1` (PyPI latest at verification
  time: 1.0.1), Python 3.12+. Note: an earlier project draft said `>=0.8.0` —
  that is stale; `>=1` is what the live onboarding doc and PyPI both confirm.
- **License:** CC BY 4.0 + ECCC Data Servers End-use Licence v2.1
  (`https://eccc-msc.github.io/open-data/licence/readme_en/`).
  **Required attribution (verbatim, from the dataset's own `attrs`):**
  > ECCC HRDPS data processed by dynamical.org from Environment and Climate
  > Change Canada, used under the ECCC Data Servers End-use Licence version 2.1
  > (https://eccc-msc.github.io/open-data/licence/readme_en/)
- **Verified-on:** 2026-09-24, by actually opening the dataset (not just reading
  the catalog page).
- **Spatial coverage:** 2.5km grid, full domain `y=1290, x=2540` covering Canada
  + northern US; **104 grid cells** fall inside the recommended line's
  (corrected) study bbox (45.35–45.60N, -75.65 to -75.35W, `config/study.yaml`)
  — consistent with a ~2.5km cell size over that bbox's area. (An earlier,
  narrower-longitude bbox centered on the originally-assumed, incorrect
  Poste Vignan crossing gave 191 cells over a differently-shaped area; not
  comparable directly.)
- **Temporal coverage:** archive starts exactly `2026-07-09T00:00:00Z`
  (confirmed both via the catalog page and via `init_time` count: 306 inits ×
  6h steps ≈ the 76 days elapsed to 2026-09-24), open-ended; 4 inits/day, 0–48h
  hourly lead (49 steps).
- **Schema (confirmed by directly opening the dataset):**
  - dims: `init_time`, `lead_time`, `y`, `x`.
  - **17 data variables:** `temperature_2m`, `dew_point_temperature_2m`,
    `specific_humidity_2m`, `wind_speed_10m`, `wind_direction_10m`,
    `wind_speed_80m`, `wind_direction_80m`, `wind_gust_10m`,
    `downward_short_wave_radiation_flux_surface`,
    `downward_long_wave_radiation_flux_surface`,
    `total_cloud_cover_atmosphere`, `precipitation_surface`,
    `pressure_surface`, `pressure_reduced_to_mean_sea_level`,
    `snow_water_equivalent_surface`,
    `categorical_precipitation_type_surface`,
    `convective_available_potential_energy_surface`.
  - **Coordinates:** `init_time`, `lead_time`, `valid_time` (= `init_time` +
    `lead_time`), plus **genuine 2D `latitude`/`longitude` coordinate arrays**
    (dims `(y, x)`, float32) — so per-span sampling can index by real lat/lon
    without ever needing to hand-roll the rotated-pole math. A `spatial_ref`
    scalar coordinate additionally carries the full CF grid-mapping metadata
    (see below) for anyone who does need the native grid.
  - **CRS (resolved — this was an open gap as of the planning pass, now
    closed):** `spatial_ref.attrs` gives `grid_mapping_name:
    rotated_latitude_longitude`, `grid_north_pole_latitude: 36.08852`,
    `grid_north_pole_longitude: 65.305142`, on a spherical datum (radius
    6371229m, GRIB convention). Full `crs_wkt` and a `GeoTransform` are also
    present. Native grid extent: `x` in [-14.82, 42.31], `y` in [-12.30, 16.70]
    (rotated-grid degrees).
  - `wind_direction_10m`/`wind_direction_80m` are confirmed already de-rotated:
    attrs literally say *"Direction the wind blows from, clockwise from true
    north rather than from the rotated grid's north."*
  - Chunking (from the catalog page, not yet independently re-verified via
    `ds.chunks` since this was opened with `chunks=None`): `init_time=1,
    lead_time=49, y=258, x=254`.
- **Known caveats/gotchas:**
  - `convective_available_potential_energy_surface` (CAPE): negative values
    are a missing-data marker, not physical energies — mask `< -0.1` before use.
  - Radiation variables (`downward_short_wave_radiation_flux_surface`,
    `downward_long_wave_radiation_flux_surface`) are averages since the
    previous step, not instantaneous — do not treat lead-0 values as a normal
    instant sample (validation notes flag some variables as NaN at lead 0).
  - Never hard-code the S3/icechunk asset href or version — resolve via STAC.
- **Related datasets on the same platform** (not used in Milestone 1, noted
  for later): GFS, GEFS, ECMWF AIFS, ECMWF IFS ENS, DWD ICON-EU, NASA IMERG,
  and a "Global Airport Observations" METAR dataset (~2500 ASOS/AWOS stations,
  a possible Ottawa CYOW cross-check). **HRRR was checked and ruled out** — its
  own dynamical.org page states a CONUS-only domain (Lambert Conformal bbox
  that does not reach Ottawa), with no mention of Canada.

## 2. Reference observations — ECCC GeoMet (MSC OGC API)

- **Endpoint:** `https://api.weather.gc.ca/` — collections `climate-hourly`
  and `climate-stations` confirmed live and queryable.
- **License:** `TODO(verify)` — the exact license/terms URL for GeoMet's own
  API (as opposed to the HRDPS-specific licence above) was not captured
  during verification; check `https://api.weather.gc.ca/collections/climate-hourly?f=json`'s
  metadata or ECCC's general open-data terms before publishing derived figures.
- **Verified-on:** 2026-09-22.
- **Spatial coverage over the recommended line:** bbox query
  `-76.5,45.0,-75.0,46.0` on `climate-stations` returned 98 stations, including
  **Ottawa CDA (#4333, ON)** and **Ottawa Macdonald-Cartier Intl A (#4337,
  ON)**. A direct `climate-hourly` items query for 2026-09-20 on the same bbox
  returned real hourly observations from **"OTTAWA GATINEAU A" (QC)** and
  **"KEMPTVILLE CS" (ON)** — confirming cross-border reference data is
  queryable on both sides of the corridor. Note: the recommended line's actual
  QC endpoint (near Masson-Angers/Thurso, ~45.55N) is further east than this
  bbox — re-run the station search centered there before Milestone 3.
- **Temporal coverage:** national hourly archive back to 1953.
- **Known caveats:** station selection prioritizes 10k+ population cities /
  Regional Basic Climatological Network stations / 30+ year records — not
  every nearby station is guaranteed to exist.

## 3. Line geometry — OpenStreetMap (Overpass API)

- **Endpoint(s):** public Overpass mirrors (`overpass-api.de`,
  `overpass.kumi.systems`, `overpass.openstreetmap.fr`) — a descriptive
  `User-Agent` is required (406 without one) and the public instances 504
  under load, so the client in `src/dlr/line/overpass.py` retries with
  backoff and falls back across mirrors.
- **License:** ODbL — attribution "© OpenStreetMap contributors".
- **Verified-on:** 2026-09-22 (initial survey) and 2026-09-24–25 (direct
  geometry tracing of the recommended candidate — see the correction below).
- **IMPORTANT CORRECTION found during Milestone 1 execution:** the initial
  survey cross-validated candidate lines by matching OSM `ref` tags against
  live IESO intertie zone names (e.g. `D5A`/`H9A` on Hawthorne TS matching
  `PQ.D5A`/`PQ.H9A`) and inferred the nearest named Hydro-Québec substation
  (**Poste Vignan**, ~4km away) as the crossing point. **Direct geometry
  tracing disproved this**: the OSM ways actually tagged `D5A`/`H9A` (and the
  parallel `A41T`/`A42T` circuits) run **northeast** from Hawthorne TS, not
  west toward Poste Vignan. Tracing the full corridor (~30 way segments,
  fetched and cached at `data/raw/osm/ottawa_gatineau_ways.json`) shows it
  actually reaches Québec near **Poste Masson / Poste de l'Interconnexion-
  Maclaren** (Hydro-Québec, ~45.5497N -75.4248W, near Masson-Angers/Thurso QC)
  — about **17km** from Hawthorne TS, not the ~4km originally assumed. The
  actual border-crossing segment (way `87817195`, 230kV;115kV) carries no
  `ref`/`operator` tag at all — a common OSM mapping gap right at
  jurisdiction boundaries — and was identified by geometric continuity
  (its endpoints closely match the end of the Ontario-tagged cluster and the
  start of the Québec-tagged cluster), not by tag matching. **Lesson:**
  ref-tag matching against IESO zone names is a useful hint but not
  sufficient evidence on its own; always confirm with actual traced geometry
  before committing to a line.
- **Recommended line's confirmed geometry:**
  - Ontario endpoint: **Hawthorne TS** (Hydro One, 500/230/115kV,
    45.3890935N -75.5969060W).
  - Québec endpoint: **Poste Masson / Poste de l'Interconnexion-Maclaren**
    (Hydro-Québec, ~45.5497N -75.4248W).
  - Circuits: **D5A** (230kV) and **H9A** (115kV), traced across ~30 OSM way
    segments (many very short, reflecting per-tower tagging granularity).
    D5A and H9A share a single way for long combined-ref stretches, but are
    mapped as *separate* parallel ways for other stretches (12 pure-H9A-only
    ways) — merging all ~30 way segments into one geometry therefore
    double-counts those shared stretches (38.26km). The stored
    `line.geoparquet` geometry instead uses only the **16 D5A-bearing ways**
    (which include every combined-ref segment too) as the representative
    route: **21.69km**, still a `MultiLineString` (22 parts) reflecting small
    real mapping gaps between fragments, not a single clean polyline.
  - Tower count: **189**, from a deduplicated Overpass node count across all
    29 traced way ids (both circuits) — likely still somewhat inflated if
    D5A/H9A use separate physical structures on shared sections; ~115m
    average spacing over 21.69km is denser than typical for 230kV, so treat
    this as an upper-bound pending Milestone 2's actual span model.
  - A parallel pair of circuits, **A41T/A42T** (230kV, Hydro One), shares
    much of the same corridor but terminates within Ontario as far as traced;
    not included in the recommended line's geometry (documented as a nearby
    parallel circuit, not this line).
- **Data quality caveat:** most feature `source` tags are `CanVec 6.0/7.0`
  (NRCan) or Bing/Yahoo imagery-derived, not surveyed — treat voltage/cable
  counts as indicative, and note that a direct live fetch of way `314394063`
  (initially reported elsewhere as a 230kV HQ segment near Poste Vignan)
  actually returned `voltage=120000` on live query — a reminder that tag
  values should always be re-confirmed by direct fetch rather than trusted
  from an earlier summary.

## 4. Grid context — IESO Intertie Schedule and Flow

- **Endpoint:** `https://reports-public.ieso.ca/public/IntertieScheduleFlow/`
  — hourly-updated XML (`PUB_IntertieScheduleFlow_YYYYMMDD.xml` + intraday
  `_v1..v24`), root doc "Intertie Schedule and Flow Report".
- **License:** `TODO(verify)` — IESO's public report terms of use were not
  captured during verification; check the IESO website's terms before
  redistributing report contents.
- **Verified-on:** 2026-09-22.
- **Coverage:** one `<IntertieZone>` block per interconnection. All
  Québec-interface zone names confirmed: `PQ.AT`, `PQ.B5D.B31L`, `PQ.D4Z`,
  `PQ.D5A`, `PQ.H4Z`, `PQ.H9A`, `PQ.P33C`, `PQ.Q4C`, `PQ.X2Y`.
- **Temporal coverage:** hourly-updated, current-day files; historical
  retention window `TODO(verify)`.
- **Known caveat:** the zone-to-physical-line mapping used in this project is
  via naming convention only (ref tags happening to match zone names), which
  Milestone 1's own OSM tracing (§3 above) showed is a useful hint but **not
  proof of the exact physical crossing point** — treat the zone assignment as
  "very likely correct interconnection, geometry independently confirmed by
  direct tracing" rather than "officially documented mapping."

## 5. Terrain — NRCan CanElevation LiDAR

- **Endpoint(s):** public, no-sign-request S3 bucket
  `canelevation-lidar-point-clouds` (note: the AWS Open Data registry slug
  `canelevation-pointcloud` differs from the real bucket name); tile index
  `Index_LiDARtiles_tuileslidar.gpkg` (448MB); project index
  `Index_LiDARprojects_projetslidar.gpkg` (5.7MB); also queryable live via the
  ESRI REST layer
  `https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/lidar_point_cloud_canelevation_en/MapServer/0`
  (used by `src/dlr/lidar/index.py` instead of downloading the full GPKG for
  a bbox lookup). AOI browser:
  `https://nrcan.github.io/CanElevation/pointclouds/projects-tiles-by-aoi/`.
- **License:** Open Government Licence – Canada. Exact version/URL
  `TODO(verify)`.
- **Verified-on:** 2026-09-22.
- **Spatial coverage over the recommended line:** re-queried (2026-09-25)
  against the corrected corridor bbox (45.35-45.60N, -75.65 to -75.35W,
  covering Hawthorne TS to Poste Masson). **4 projects confirmed**:
  `Riviere_Outaouais_2019` (NRCan) and `Ottawa_Gatineau_2019`/`2020` (City of
  Ottawa) cover the Hawthorne TS end; `Eastern_Ontario_Part2_2021` (South
  Nation Conservation Authority) extends coverage toward the corrected,
  more northeasterly crossing point. Project-level intersection only — exact
  coverage *fraction* along the traced line still needs the tile-level
  buffer/intersection in Milestone 2.
- **Known caveat:** coverage *fraction* (buffer ∩ tile footprints) has not
  been computed — only project-level intersection has been confirmed.

## 6. Fallback elevation sources (not primary, noted for redundancy)

- Données Québec LiDAR + "Forêt ouverte" showcase; Ontario GeoHub
  LiDAR-derived DTM/DSM items; NRCan HRDEM per-project download directory
  (own footprint index files). All confirmed reachable 2026-09-22; per-source
  license/coverage over the specific corridor is `TODO(verify)` if
  CanElevation direct access ever fails.

---

## Candidates considered but not pursued

**Beauharnois corridor** (`PQ.B5D.B31L`, circuits `B-5-D`/`B-31-L`, 230kV):
tag-matched against IESO but the Ontario-side endpoint was never identified —
the bbox used in the initial survey didn't reach Ontario territory at all.
Given how badly tag-matching alone misled the Ottawa/Gatineau candidate (see
§3), this candidate would need the same direct-geometry-tracing treatment
before it could be responsibly recommended. Kept as a documented alternative,
not deleted, in case the owner wants a second demo line later.

No official Hydro-Québec or Ontario transmission-line open geometry dataset
exists (checked Hydro-Québec's open-data catalog directly — 26 datasets, none
are transmission-line geometry; Ontario GeoHub's "Utility Line" layer is
self-described as unmaintained: *"Not planned: there are no plans to update
the data."*). **OSM remains the best available open geometry source for this
project**, provided every candidate is confirmed by direct geometry tracing
rather than tag matching alone.
