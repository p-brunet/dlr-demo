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
        "ottawa-gatineau", help="Registered candidate id (see src/dlr/line/candidates.py)."
    ),
    out: Path = typer.Option(DEFAULT_LINE_CONFIG, help="Where to write the resolved line config."),
) -> None:
    """Resolve a candidate's OSM geometry and write data/processed/line.geoparquet."""
    from dlr.line.select import select_line as _select_line

    _select_line(candidate=candidate, line_config_out=out)


@app.command("fetch")
def fetch(
    study: Path = typer.Option(DEFAULT_STUDY_CONFIG),
    dry_run: bool = typer.Option(
        True, help="Open the dataset and print its schema; no bulk download."
    ),
) -> None:
    """Open HRDPS via dynamical-catalog and print its dims/coords/variables/attrs."""
    from dlr.weather.hrdps import print_hrdps_schema

    study_cfg = StudyConfig.from_yaml(study)
    print_hrdps_schema(study_cfg, dry_run=dry_run)


@app.command("lidar")
def lidar(study: Path = typer.Option(DEFAULT_STUDY_CONFIG)) -> None:
    """List CanElevation LiDAR projects/tiles intersecting the study bbox."""
    from dlr.lidar.index import list_projects_for_bbox

    study_cfg = StudyConfig.from_yaml(study)
    projects = list_projects_for_bbox(study_cfg.bbox)
    print(f"{len(projects)} LiDAR project(s) intersect the study bbox:")
    for project in projects:
        print(f"  {project.get('provider')} | {project.get('project')} | {project.get('url')}")


@app.command("rate")
def rate() -> None:
    """Thermal rating (CIGRE TB 601 via linerate). Not yet implemented."""
    typer.echo("rate: not yet implemented (Milestone 4+)")


@app.command("verify")
def verify() -> None:
    """Forecast verification metrics and safety margins. Not yet implemented."""
    typer.echo("verify: not yet implemented (Milestone 5+)")


@app.command("export")
def export() -> None:
    """Zarr/GeoParquet/PMTiles/GeoLibre export, figures and manifest. Not yet implemented."""
    typer.echo("export: not yet implemented (Milestone 6+)")


@app.command("publish")
def publish() -> None:
    """Publish dashboard artifacts. Not yet implemented."""
    typer.echo("publish: not yet implemented (Milestone 6+)")


@app.command("blog-sync")
def blog_sync() -> None:
    """Sync the post draft, figures and thumbnail into a local my-blog clone. Not implemented."""
    typer.echo("blog-sync: not yet implemented (Milestone 7+)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
