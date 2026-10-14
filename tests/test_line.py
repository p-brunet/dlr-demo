import json
from pathlib import Path

import pytest

from dlr.line.candidates import CANDIDATES
from dlr.line.select import (
    RouteValidationError,
    _line_config,
    assemble_route,
    orient_and_validate,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "osm_hawthorne_outaouais.json"
CANDIDATE = CANDIDATES["hawthorne-outaouais"]


@pytest.fixture(scope="module")
def osm() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_route_is_connected_and_reaches_both_substations(osm: dict) -> None:
    route = assemble_route(osm, CANDIDATE.route_way_ids)
    route, d_start, d_end = orient_and_validate(route, osm, CANDIDATE)
    assert d_start < 100 and d_end < 100
    assert len(route.towers) == 111
    assert route.length_m / 1000 == pytest.approx(34.38, abs=0.05)


def test_dropping_the_untagged_crossing_way_fails(osm: dict) -> None:
    # Regression: the untagged river-crossing way was once silently dropped
    # and the stored route stopped on the Ontario bank.
    route = assemble_route(osm, CANDIDATE.route_way_ids[:2])
    with pytest.raises(RouteValidationError, match="Outaouais"):
        orient_and_validate(route, osm, CANDIDATE)


def test_non_adjacent_ways_are_rejected(osm: dict) -> None:
    with pytest.raises(RouteValidationError, match="do not join"):
        assemble_route(osm, [76608622, 87817211])


def test_reversed_way_order_is_reoriented_from_hawthorne(osm: dict) -> None:
    forward, _, _ = orient_and_validate(
        assemble_route(osm, CANDIDATE.route_way_ids), osm, CANDIDATE
    )
    backward, _, _ = orient_and_validate(
        assemble_route(osm, CANDIDATE.route_way_ids[::-1]), osm, CANDIDATE
    )
    assert backward.towers[0]["osm_node_id"] == forward.towers[0]["osm_node_id"]
    assert backward.towers[-1]["osm_node_id"] == forward.towers[-1]["osm_node_id"]


def test_line_config_reads_voltage_circuits_and_bundle_from_osm(osm: dict) -> None:
    route, _, _ = orient_and_validate(assemble_route(osm, CANDIDATE.route_way_ids), osm, CANDIDATE)
    config = _line_config(CANDIDATE, osm, route, verified_on="2026-10-13")
    assert config.voltage_kv.value == 230.0
    assert config.circuits.value == 2
    assert config.sub_conductors.value == 2
    assert config.tower_count == 111


def test_superseded_candidate_refuses_to_build() -> None:
    from dlr.line.select import select_line

    with pytest.raises(RouteValidationError, match="no registered route"):
        select_line("ottawa-gatineau", line_config_out=Path("unused.yaml"))
