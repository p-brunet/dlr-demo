import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from dlr.line.candidates import CANDIDATES
from dlr.line.select import _towers_frame, assemble_route, orient_and_validate
from dlr.line.spans import build_spans

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANDIDATE = CANDIDATES["hawthorne-outaouais"]


@pytest.fixture(scope="module")
def towers() -> gpd.GeoDataFrame:
    osm = json.loads((FIXTURES / "osm_hawthorne_outaouais.json").read_text(encoding="utf-8"))
    route, _, _ = orient_and_validate(assemble_route(osm, CANDIDATE.route_way_ids), osm, CANDIDATE)
    return _towers_frame(route, CANDIDATE.candidate_id)


@pytest.fixture(scope="module")
def river() -> list[LineString]:
    data = json.loads((FIXTURES / "osm_ottawa_river.json").read_text(encoding="utf-8"))
    return [
        LineString([(p["lon"], p["lat"]) for p in el["geometry"]])
        for el in data["elements"]
        if el["type"] == "way"
    ]


def test_one_span_per_consecutive_tower_pair(towers, river) -> None:
    spans = build_spans(towers, river, "ON", "QC")
    assert len(spans) == len(towers) - 1 == 110
    assert spans["length_m"].sum() / 1000 == pytest.approx(34.38, abs=0.05)
    assert (spans["length_m"] > 50).all()


def test_exactly_one_river_crossing_splits_provinces(towers, river) -> None:
    spans = build_spans(towers, river, "ON", "QC")
    crossing = spans[spans["crosses_river"]]
    assert len(crossing) == 1 and crossing["region"].iloc[0] == "ON/QC"
    seq = crossing["span_seq"].iloc[0]
    assert (spans.loc[spans["span_seq"] < seq, "region"] == "ON").all()
    assert (spans.loc[spans["span_seq"] > seq, "region"] == "QC").all()


def test_azimuth_convention_is_clockwise_from_true_north() -> None:
    towers = gpd.GeoDataFrame(
        {
            "line_id": "t",
            "tower_seq": [0, 1, 2],
            "osm_node_id": [1, 2, 3],
            "chainage_km": [0.0, 1.1, 2.0],
        },
        geometry=[Point(-75.5, 45.40), Point(-75.5, 45.41), Point(-75.487, 45.41)],
        crs="EPSG:4326",
    )
    river = [LineString([(-75.6, 45.405), (-75.4, 45.405)])]
    spans = build_spans(towers, river, "ON", "QC")
    assert spans["azimuth_deg"].iloc[0] == pytest.approx(0.0, abs=0.01)  # due north
    assert spans["azimuth_deg"].iloc[1] == pytest.approx(90.0, abs=0.1)  # due east
