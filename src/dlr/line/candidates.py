"""Registry of candidate ON-QC line corridors considered for this demonstrator.

Populated from live OSM/IESO verification in Milestone 1 (see docs/DATA_SOURCES.md
for the full trail). `ottawa_gatineau` is the recommended candidate; its OSM way
ids were found by direct geometric tracing, NOT by trusting ref-tag matches alone
-- an initial tag-only match to IESO zone codes D5A/H9A pointed at a much shorter,
incorrect crossing (Hawthorne TS <-> Poste Vignan) that direct geometry tracing
disproved. The real crossing is ~17km further northeast, near Masson-Angers/
Thurso QC.
"""

from __future__ import annotations

from pydantic import BaseModel


class Substation(BaseModel):
    name: str
    operator: str
    region: str  # "ON" or "QC"
    lat: float
    lon: float


class LineCandidate(BaseModel):
    candidate_id: str
    from_substation: Substation
    to_substation: Substation
    ieso_zones: list[str]
    osm_refs: list[str]
    osm_way_ids: list[int]
    primary_ref: str | None = None
    """Which `osm_refs` entry defines the route geometry/length.

    Several of the traced circuits (e.g. D5A 230kV and H9A 115kV here) share
    the same right-of-way but are mapped as separate parallel OSM ways for
    part of the corridor. Merging every tagged way into one geometry would
    double-count those shared stretches. `primary_ref` picks one circuit's
    ways (which happen to include every combined-ref way too, since a
    combined "D5A;H9A" way still carries "D5A") as the representative route;
    `osm_way_ids` is kept as-is for tower counting and full provenance.
    """
    notes: str


CANDIDATES: dict[str, LineCandidate] = {
    "ottawa-gatineau": LineCandidate(
        candidate_id="ottawa-gatineau",
        from_substation=Substation(
            name="Hawthorne TS",
            operator="Hydro One",
            region="ON",
            lat=45.3890935,
            lon=-75.5969060,
        ),
        to_substation=Substation(
            name="Poste Masson / Poste de l'Interconnexion-Maclaren",
            operator="Hydro-Quebec",
            region="QC",
            lat=45.549673,
            lon=-75.4247809,
        ),
        ieso_zones=["PQ.D5A", "PQ.H9A"],
        osm_refs=["D5A", "H9A"],
        primary_ref="D5A",
        # D5A/H9A-tagged segments (Ontario side) + the untagged bridge segment
        # at the border (87817195, confirmed by geometric continuity, not by
        # ref tag -- OSM has no ref/operator tag on the crossing itself).
        osm_way_ids=[
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
            "Corrected during Milestone 1 execution: initial tag-based cross-"
            "validation (D5A/H9A refs matching IESO zone names) pointed at "
            "Poste Vignan (~4km from Hawthorne TS) as the QC endpoint, but "
            "direct geometry tracing of the ref-tagged OSM ways showed the "
            "corridor actually runs ~17km northeast to Poste Masson / Poste "
            "de l'Interconnexion-Maclaren near Masson-Angers/Thurso, QC. "
            "The A41T/A42T circuits (also Hydro One, 230kV) run in the same "
            "corridor and likely share the same right-of-way but are not "
            "included in this geometry (kept as a documented parallel "
            "circuit, not this candidate's primary pair). D5A (230kV) and "
            "H9A (115kV) are combined on many way segments but mapped as "
            "separate parallel ways for part of the corridor; the stored "
            "geometry/length uses only D5A-bearing ways (primary_ref) to "
            "avoid double-counting the shared right-of-way -- see "
            "docs/DATA_SOURCES.md for the length comparison."
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
        osm_refs=["B-5-D", "B-31-L"],
        osm_way_ids=[],
        notes=(
            "Documented alternative, not pursued for Milestone 1: the bbox "
            "used in initial research did not reach Ontario, so no Ontario-"
            "side substation or connecting geometry has been identified. "
            "Would need the same direct-geometry-tracing treatment as "
            "ottawa-gatineau before it could be recommended."
        ),
    ),
}
