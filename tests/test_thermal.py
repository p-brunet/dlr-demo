"""M4 gate: CIGRE TB 601 Annex E worked examples reproduced through linerate,
plus the project's own extensions (measured-GHI solar term, bundles, wind floor).

Expected values and tolerances are those of linerate's acceptance tests
(https://github.com/statnett/linerate/blob/5.0.0/tests/acceptance_tests/test_cigre_ampacity_cases.py),
which transcribe CIGRE TB 601 Annex E (Example B's "1054 A" in the brochure is
a typo for 1504 A, per linerate).
"""

import numpy as np
import pytest
from linerate import Conductor, Span, Tower, Weather
from linerate.models.cigre601 import Cigre601

from dlr.thermal.rating import Cigre601MeasuredGHI, RatingInputs, dlr_rating
from dlr.thermal.run import floor_attack_angle


def drake(strand_m: float, emissivity: float, absorptivity: float) -> Conductor:
    return Conductor(
        core_diameter=10.4e-3,
        conductor_diameter=28.1e-3,
        outer_layer_strand_diameter=strand_m,
        emissivity=emissivity,
        solar_absorptivity=absorptivity,
        temperature1=25,
        temperature2=75,
        resistance_at_temperature1=7.283e-5,
        resistance_at_temperature2=8.688e-5,
        aluminium_cross_section_area=float("nan"),
        constant_magnetic_effect=1,
        current_density_proportional_magnetic_effect=0,
        max_magnetic_core_relative_resistance_increase=1,
    )


@pytest.fixture
def example_a() -> Cigre601:
    span = Span(
        conductor=drake(4.4e-3, 0.8, 0.8),
        start_tower=Tower(latitude=30, longitude=0.0001, altitude=0),
        end_tower=Tower(latitude=30, longitude=-0.0001, altitude=0),
        num_conductors=1,
    )
    weather = Weather(
        air_temperature=40,
        wind_direction=np.radians(30),
        wind_speed=0.61,
        ground_albedo=0.1,
        clearness_ratio=1,
    )
    return Cigre601(span, weather, np.datetime64("2016-06-10 11:00"))


@pytest.fixture
def example_b() -> Cigre601:
    span = Span(
        conductor=drake(2.2e-3, 0.9, 0.9),
        start_tower=Tower(latitude=50 - 0.0045, longitude=0, altitude=500 - 88),
        end_tower=Tower(latitude=50 + 0.0045, longitude=0, altitude=500 + 88),
        num_conductors=1,
    )
    weather = Weather(
        air_temperature=20,
        wind_direction=np.radians(80),
        wind_speed=1.66,
        ground_albedo=0.15,
        clearness_ratio=0.5,
    )
    return Cigre601(span, weather, np.datetime64("2016-10-03 14:00"))


def test_cigre601_example_a(example_a: Cigre601) -> None:
    assert example_a.compute_convective_cooling(100) == pytest.approx(77.6, abs=0.5)
    assert example_a.compute_radiative_cooling(100) == pytest.approx(39.1, abs=0.5)
    assert example_a.compute_solar_heating() == pytest.approx(27.2, abs=0.5)
    assert example_a.compute_resistance(100, np.nan) == pytest.approx(9.3905e-5, abs=1e-9)
    assert example_a.compute_steady_state_ampacity(100, tolerance=1e-8) == pytest.approx(
        976, abs=1.5
    )


def test_cigre601_example_b(example_b: Cigre601) -> None:
    assert example_b.compute_convective_cooling(100) == pytest.approx(172.1, abs=0.5)
    assert example_b.compute_radiative_cooling(100) == pytest.approx(54, abs=0.5)
    assert example_b.compute_solar_heating() == pytest.approx(13.7, abs=0.5)
    assert example_b.compute_steady_state_ampacity(100, tolerance=1e-8) == pytest.approx(
        1504, abs=1.5
    )


def test_bundle_ampacity_is_per_phase_bundle(example_a: Cigre601) -> None:
    single = example_a.compute_steady_state_ampacity(100, tolerance=1e-8)
    span2 = Span(example_a.span.conductor, example_a.span.start_tower, example_a.span.end_tower, 2)
    twin = Cigre601(span2, example_a.weather, example_a.time)
    assert twin.compute_steady_state_ampacity(100, tolerance=1e-8) == pytest.approx(2 * single)


def _clear_sky_horizontal_ghi(model: Cigre601) -> float:
    from linerate.equations import solar_angles
    from linerate.equations.cigre601 import solar_heating as cs

    sin_h = solar_angles.compute_sin_solar_altitude_for_span(model.span, model.time)
    direct = cs.compute_direct_solar_radiation(sin_h, 1.0, model.span.conductor_altitude)
    return float(direct * max(sin_h, 0.0) + cs.compute_diffuse_sky_radiation(direct, sin_h))


def test_measured_ghi_equal_to_clear_sky_reproduces_cigre(example_a: Cigre601) -> None:
    ghi = _clear_sky_horizontal_ghi(example_a)
    measured = Cigre601MeasuredGHI(example_a.span, example_a.weather, example_a.time, ghi)
    assert measured.compute_solar_heating() == pytest.approx(example_a.compute_solar_heating())


def test_overcast_ghi_scales_solar_heating_and_night_has_none(example_a: Cigre601) -> None:
    ghi = _clear_sky_horizontal_ghi(example_a)
    half = Cigre601MeasuredGHI(example_a.span, example_a.weather, example_a.time, ghi / 2)
    assert half.compute_solar_heating() == pytest.approx(example_a.compute_solar_heating() / 2)
    night = Cigre601MeasuredGHI(
        example_a.span, example_a.weather, np.datetime64("2016-06-10 23:00"), 500.0
    )
    assert night.compute_solar_heating() == pytest.approx(0.0)


def test_dlr_rating_propagates_missing_weather_as_nan(example_a: Cigre601) -> None:
    span = example_a.span
    inp = RatingInputs(
        span=span,
        span_azimuth_rad=np.asarray(span.conductor_azimuth),
        sub_conductors=1,
        max_temp_c=100.0,
        ground_albedo=0.1,
    )
    wx = {
        "air_temperature": np.array([[40.0, np.nan]]),
        "wind_speed": np.array([[0.61, 0.61]]),
        "wind_direction": np.array([[30.0, 30.0]]),
        "ghi": np.array([[800.0, 800.0]]),
    }
    times = np.array(["2016-06-10T11:30", "2016-06-10T12:30"], dtype="datetime64[m]")
    out = dlr_rating(inp, wx, times)
    assert np.isfinite(out[0, 0]) and np.isnan(out[0, 1])


def test_attack_angle_floor_only_moves_near_parallel_winds() -> None:
    azimuth = np.radians(np.array([[0.0], [90.0]]))
    direction = np.array([[5.0, 45.0, 178.0], [95.0, 0.0, 271.0]])
    floored = floor_attack_angle(direction, azimuth, 20.0)
    np.testing.assert_allclose(floored, [[20.0, 45.0, 20.0], [110.0, 0.0, 110.0]])
