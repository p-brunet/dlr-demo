# Dynamic Line Rating Demonstrator — Project Plan

## 1. Overview

A reproducible, open-data demonstrator that quantifies and explains Dynamic
Line Rating (DLR) on one real transmission line between Ontario and Québec,
inspired by sensorless, forecast-based DLR approaches: no line sensors, only
weather + line geometry from open data. Results are published to a GeoLibre
web map and a blog post at `p-brunet.github.io/my-blog`.

## 2. Milestones

| # | Deliverable | Gate |
|---|---|---|
| 1 | `docs/PLAN.md`, `docs/DATA_SOURCES.md` with live-verified sources; candidate line table; recommended line | My approval |
| 2 | Span model + LiDAR corridor subset + derived span attributes (map preview) | Visual check |
| 3 | HRDPS extraction for the line (rotated-grid sampler tested), reference obs pipeline | Tests |
| 4 | Thermal rating: static vs reference DLR, bottleneck attribution | CIGRE regression passes |
| 5 | Forecast DLR 0–48h, verification metrics, safety margin | Metrics report in docs/ |
| 6 | Zarr/GeoParquet exports + GeoLibre project + story map, published link | Link works in a clean browser |
| 7 | Blog post draft + figures + thumbnail, blog-sync into a local clone of my-blog, myst build green | My review, I open the PR |
| 8 | (Optional) IESO flow overlay, multi-model/ensemble, ML post-processing | — |

## 3. Milestone 1 detail (this deliverable)

**Scope:** `docs/PLAN.md`, `docs/DATA_SOURCES.md`, the candidate comparison
table below, `data/processed/line.geoparquet` — then **stop for manual
approval** before Milestone 2 (LiDAR span attributes, thermal modelling).

**Recommended line:** Hawthorne TS (Hydro One, Ottawa ON) ↔ Poste Masson /
Poste de l'Interconnexion-Maclaren (Hydro-Québec, near Masson-Angers/Thurso
QC), carrying circuits **D5A** (230kV) and **H9A** (115kV). See §4 for the
comparison table and §5 for how this differs from the corridor first assumed
during planning.

**Explicitly out of scope for M1:** opening/sampling HRDPS at span resolution
(only a schema probe was done, see `docs/DATA_SOURCES.md` §1), computing any
DLR value, LiDAR point-cloud streaming (only project-index lookup), any
dashboard work.

## 4. Candidate comparison table

