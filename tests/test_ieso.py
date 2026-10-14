from pathlib import Path

import pandas as pd

from dlr.grid.ieso import interval_end_utc, parse_report

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ieso_intertie_flow_sample.xml"


def test_parse_report_keeps_only_quebec_zones_and_ignores_totals() -> None:
    flows, schedules = parse_report(FIXTURE.read_bytes(), zone_prefix="PQ.")
    assert set(flows["zone"]) == {"PQ.AT"}
    assert len(flows) == 3 and len(schedules) == 2
    assert flows["date"].unique().tolist() == ["2026-10-10"]
    assert flows.loc[1, "flow_mw"] == 1235.1
    assert flows["flow_mw"].isna().sum() == 1  # empty <Flow/> stays missing, not 0
    assert schedules.loc[0, "export_mw"] == 1238


def test_interval_end_utc_assumes_est_all_year() -> None:
    df = pd.DataFrame({"date": ["2026-10-10", "2026-01-15"], "hour": [1, 24], "interval": [1, 12]})
    utc = interval_end_utc(df)
    # HE1 interval 1 ends 00:05 EST = 05:05 UTC; HE24 interval 12 ends 24:00 EST
    assert utc.iloc[0] == pd.Timestamp("2026-10-10 05:05", tz="UTC")
    assert utc.iloc[1] == pd.Timestamp("2026-01-16 05:00", tz="UTC")
