"""IESO Intertie Schedule and Flow archive.

IESO keeps only a rolling ~3 months of these daily reports, so `archive()` is
idempotent and meant to run daily (`.github/workflows/open-data-archive.yml`): it
downloads every complete day not yet archived and merges it into monthly CSV
files under `data/ieso/`.

Times are stored as published: delivery date, hour-ending 1-24 and 5-minute
interval 1-12. Positive flow = export from Ontario (checked against the
schedules). `interval_end_utc` converts assuming IESO market time, Eastern
Standard Time all year with no DST -- TODO(verify) for this specific report.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

LISTING_URL = "https://reports-public.ieso.ca/public/IntertieScheduleFlow/"
FILE_RE = re.compile(r"PUB_IntertieScheduleFlow_(\d{8})\.xml")
NS = {"i": "http://www.theIMO.com/schema"}
USER_AGENT = "dlr-demo/0.1 (+https://github.com/p-brunet/dlr-demo)"
ARCHIVE_DIR = Path("data/ieso")
EST = timedelta(hours=-5)

FLOW_KEYS = ["date", "hour", "interval", "zone"]
SCHEDULE_KEYS = ["date", "hour", "zone"]


def _text(el: ET.Element | None, tag: str) -> str | None:
    child = el.find(f"i:{tag}", NS) if el is not None else None
    return child.text.strip() if child is not None and child.text else None


def _int(el: ET.Element, tag: str) -> int:
    value = _text(el, tag)
    if value is None:
        raise ValueError(f"IESO report: missing <{tag}>")
    return int(value)


def _float(value: str | None) -> float:
    return float(value) if value not in (None, "") else float("nan")


def parse_report(xml: bytes, zone_prefix: str = "PQ.") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (flows, schedules) for zones whose name starts with `zone_prefix`."""
    root = ET.fromstring(xml)
    day = _text(root.find("i:IMODocBody", NS), "Date")
    flows, schedules = [], []
    for zone_el in root.iterfind(".//i:IntertieZone", NS):
        zone = _text(zone_el, "IntertieZoneName") or ""
        if not zone.startswith(zone_prefix):
            continue
        for s in zone_el.iterfind("i:Schedules/i:Schedule", NS):
            schedules.append(
                {
                    "date": day,
                    "hour": _int(s, "Hour"),
                    "zone": zone,
                    "import_mw": _float(_text(s, "Import")),
                    "export_mw": _float(_text(s, "Export")),
                }
            )
        for a in zone_el.iterfind("i:Actuals/i:Actual", NS):
            flows.append(
                {
                    "date": day,
                    "hour": _int(a, "Hour"),
                    "interval": _int(a, "Interval"),
                    "zone": zone,
                    "flow_mw": _float(_text(a, "Flow")),
                }
            )
    return (
        pd.DataFrame(flows, columns=[*FLOW_KEYS, "flow_mw"]),
        pd.DataFrame(schedules, columns=[*SCHEDULE_KEYS, "import_mw", "export_mw"]),
    )


def interval_end_utc(df: pd.DataFrame) -> pd.Series:
    """UTC end time of each (date, hour[, interval]) row, assuming EST market time."""
    minutes = (df["hour"] - 1) * 60 + (df["interval"] * 5 if "interval" in df else 60)
    local = pd.to_datetime(df["date"]) + pd.to_timedelta(minutes, unit="min")
    return (local - pd.Timedelta(EST)).dt.tz_localize("UTC")


def list_available_days(client: httpx.Client) -> list[date]:
    resp = client.get(LISTING_URL)
    resp.raise_for_status()
    stamps = sorted(set(FILE_RE.findall(resp.text)))
    return [datetime.strptime(s, "%Y%m%d").date() for s in stamps]


def _monthly_path(archive_dir: Path, kind: str, label: str, month: str) -> Path:
    return archive_dir / f"intertie_{kind}_{label}_{month}.csv"


def archived_days(archive_dir: Path, label: str) -> set[date]:
    days: set[date] = set()
    for path in archive_dir.glob(f"intertie_flow_{label}_*.csv"):
        days.update(date.fromisoformat(d) for d in pd.read_csv(path, usecols=["date"])["date"])
    return days


def _merge_write(path: Path, new: pd.DataFrame, keys: list[str]) -> None:
    if path.exists():
        new = pd.concat([pd.read_csv(path), new], ignore_index=True)
    new = new.drop_duplicates(subset=keys, keep="last").sort_values(keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_csv(path, index=False, lineterminator="\n")


def archive(
    archive_dir: Path = ARCHIVE_DIR, zone_prefix: str = "PQ.", today: date | None = None
) -> list[date]:
    """Fetch every complete, not-yet-archived day; return the days added."""
    label = zone_prefix.rstrip(".")
    today = today or (datetime.now(UTC) + EST).date()  # the current EST day is still partial
    done = archived_days(archive_dir, label)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0) as client:
        todo = [d for d in list_available_days(client) if d < today and d not in done]
        flows, schedules = [], []
        for day in todo:
            resp = client.get(f"{LISTING_URL}PUB_IntertieScheduleFlow_{day:%Y%m%d}.xml")
            resp.raise_for_status()
            f, s = parse_report(resp.content, zone_prefix)
            flows.append(f)
            schedules.append(s)
    if not todo:
        return []
    for kind, frames, keys in (("flow", flows, FLOW_KEYS), ("schedule", schedules, SCHEDULE_KEYS)):
        df = pd.concat(frames, ignore_index=True)
        for month, part in df.groupby(df["date"].str[:7]):
            _merge_write(_monthly_path(archive_dir, kind, label, str(month)), part, keys)
    return todo


def load_flows(archive_dir: Path = ARCHIVE_DIR, label: str = "PQ") -> pd.DataFrame:
    paths = sorted(archive_dir.glob(f"intertie_flow_{label}_*.csv"))
    if not paths:
        return pd.DataFrame(columns=[*FLOW_KEYS, "flow_mw"])
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    df["time_utc"] = interval_end_utc(df)
    return df
