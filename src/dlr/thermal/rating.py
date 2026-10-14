"""Steady-state thermal ratings with linerate (CIGRE TB 601), per span and hour.

Three tiers share one code path:

- **static**: seasonal ambient, fixed perpendicular wind, clear-sky sun at
  solar noon on a reference date (config/rating.yaml);
- **AAR** (ambient-adjusted): as static, but hourly ambient temperature and
  clear-sky sun at the actual hour (no solar heating at night);
- **DLR**: full weather -- wind at conductor height (optionally reduced by
  terrain sheltering), wind direction, ambient, and HRDPS shortwave.

Measured shortwave enters as a cloudiness factor on CIGRE's clear-sky
conductor irradiance (`Cigre601MeasuredGHI`): the span/sun geometry stays
CIGRE's, the clear-sky magnitude is scaled by HRDPS GHI / clear-sky GHI.

Ratings are per phase bundle (linerate multiplies by `num_conductors`), i.e.
the current one circuit can carry.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from linerate import Conductor, Span, Tower, Weather
from linerate.equations import solar_angles
from linerate.equations.cigre601 import solar_heating as cigre_solar
from linerate.models.cigre601 import Cigre601

from dlr.config import ConductorConfig, ConductorSpec, LineConfig, RatingConfig

MAX_AMPACITY_PER_SUBCONDUCTOR_A = 6000.0
MIN_CLEAR_SKY_GHI = 20.0  # W/m2; below this the cloudiness factor is ill-defined
MAX_CLOUDINESS_FACTOR = 1.2


def linerate_conductor(spec: ConductorSpec, cfg: ConductorConfig) -> Conductor:
    return Conductor(
        core_diameter=spec.core_diameter_m.value,
        conductor_diameter=spec.conductor_diameter_m.value,
        outer_layer_strand_diameter=spec.outer_strand_diameter_m.value,
        emissivity=cfg.emissivity.value,
        solar_absorptivity=cfg.absorptivity.value,
        temperature1=25.0,
        temperature2=75.0,
        resistance_at_temperature1=spec.resistance_ac_25c_ohm_per_m.value,
        resistance_at_temperature2=spec.resistance_ac_75c_ohm_per_m.value,
        aluminium_cross_section_area=spec.aluminium_area_m2.value,
        # the datasheet AC resistances already approximate core magnetisation
        constant_magnetic_effect=1.0,
        current_density_proportional_magnetic_effect=0.0,
        max_magnetic_core_relative_resistance_increase=1.0,
    )


def linerate_span(
    spans: pd.DataFrame,
    towers: pd.DataFrame,
    conductor: Conductor,
    sub_conductors: int,
    conductor_height_m: float,
) -> Span:
    """One vectorised Span with tower arrays shaped (n_spans, 1)."""
    t = towers.set_index("tower_seq")

    def tower(column: str) -> Tower:
        seq = spans[column].to_numpy()
        return Tower(
            longitude=t.geometry.x.loc[seq].to_numpy()[:, None],
            latitude=t.geometry.y.loc[seq].to_numpy()[:, None],
            altitude=(t["ground_alt_m"].loc[seq].to_numpy() + conductor_height_m)[:, None],
        )

    return Span(
        conductor=conductor,
        start_tower=tower("from_tower_seq"),
        end_tower=tower("to_tower_seq"),
        num_conductors=sub_conductors,
    )


class Cigre601MeasuredGHI(Cigre601):
    """CIGRE TB 601 with clear-sky conductor irradiance scaled by measured GHI."""

    def __init__(self, span: Span, weather: Weather, time: np.ndarray, ghi: np.ndarray, **kw):
        super().__init__(span, weather, time, **kw)
        self.ghi = np.asarray(ghi, dtype=float)

    def compute_global_radiation_intensity(self) -> np.ndarray:
        clear_on_conductor = super().compute_global_radiation_intensity()  # clearness_ratio = 1
        sin_h = solar_angles.compute_sin_solar_altitude_for_span(self.span, self.time)
        direct = cigre_solar.compute_direct_solar_radiation(
            sin_h, 1.0, self.span.conductor_altitude
        )
        diffuse = cigre_solar.compute_diffuse_sky_radiation(direct, sin_h)
        clear_horizontal = direct * np.maximum(sin_h, 0.0) + diffuse
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(
                clear_horizontal > MIN_CLEAR_SKY_GHI, self.ghi / clear_horizontal, 0.0
            )
        return np.asarray(clear_on_conductor * np.clip(factor, 0.0, MAX_CLOUDINESS_FACTOR))


def _solve(model: Cigre601, max_temp_c: float, sub_conductors: int) -> np.ndarray:
    ampacity = model.compute_steady_state_ampacity(
        max_temp_c,
        min_ampacity=0.0,
        max_ampacity=MAX_AMPACITY_PER_SUBCONDUCTOR_A * sub_conductors,
        tolerance=0.5,
    )
    return np.asarray(ampacity)


@dataclass
class RatingInputs:
    span: Span
    span_azimuth_rad: np.ndarray  # (n_spans, 1)
    sub_conductors: int
    max_temp_c: float
    ground_albedo: float


def build_inputs(
    spans: pd.DataFrame,
    towers: pd.DataFrame,
    line: LineConfig,
    conductor_cfg: ConductorConfig,
    rating_cfg: RatingConfig,
    conductor: str | None = None,
    max_temp_c: float | None = None,
) -> RatingInputs:
    spec = conductor_cfg.spec(conductor)
    n = line.sub_conductors.value
    span = linerate_span(
        spans,
        towers,
        linerate_conductor(spec, conductor_cfg),
        n,
        conductor_cfg.conductor_height_m.value,
    )
    return RatingInputs(
        span=span,
        span_azimuth_rad=np.asarray(span.conductor_azimuth),
        sub_conductors=n,
        max_temp_c=max_temp_c or conductor_cfg.max_operating_temp_c.value,
        ground_albedo=rating_cfg.ground_albedo.value,
    )


def _solar_noon_utc(date: str, longitude_deg: float) -> np.datetime64:
    return np.datetime64(pd.Timestamp(date) + pd.Timedelta(hours=12 - longitude_deg / 15.0), "m")


def static_rating(
    inp: RatingInputs,
    rating_cfg: RatingConfig,
    season: str = "summer",
    ambient_c: float | None = None,
) -> np.ndarray:
    """Per-span static rating (A), shape (n_spans,)."""
    a = getattr(rating_cfg, season)
    mean_lon = float(np.mean(inp.span.start_tower.longitude))
    time = _solar_noon_utc(a.solar_date, mean_lon)
    weather = Weather(
        air_temperature=ambient_c if ambient_c is not None else a.ambient_c.value,
        wind_direction=inp.span_azimuth_rad + np.deg2rad(a.wind_angle_deg.value),
        wind_speed=a.wind_speed_ms.value,
        ground_albedo=inp.ground_albedo,
        clearness_ratio=1.0,
    )
    return _solve(Cigre601(inp.span, weather, time), inp.max_temp_c, inp.sub_conductors)[:, 0]


def aar_rating(
    inp: RatingInputs,
    rating_cfg: RatingConfig,
    air_temperature: np.ndarray,
    times: np.ndarray,
    season: str = "summer",
) -> np.ndarray:
    """Ambient-adjusted rating (A), shape (n_spans, n_times)."""
    a = getattr(rating_cfg, season)
    weather = Weather(
        air_temperature=air_temperature,
        wind_direction=np.broadcast_to(
            inp.span_azimuth_rad + np.deg2rad(a.wind_angle_deg.value), air_temperature.shape
        ),
        wind_speed=a.wind_speed_ms.value,
        ground_albedo=inp.ground_albedo,
        clearness_ratio=1.0,
    )
    mid_hour = (times - np.timedelta64(30, "m")).astype("datetime64[m]")[None, :]
    return _solve(Cigre601(inp.span, weather, mid_hour), inp.max_temp_c, inp.sub_conductors)


def dlr_rating(
    inp: RatingInputs,
    wx: dict[str, np.ndarray],
    times: np.ndarray,
    wind_factor: np.ndarray | float = 1.0,
    clear_sky: bool = False,
) -> np.ndarray:
    """Dynamic rating (A), shape (n_spans, n_times); NaN where any input is missing.

    `wx` holds (n_spans, n_times) arrays: air_temperature, wind_speed,
    wind_direction (degrees, from), ghi (W/m2, mean over the hour ending at t).
    `clear_sky=True` ignores `ghi` and uses CIGRE clear-sky sun at each hour.
    """
    fields = ("air_temperature", "wind_speed", "wind_direction") + (() if clear_sky else ("ghi",))
    missing = np.zeros(wx["air_temperature"].shape, dtype=bool)
    for f in fields:
        missing |= ~np.isfinite(wx[f])
    clean = {f: np.where(missing, 0.0, wx[f]) for f in fields}
    weather = Weather(
        air_temperature=clean["air_temperature"],
        wind_direction=np.deg2rad(clean["wind_direction"]),
        wind_speed=clean["wind_speed"] * wind_factor,
        ground_albedo=inp.ground_albedo,
        clearness_ratio=1.0,
    )
    mid_hour = (times - np.timedelta64(30, "m")).astype("datetime64[m]")[None, :]
    model = (
        Cigre601(inp.span, weather, mid_hour)
        if clear_sky
        else Cigre601MeasuredGHI(inp.span, weather, mid_hour, clean["ghi"])
    )
    out = _solve(model, inp.max_temp_c, inp.sub_conductors)
    return np.where(missing, np.nan, out)


def sheltering_factor(spans: pd.DataFrame, rating_cfg: RatingConfig) -> np.ndarray:
    """Wind multiplier per span, shape (n_spans, 1): 1 for fully exposed spans."""
    reduction = rating_cfg.sheltering_max_reduction.value * (
        1.0 - spans["exposure_index"].to_numpy()
    )
    return (1.0 - reduction)[:, None]


def mw_per_circuit(amps, voltage_kv: float):  # float, array, Series or DataArray in -> same out
    """Three-phase MW at unity power factor and nominal voltage."""
    return np.sqrt(3.0) * voltage_kv * amps / 1000.0
