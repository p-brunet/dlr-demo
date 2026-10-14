import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlr.weather import swob

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "swob_cxoa_sample.json"


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_tidy_converts_units_and_keeps_utc_minute_end(records: list[dict]) -> None:
    df = swob.tidy(records, "CXOA")
    assert df["time"].is_monotonic_increasing and df["time"].is_unique
    assert df["time"].iloc[0] == pd.Timestamp("2026-10-01 10:01")
    assert df.loc[0, "ws_1min"] == pytest.approx(13.0 / 3.6)
    assert df.loc[0, "wd_1min"] == 225
    assert df.loc[0, "ws_1h"] == pytest.approx(14.5 / 3.6)


def test_tidy_drops_values_that_failed_quality_control(records: list[dict]) -> None:
    df = swob.tidy(records, "CXOA").set_index("time")
    bad = pd.Timestamp(records[3]["date_tm-value"]).tz_localize(None)
    assert np.isnan(df.loc[bad, "ws_1min"])
    assert np.isfinite(df.loc[bad, "wd_1min"])  # other fields of that minute are kept


def test_archive_writes_each_complete_day_once(
    records: list[dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_fetch(msc_id: str, start: str, end: str) -> list[dict]:
        calls.append(start)
        return records if start.startswith("2026-10-01") else []

    monkeypatch.setattr(swob, "fetch_swob", fake_fetch)
    first = swob.archive(tmp_path, today=date(2026, 10, 3), retention_days=3)
    assert [p.name for p in first] == ["2026-10-01.parquet"]
    n_calls = len(calls)
    assert swob.archive(tmp_path, today=date(2026, 10, 3), retention_days=3) == []
    assert len(calls) == n_calls + 2  # empty days are retried, the stored day is not
    assert len(swob.load_minutes("CXOA", tmp_path)) == len(swob.tidy(records, "CXOA"))
