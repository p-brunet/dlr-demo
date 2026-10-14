"""Registry of candidate ON-QC line corridors considered for this demonstrator.

Every candidate's route is an explicit, ordered list of OSM way ids, including
untagged "bridge" ways: twice in this project the way that actually crosses the
Ottawa River carried no `ref`/`operator` tag, and inferring the route from
`ref` tags silently dropped it (see docs/METHODOLOGY.md). `select_line`
validates that the assembled route is connected and reaches both substations,
so a missing bridge way now fails loudly instead.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Substation(BaseModel):
    name: str
    operator: str
    region: str  # "ON" or "QC"
    osm_way_id: int | None = None
    """OSM substation polygon; route endpoints are validated against it."""
    lat: float
    lon: float


class LineCandidate(BaseModel):
    candidate_id: str
    from_substation: Substation
    to_substation: Substation
    ieso_zones: list[str]
    ieso_zone_basis: str
    """How the IESO zone was matched to this line (inferred vs documented)."""
    osm_refs: list[str]
    route_way_ids: list[int] = Field(default_factory=list)
    """Ordered from `from_substation` to `to_substation`; empty = not resolvable."""
    traced_way_ids: list[int] = Field(default_factory=list)
    """Other ways examined while tracing, kept for provenance only."""
    notes: str


CANDIDATES: dict[str, LineCandidate] = {
    "hawthorne-outaouais": LineCandidate(
        candidate_id="hawthorne-outaouais",
        from_substation=Substation(
            name="Hawthorne TS",
            operator="Hydro One",
            region="ON",
            osm_way_id=76734082,
            lat=45.3890935,
            lon=-75.5969060,
        ),
        to_substation=Substation(
            name="Poste de l'Outaouais",
            operator="Hydro-Quebec",
            region="QC",
            osm_way_id=168607246,
            lat=45.5997,
            lon=-75.4457,
        ),
        ieso_zones=["PQ.AT"],
        ieso_zone_basis=(
            "inferred: IESO 2022 APO describes the Ottawa-zone HVDC tie as two "
            "230 kV circuits with a 1,250 MW converter limitation, and PQ.AT "
            "flows peak at ~1,235 MW; no IESO document naming circuits per zone "
            "was found"
        ),
        osm_refs=["A41T", "A42T"],
        # 76608622 and 998555319 carry ref=A41T;A42T; 87817211 (river crossing
        # and Quebec side) is untagged and was matched by geometric continuity
        # (shared node at 45.5166N -75.4224W, same circuits=2/cables=6).
        route_way_ids=[76608622, 998555319, 87817211],
        notes=(
            "Hydro One 230 kV double circuit A41T/A42T from Hawthorne TS to "
            "Hydro-Quebec's Outaouais back-to-back HVDC converter (2 x 625 MW). "
            "Selected 2026-10-13 over D5A/H9A, which are radial ties with no "
            "flow (docs/METHODOLOGY.md). The transfer limit under N-0 is the "
            "converter, not the line's thermal rating."
        ),
    ),
    "ottawa-gatineau": LineCandidate(
        candidate_id="ottawa-gatineau",
        from_substation=Substation(
            name="Hawthorne TS",
            operator="Hydro One",
            region="ON",
            osm_way_id=76734082,
            lat=45.3890935,
            lon=-75.5969060,
        ),
        to_substation=Substation(
            name="Poste Masson",
            operator="Hydro-Quebec",
            region="QC",
            osm_way_id=843488483,
            lat=45.5439,
            lon=-75.4215,
        ),
        ieso_zones=["PQ.D5A", "PQ.H9A"],
        ieso_zone_basis="ref tags match zone names; IESO 2022 APO lists them as radial ties",
        osm_refs=["D5A", "H9A"],
        traced_way_ids=[
            535370792,
            535370788,
            76734037,
            702958168,
            702958169,
            702958170,
            702958171,
            702958172,
            702958173,
            702958174,
            702958175,
            702958176,
            702958177,
            702958178,
            702958180,
            702958181,
            702958182,
            702958199,
            702958200,
            702958201,
            998555311,
            998555312,
            998555313,
            998555314,
            998555315,
            998555316,
            998555317,
            998555318,
            87817195,
        ],
        notes=(
            "Superseded 2026-10-13 (first candidate). D5A (230 kV) and H9A (115 kV) "
            "are radial interconnection points that carried 0 MW on every "
            "sampled day. The untagged crossing way 87817195 ends at Poste "
            "Masson, not Interconnexion-Maclaren. The ways are fragmented (D5A "
            "and H9A split into per-circuit stubs at two junctions) and D5A also "
            "branches 45 km east (way 87872390), so no single ordered route is "
            "registered."
        ),
    ),
    "beauharnois": LineCandidate(
        candidate_id="beauharnois",
        from_substation=Substation(
            name="TBD -- Ontario-side endpoint not identified",
            operator="Hydro One",
            region="ON",
            lat=0.0,
            lon=0.0,
        ),
        to_substation=Substation(
            name="Poste des Cedres (candidate; unconfirmed)",
            operator="Hydro-Quebec",
            region="QC",
            lat=45.31,
            lon=-73.99,
        ),
        ieso_zones=["PQ.B5D.B31L"],
        ieso_zone_basis="ref tags match zone name only",
        osm_refs=["B-5-D", "B-31-L"],
        notes=(
            "Documented alternative, not pursued: no Ontario-side substation or "
            "connecting geometry has been identified. Would need the same "
            "direct-geometry-tracing treatment before it could be recommended."
        ),
    ),
}

DEFAULT_CANDIDATE = "hawthorne-outaouais"
