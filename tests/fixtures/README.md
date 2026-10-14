# Test fixtures

- `osm_hawthorne_outaouais.json`: Overpass response for the A41T/A42T route
  (`dlr.line.overpass.build_route_query`), © OpenStreetMap contributors, ODbL.
- `osm_ottawa_river.json`: Ottawa River centreline ways in the study bbox
  (`dlr.line.spans.build_river_query`), © OpenStreetMap contributors, ODbL.
- `ieso_intertie_flow_sample.xml`: trimmed IESO Intertie Schedule and Flow
  report (two zones, two hours) used to test the parser.
- `hrdps_corridor_grid.json`: coordinates only (rotated x/y, 2D lat/lon, CRS WKT)
  of the HRDPS corridor subset, for sampler tests. ECCC HRDPS via dynamical.org.
- `swob_cxoa_sample.json`: two hours of SWOB minute records for Ottawa CDA RCS
  (property subset, record 4 given a failed QA flag), ECCC Data Server End-use Licence.
