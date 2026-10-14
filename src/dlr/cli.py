from __future__ import annotations

from pathlib import Path

import typer

from dlr.config import StudyConfig

app = typer.Typer(help="Dynamic Line Rating (DLR) demonstrator CLI.")

DEFAULT_LINE_CONFIG = Path("config/line.yaml")
DEFAULT_STUDY_CONFIG = Path("config/study.yaml")


@app.command("select-line")
def select_line(
    candidate: str = typer.Option(
        "hawthorne-outaouais", help="Registered candidate id (see src/dlr/line/candidates.py)."
    ),
    out: Path = typer.Option(DEFAULT_LINE_CONFIG, help="Where to write the resolved line config."),
    refresh: bool = typer.Option(False, help="Re-query Overpass instead of using the cache."),
) -> None:
    """Resolve a candidate's OSM route; write line/tower GeoParquet and config/line.yaml."""
    from dlr.line.select import select_line as _select_line

    _select_line(candidate=candidate, line_config_out=out, refresh=refresh)


@app.command("spans")
def spans(
    study: Path = typer.Option(DEFAULT_STUDY_CONFIG),
    conductor: Path = typer.Option(Path("config/conductor.yaml")),
    terrain: bool = typer.Option(True, help="Add HRDEM terrain attributes (network)."),
) -> None:
    """Build the span model (and terrain attributes) from data/processed/towers.geoparquet."""
    import geopandas as gpd

    from dlr.config import ConductorConfig, LineConfig
    from dlr.line.select import TOWERS_PATH
    from dlr.line.spans import SPANS_PATH, build_spans, fetch_river

    study_cfg = StudyConfig.from_yaml(study)
    line_cfg = LineConfig.from_yaml(DEFAULT_LINE_CONFIG)
    from dlr.line.candidates import CANDIDATES

    cand = CANDIDATES[line_cfg.line_id]
    towers = gpd.read_parquet(TOWERS_PATH)
    span_gdf = build_spans(
        towers, fetch_river(study_cfg.bbox), cand.from_substation.region, cand.to_substation.region
    )
    if terrain:
        from dlr.terrain.hrdem import add_terrain

        height = ConductorConfig.from_yaml(conductor).conductor_height_m.value
        towers, span_gdf = add_terrain(towers, span_gdf, conductor_height_m=height)
        towers.to_parquet(TOWERS_PATH)
    span_gdf.to_parquet(SPANS_PATH)
    typer.echo(f"wrote {SPANS_PATH} ({len(span_gdf)} spans)" + (" and terrain" if terrain else ""))


@app.command("fetch")
def fetch(
    study: Path = typer.Option(DEFAULT_STUDY_CONFIG),
    dry_run: bool = typer.Option(
        True, help="Open the dataset and print its schema; no bulk download."
    ),
    period_only: bool = typer.Option(
        False, help="Only the inits covering the study period (thin slice), not the archive."
    ),
) -> None:
    """HRDPS via dynamical-catalog: print the schema, or extract the corridor subset."""
    from dlr.weather.hrdps import extract_corridor, print_hrdps_schema

    study_cfg = StudyConfig.from_yaml(study)
    if dry_run:
        print_hrdps_schema(study_cfg)
    else:
        extract_corridor(study_cfg, period_only=period_only)


@app.command("lidar")
def lidar(study: Path = typer.Option(DEFAULT_STUDY_CONFIG)) -> None:
    """List CanElevation LiDAR projects/tiles intersecting the study bbox."""
    from dlr.lidar.index import list_projects_for_bbox

    study_cfg = StudyConfig.from_yaml(study)
    projects = list_projects_for_bbox(study_cfg.bbox)
    print(f"{len(projects)} LiDAR project(s) intersect the study bbox:")
    for project in projects:
        print(f"  {project.get('provider')} | {project.get('project')} | {project.get('url')}")


@app.command("fetch-ieso")
def fetch_ieso(
    out: Path = typer.Option(Path("data/ieso"), help="Archive directory (monthly CSVs)."),
    zone_prefix: str = typer.Option("PQ.", help="Keep intertie zones starting with this."),
) -> None:
    """Archive IESO Intertie Schedule and Flow days not yet stored (IESO keeps ~3 months)."""
    from dlr.grid.ieso import archive

    added = archive(archive_dir=out, zone_prefix=zone_prefix)
    if added:
        typer.echo(f"archived {len(added)} day(s): {added[0]} .. {added[-1]}")
    else:
        typer.echo("archive already up to date")


