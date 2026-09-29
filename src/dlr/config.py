"""Typed configuration for the DLR demonstrator.

Every assumption that isn't directly observed (conductor properties, static
ratings, span geometry not derived from LiDAR, ...) is wrapped in `Sourced`
so its provenance travels with the value instead of living only in a comment.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SourceKind(StrEnum):
    PUBLIC_DOC = "public_doc"
    INDUSTRY_TYPICAL = "industry_typical"
    USER_PROVIDED = "user_provided"
    COMPUTED = "computed"


class Sourced[T](BaseModel):
    value: T
    source: SourceKind
    reference: str | None = None


class ConductorSpec(BaseModel):
    name: str
    diameter_m: Sourced[float]
    resistance_dc_20c_ohm_per_m: Sourced[float]
    absorptivity: Sourced[float]
    emissivity: Sourced[float]
    max_operating_temp_c: Sourced[float]
    static_rating_a: Sourced[float] | None = None


class SpanGeometry(BaseModel):
    span_id: str
    start_lonlat: tuple[float, float]
    end_lonlat: tuple[float, float]
    azimuth_deg: Sourced[float] | None = None
    conductor_height_m: Sourced[float] | None = None


class LineConfig(BaseModel):
    line_name: str
    operator: str | None = None
    osm_way_ids: list[int] = Field(default_factory=list)
    official_geometry_path: Path | None = None
    conductor: ConductorSpec
    spans: list[SpanGeometry] = Field(default_factory=list)
    notes: str | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> LineConfig:
        return cls.model_validate(yaml.safe_load(path.read_text()))


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
        return cls.model_validate(yaml.safe_load(path.read_text()))
