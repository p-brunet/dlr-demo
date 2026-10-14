from pathlib import Path

from dlr.config import LineConfig, SourceKind, StudyConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def test_line_config_round_trip() -> None:
    line = LineConfig.from_yaml(CONFIG_DIR / "line.yaml")
    assert line.line_id == "hawthorne-outaouais"
    assert line.sub_conductors.source == SourceKind.OPEN_DATA
    assert line.tower_count > 100


def test_study_config_round_trip() -> None:
    study = StudyConfig.from_yaml(CONFIG_DIR / "study.yaml")
    assert study.bbox.max_lat >= 45.60  # Poste de l'Outaouais sits at 45.5997N
    assert study.weather.source == "hrdps"
    assert "wind_speed_10m" in study.weather.variables
