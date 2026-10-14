"""Typed configuration for the DLR demonstrator.

Every value that isn't directly observed (conductor properties, static rating
assumptions, ...) is wrapped in `Sourced` so its provenance travels with the
value instead of living only in a comment.

`config/line.yaml` is *generated* by `dlr select-line` and must not be edited by
hand; curated inputs (conductor, rating assumptions) live in their own files so
regenerating the line cannot clobber them.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, PositiveFloat

Fraction = Annotated[float, Field(gt=0, le=1)]


class SourceKind(StrEnum):
    PUBLIC_DOC = "public_doc"
    OPEN_DATA = "open_data"
    INDUSTRY_TYPICAL = "industry_typical"
    USER_PROVIDED = "user_provided"
    COMPUTED = "computed"


class Sourced[T](BaseModel):
    value: T
    source: SourceKind
    reference: str | None = None


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class LineConfig(BaseModel):
    """Line metadata resolved from OSM by `dlr select-line` (generated file)."""

    line_id: str
    line_name: str
    operator: str | None = None
    voltage_kv: Sourced[float]
    circuits: Sourced[int]
    sub_conductors: Sourced[int]
    """Conductors per phase bundle (OSM `wires=double` -> 2)."""
    from_substation: str
    to_substation: str
    ieso_zones: list[str] = Field(default_factory=list)
    route_osm_way_ids: list[int]
    length_km: float
    tower_count: int
    osm_timestamp: str
    verified_on: str
    notes: str | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> LineConfig:
        return cls.model_validate(_load_yaml(path))


class ConductorSpec(BaseModel):
    """One sub-conductor, in SI units. Zero/negative placeholders fail validation."""

    code_word: str
    stranding: str
    conductor_diameter_m: Sourced[PositiveFloat]
    core_diameter_m: Sourced[PositiveFloat]
    outer_strand_diameter_m: Sourced[PositiveFloat]
    aluminium_area_m2: Sourced[PositiveFloat]
    resistance_ac_25c_ohm_per_m: Sourced[PositiveFloat]
    resistance_ac_75c_ohm_per_m: Sourced[PositiveFloat]


class ConductorConfig(BaseModel):
    """Curated conductor assumptions (`config/conductor.yaml`)."""

    base: str
    sensitivity: list[str]
    conductors: dict[str, ConductorSpec]
    emissivity: Sourced[Fraction]
    absorptivity: Sourced[Fraction]
    max_operating_temp_c: Sourced[PositiveFloat]
    max_operating_temp_sensitivity_c: list[PositiveFloat] = Field(default_factory=list)
    conductor_height_m: Sourced[PositiveFloat]
    """Mean conductor height above ground, for the wind profile."""

    @classmethod
    def from_yaml(cls, path: Path) -> ConductorConfig:
        return cls.model_validate(_load_yaml(path))

    def spec(self, name: str | None = None) -> ConductorSpec:
        return self.conductors[name or self.base]


class SeasonalAssumptions(BaseModel):
    ambient_c: Sourced[float]
    wind_speed_ms: Sourced[float]
    wind_angle_deg: Sourced[float]
    """Angle of attack relative to the span axis (90 = perpendicular)."""
    solar_date: str
    """Clear-sky solar heating is evaluated at local solar noon on this date."""


class RatingConfig(BaseModel):
    """Static / ambient-adjusted rating assumptions (`config/rating.yaml`)."""

    summer_months: list[int]
    summer: SeasonalAssumptions
    winter: SeasonalAssumptions
    summer_ambient_sensitivity_c: list[float] = Field(default_factory=list)
    min_attack_angle_sensitivity_deg: list[float] = Field(default_factory=list)
    ground_albedo: Sourced[Fraction]
    sheltering_max_reduction: Sourced[float]
    """Wind-speed reduction applied to a fully sheltered span (exposure index 0)."""
    min_wind_speed_ms: Sourced[float]
    """Floor on modelled wind at conductor height (forecast calm is not calm)."""

    @classmethod
    def from_yaml(cls, path: Path) -> RatingConfig:
        return cls.model_validate(_load_yaml(path))


class BoundingBox(BaseModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class StudyPeriod(BaseModel):
    start: str
    end: str


class WeatherConfig(BaseModel):
    variables: list[str] = Field(
        default_factory=lambda: [
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_speed_80m",
            "wind_direction_80m",
            "wind_gust_10m",
            "downward_short_wave_radiation_flux_surface",
            "downward_long_wave_radiation_flux_surface",
            "total_cloud_cover_atmosphere",
            "precipitation_surface",
            "pressure_surface",
            "dew_point_temperature_2m",
        ]
    )
    source: Literal["hrdps", "geomet_obs"] = "hrdps"


class PathsConfig(BaseModel):
    data_dir: Path = Path("data")
    cache_dir: Path = Path(".cache")
    output_dir: Path = Path("output")


class StudyConfig(BaseModel):
    period: StudyPeriod
    bbox: BoundingBox
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> StudyConfig:
        return cls.model_validate(_load_yaml(path))