| Field | Ottawa/Gatineau (recommended) | Beauharnois (not pursued) |
|---|---|---|
| ON substation | Hawthorne TS (Hydro One), 500/230/115kV | `TODO(verify)` — not identified |
| QC substation | Poste Masson / Poste de l'Interconnexion-Maclaren (Hydro-Québec) | Poste des Cèdres (candidate only, unconfirmed) |
| IESO zone(s) | `PQ.D5A`, `PQ.H9A` | `PQ.B5D.B31L` |
| OSM refs | D5A, H9A (+ parallel A41T/A42T, not included) | B-5-D, B-31-L |
| Voltage | 230kV (D5A) + 115kV (H9A), two circuits sharing a corridor | 230kV / 315kV, circuit unclear |
| Length | **21.69 km** (D5A-tagged route only, 16 of the ~30 traced way segments — H9A-only segments excluded to avoid double-counting a parallel same-corridor conductor; the naive merge of all 30 segments gives 38.26km, an overcount) | `TODO(verify)` — no confirmed endpoint |
| Tower count | **189** (deduplicated node count across all 29 traced ways, both circuits — likely includes some double-counting where D5A/H9A are mapped as separate structures on a shared corridor; treat as an upper-bound estimate pending Milestone 2's span model) | `TODO(verify)` |
| LiDAR coverage | Re-confirmed against the corrected corridor: `Riviere_Outaouais_2019`, `Ottawa_Gatineau_2019/2020`, `Eastern_Ontario_Part2_2021` (project-level only, tile-fraction still M2) | Partial (QC side only) |
| HRDPS grid cells | 104 cells (recomputed against the corrected `config/study.yaml` bbox, 2026-09-25) | Not computed |
| Flow data | Yes — `PQ.D5A`/`PQ.H9A` live in IESO reports | Yes — `PQ.B5D.B31L` live, but circuit mapping incomplete |
| Cross-validation confidence | **High, and geometry-confirmed** (not just tag-matched — see §5) | Medium — one circuit pair tag-matched only, no geometry check done |
| Verified-on | 2026-09-22 (tags), 2026-09-24–25 (geometry) | 2026-09-22 |

## 5. Important correction made during Milestone 1 execution

The initial research pass recommended Hawthorne TS ↔ **Poste Vignan** based on
proximity and ref-tag matching against IESO zone codes. While actually
building `line.geoparquet`, direct OSM geometry tracing showed this was
**wrong**: the D5A/H9A-tagged ways run northeast from Hawthorne TS, not west
toward Poste Vignan, and the real crossing is ~17km away near Poste Masson /
Poste de l'Interconnexion-Maclaren. Full trail in `docs/DATA_SOURCES.md` §3.
This is exactly the failure mode the project's "verify everything live, never
trust a candidate list blindly" rule is meant to catch — recorded here rather
than quietly overwritten so the correction is visible.

## 6. Open questions / TODO(verify) register

- **[M1, resolved]** ~~Re-run the LiDAR project-index bbox query against the
  corrected corridor~~ — done 2026-09-25, 4 projects confirmed (§4).
- **[M1, resolved]** ~~Widen `config/study.yaml`'s bbox to the corrected
  corridor and recompute the HRDPS grid-cell count~~ — done 2026-09-25
  (104 cells).
- **[M2]** Reconcile the tower count (189, across both D5A and H9A way ids)
  against the deduplicated route length (21.69km, D5A only) — at ~115m
  average spacing this is dense for 230kV and may double-count towers where
  D5A/H9A are mapped as separate structures sharing one physical corridor;
  the real per-span model in M2 should settle this.
- **[M2]** Whether A41T/A42T (parallel Hydro One circuits sharing the same
  corridor) should be modelled alongside D5A/H9A, or kept as a documented
  but excluded parallel circuit as done here.
- **[M2]** Candidate 2 (Beauharnois): Ontario-side substation still
  unidentified; deferred unless the owner asks to revisit it.
- **[docs/DATA_SOURCES.md]** Exact license/terms text for ECCC GeoMet and for
  IESO's public reports.
- **[M3]** HRDPS rotated-pole geo-referencing is now resolved (2D lat/lon
  coordinate arrays + a `spatial_ref` CF grid-mapping variable, see
  `docs/DATA_SOURCES.md` §1) — no longer open, but the actual span-level
  sampler using it is still unbuilt.

## 7. Blog post conventions (for later milestones)

Distilled from reading `p-brunet/my-blog` directly (README, `myst.yml`, two
existing posts, `data-sources.md`) so later milestones don't need to re-derive
it:

- **Category fit:** a DLR post fits best under `projects/modelling.md`
  ("Spatial and energy modelling — demand forecasting, network analysis and
  machine learning"); consider cross-listing under `projects/maps.md` too.
- **`data-sources.md`** on the blog uses a flat `## Category` → bullet list
  with some empty placeholder sections (Renewables, Mobility) ready to
  extend; new DLR sources fit under a new "Weather" heading or the existing
  "Energy/Infrastructure" one.
- **Frontmatter convention:** `title`, `date`, `authors: [{name, url}]`,
  `description` (YAML block scalar), `thumbnail` (a `raw.githubusercontent.com`
  URL on the *project's own* repo, not copied into the blog repo), `tags`,
  `keywords`.
- **Narrative flow:** hook/motivation → data sources (often a `{grid}` of
  `{card}` blocks) → method/pipeline → results (with figures) → limitations
  → a `| Tool | Role |` stack table → source-code link.
- **Embedding precedent:** the existing `solar-mtl` post explicitly **links
  out** to a heavy interactive Folium map rather than embedding it in-page,
  citing page weight — directly relevant to the eventual GeoLibre dashboard
  embed decision (Milestone 6/7): plan to default to linking out unless the
  embed is demonstrably light.
