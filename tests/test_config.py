from pathlib import Path

from dlr.config import LineConfig, SourceKind, StudyConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def test_line_config_round_trip() -> None:
    line = LineConfig.from_yaml(CONFIG_DIR / "line.yaml")
    assert line.conductor.absorptivity.source == SourceKind.INDUSTRY_TYPICAL
    assert line.conductor.absorptivity.value == 0.8


def test_study_config_round_trip() -> None:
    study = StudyConfig.from_yaml(CONFIG_DIR / "study.yaml")
    assert study.bbox.min_lon == -75.65
    assert study.bbox.max_lat == 45.60
    assert study.weather.source == "hrdps"
    assert "wind_speed_10m" in study.weather.variables