@app.command("fetch-swob")
def fetch_swob(
    out: Path = typer.Option(Path("data/obs/swob"), help="Archive directory (daily Parquet)."),
) -> None:
    """Archive SWOB minute observations (CXOA) not yet stored (SWOB keeps ~30 days)."""
    from dlr.weather.swob import archive

    written = archive(archive_dir=out)
    if written:
        typer.echo(f"archived {len(written)} day(s): {written[0].stem} .. {written[-1].stem}")
    else:
        typer.echo("archive already up to date")


@app.command("fetch-obs")
def fetch_obs() -> None:
    """Refresh YOW/YND hourly station observations for the whole HRDPS archive period."""
    from dlr.pipeline import refresh_station_obs
    from dlr.weather.geomet import OBS_PATH

    typer.echo(f"wrote {refresh_station_obs()} hours to {OBS_PATH}")


@app.command("refresh")
def refresh(
    force: bool = typer.Option(False, help="Recompute and re-export even without a new run."),
) -> None:
    """Continuous forecast: extract new HRDPS runs, recompute ratings/verification, export site."""
    import json
    import os

    from dlr.pipeline import refresh as _refresh

    result = _refresh(force=force)
    typer.echo(json.dumps(result))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        published = result["new_runs"] > 0 or force
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"new_runs={result['new_runs']}\npublish={'true' if published else 'false'}\n")


@app.command("rate")
def rate(
    rebuild_weather: bool = typer.Option(
        True, help="Re-sample span weather from the HRDPS corridor store first."
    ),
) -> None:
    """Static / AAR / DLR reference ratings per span and hour (CIGRE TB 601 via linerate)."""
    import json

    from dlr.thermal.run import LINE_REFERENCE_PATH, SUMMARY_PATH, run_reference

    summary = run_reference(rebuild_weather=rebuild_weather)
    typer.echo(json.dumps({k: summary[k] for k in ("static_line_a", "dlr_over_static")}))
    typer.echo(f"wrote {LINE_REFERENCE_PATH} and {SUMMARY_PATH}")


@app.command("verify")
def verify() -> None:
    """Forecast DLR 0-48 h, layered verification (reference + stations), safety margins."""
    import json

    from dlr.verify.run import OUT_DIR, run_verification

    summary = run_verification()
    typer.echo(json.dumps(summary["variants"]["base"]["test"], indent=1))
    typer.echo(f"wrote {OUT_DIR}/")


@app.command("verify-wind")
def verify_wind() -> None:
    """Wind check with SWOB minute observations: fair skill, direction spread, effective cooling."""
    import json

    from dlr.verify.wind import OUT_DIR, run_wind_check

    summary = run_wind_check()
    keys = ("hours", "effective_over_vector", "fitted_floor_deg", "station_margin")
    typer.echo(json.dumps({k: summary[k] for k in keys}, indent=1))
    typer.echo(f"wrote {OUT_DIR}/wind_*")


@app.command("figures")
def figures() -> None:
    """Regenerate every figure in docs/figures/ from the processed outputs."""
    from dlr.export.figures import render_all

    for path in render_all():
        typer.echo(f"wrote {path}")


@app.command("export")
def export() -> None:
    """Write site/data/: viewer files, GeoParquet/Parquet/Zarr exports and manifest."""
    from dlr.export.site import DATA_DIR, build_site

    manifest = build_site()
    typer.echo(f"wrote {len(manifest['files'])} files to {DATA_DIR}")


@app.command("publish")
def publish(port: int = typer.Option(8000, help="Local port for the preview server.")) -> None:
    """Serve site/ locally for a clean-browser check (publishing itself is a git push)."""
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory="site")
    typer.echo(f"serving site/ on http://localhost:{port} (Ctrl+C to stop)")
    typer.echo("to publish: push to main; .github/workflows/forecast.yml rebuilds and deploys it")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


@app.command("blog-sync")
def blog_sync(
    blog: Path = typer.Option(Path("../my-blog"), help="Local clone of p-brunet/my-blog."),
) -> None:
    """Copy the post draft and its figures into a local my-blog clone (no git operations)."""
    from dlr.export.blog import sync

    for path in sync(blog):
        typer.echo(f"wrote {path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
