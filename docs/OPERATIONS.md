# Continuous forecast — how it runs

> Since 2026-10-14 the dashboard opens on a **48 h line-rating forecast**
> that is rebuilt from every new Environment Canada HRDPS run, published to
> GitHub Pages without anyone running anything.

## What runs, and when

| Workflow | When | What it does |
|---|---|---|
| `.github/workflows/forecast.yml` — *Forecast and publish* | every hour at :25; on pushes to `src/`, `config/`, `site/index.html`; by hand | `dlr refresh`. If a new HRDPS run exists (or the run is forced), it rebuilds `site/` and deploys it to Pages; otherwise it exits in about a minute |
| `.github/workflows/open-data-archive.yml` — *Daily open-data archive* | daily 09:17 UTC; by hand | Commits new days of IESO intertie flows and SWOB minute wind; both sources only keep a rolling window |

## Latency: how fresh the forecast is

- HRDPS runs at 00, 06, 12 and 18 UTC. dynamical.org publishes each run about
  **5–10 h after its start** (observed on 2026-10-14: the 06Z run was there at
  16:09 UTC, the 12Z run was not).
- `dlr refresh` uses a run as soon as its last lead hour is ingested. It
  checks one corridor cell rather than skipping the newest run.
- So when a run arrives, roughly its first 5–10 lead hours are already in the
  past. The dashboard starts at "now", greys out the past, and covers about
  the next **38–43 h**.
- Faster option (not built): read HRDPS GRIB2 directly from ECCC's MSC
  Datamart, about 3–4 h after the run starts.

## What one refresh does (`src/dlr/pipeline.py`)

1. Extract the new HRDPS runs for the corridor, one storage chunk per variable
   per run.
2. Re-fetch YOW/YND station observations. If that fails, keep the previous
   file; it only feeds verification.
3. Recompute the reference ratings on the extended pseudo-analysis.
4. Recompute forecast ratings for every run and the verification report.
5. Re-run the SWOB wind check if its archive is present.
6. Export `site/data/`, including `forecast/` for the newest run:
   - line forecast and **secure** rating per lead hour;
   - span ratios;
   - wind at each span;
   - the wind particle grid.

Locally that takes about 4 minutes. In CI add environment setup and the
GeoLibre build (`scripts/build_geolibre.py`).

## What is refit, and when

| Item | Rule |
|---|---|
| Safety margin per lead hour (98 %) | Refit **at most once per UTC day**, on every forecast/truth pair verified so far (expanding window, so only past data). Leads under 7 h take the lead-7 value. Stored in `data/interim/margins_operational.json` |
| Static baseline | **Seasonal**: summer values May–October (35 °C), winter values November–April (10 °C, December sun). The switch happens on 1 November |
| Reference ratings, verification, wind check | Recomputed on every refresh (cheap) |
| Backtest numbers in `docs/RESULTS.md` | A dated snapshot (fixed Jul–Aug / Sep–Oct split). `data/processed/verification/summary.json` has the latest; update the doc by hand before quoting new numbers |
| Static figures, `docs/*.md`, blog | Not refreshed automatically |

## State, storage and cost

- **Between CI runs:** the HRDPS corridor extract and today's margins live in
  the GitHub Actions cache, not in git.
  - A lost or evicted cache means the next run re-extracts the whole archive
    (30–60 min) and carries on.
  - The first run after you enable the workflow does exactly that.
- **In git:** code, configs, the archives committed daily (IESO ≈25 MB/yr,
  SWOB ≈12 MB/yr), and the processed snapshot.
- **Not in git:** `site/data/`, `site/geolibre.html` and
  `site/dlr.geolibre.json` are generated in CI and deployed straight to Pages.
  To preview locally: `dlr refresh --force` (or `dlr export`), then
  `dlr publish`.
- **Cost:** free on a public repo (Actions minutes and Pages). About 4 heavy
  runs a day, plus 20 one-minute checks.

## Monitoring and failure modes

- The forecast view shows the HRDPS run time and when it was published.
  - After 18 h without a new run it shows **"older than usual"**.
  - Once the run's 48 h are over it shows **"forecast has run out"**.
- A failed run shows in the Actions tab. GitHub emails the person who last
  edited the workflow when a scheduled run fails.
- dynamical.org late or down: no new run, so no deploy. The page keeps the
  last forecast with its age shown.
- GitHub can delay scheduled runs by tens of minutes. It also disables
  schedules after 60 days without repository activity; the daily archive
  commits prevent that.

## Limits

This is a demonstrator. The conductor and static assumptions are typical
public values, not the operator's. There is no service level. Each published
rating is an estimate from a 2.5 km forecast with a statistical margin, not a
measured line state.
