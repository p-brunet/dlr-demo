"""Static figures for docs/ and the blog post (PNG)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

from dlr.export import style  # noqa: E402

if TYPE_CHECKING:
    import pandas as pd

FIGURES_DIR = Path("docs/figures")
METRIC_CRS = "EPSG:32618"
OSM_CREDIT = "© OpenStreetMap contributors (ODbL)"
HRDEM_CREDIT = "HRDEM © NRCan (OGL-Canada)"


def span_model_figure(
    spans: gpd.GeoDataFrame,
    towers: gpd.GeoDataFrame,
    river: list,
    out: Path = FIGURES_DIR / "span_model.png",
) -> Path:
    """Map of spans coloured by exposure index + ground/tower-top profile."""
    style.apply()
    s = spans.to_crs(METRIC_CRS)
    t = towers.to_crs(METRIC_CRS)
    r = gpd.GeoSeries(river, crs=spans.crs).to_crs(METRIC_CRS)

    fig, (ax_map, ax_prof) = plt.subplots(
        1, 2, figsize=(11, 5.6), gridspec_kw={"width_ratios": [1, 1.35], "wspace": 0.18}
    )

    # map
    ax_map.grid(False)
    ax_map.set_xticks([])
    ax_map.set_yticks([])
    for spine in ax_map.spines.values():
        spine.set_visible(False)
    r.plot(ax=ax_map, color=style.WATER, linewidth=6, capstyle="round")
    segments = [list(g.coords) for g in s.geometry]
    lc = LineCollection(segments, cmap=style.SEQUENTIAL_CMAP, linewidths=5, capstyle="butt")
    lc.set_array(s["exposure_index"].to_numpy())
    lc.set_clim(0.4, 1.0)
    ax_map.add_collection(lc)
    for label, row, dx, ha in (
        ("Hawthorne TS (ON)", t.iloc[0], 10, "left"),
        ("Poste de l'Outaouais (QC)", t.iloc[-1], -10, "right"),
    ):
        ax_map.scatter(
            row.geometry.x,
            row.geometry.y,
            s=40,
            color=style.TEXT,
            edgecolor=style.SURFACE,
            linewidth=2,
            zorder=4,
        )
        ax_map.annotate(
            label,
            (row.geometry.x, row.geometry.y),
            xytext=(dx, 0),
            ha=ha,
            textcoords="offset points",
            fontsize=9,
            color=style.TEXT,
            va="center",
        )
    crossing = s[s["crosses_river"]].geometry.iloc[0].centroid
    ax_map.annotate(
        "Ottawa River\n(ON | QC border)",
        (crossing.x, crossing.y),
        xytext=(crossing.x - 12000, crossing.y + 3000),
        fontsize=8,
        color=style.TEXT_SECONDARY,
        arrowprops={"arrowstyle": "-", "color": style.MUTED, "lw": 0.8},
    )
    minx, miny, maxx, maxy = s.total_bounds
    pad = 2500
    ax_map.set_xlim(minx - pad * 3, maxx + pad * 2.5)
    ax_map.set_ylim(miny - pad, maxy + pad)
    ax_map.set_aspect("equal")
    ax_map.plot([maxx - 2000, maxx + 3000], [miny + 1500] * 2, color=style.TEXT, lw=2)
    ax_map.text(maxx + 500, miny + 2000, "5 km", ha="center", fontsize=8)
    cbar = fig.colorbar(lc, ax=ax_map, orientation="horizontal", fraction=0.04, pad=0.02)
    cbar.set_label("Span exposure index (1 = open, no obstacles 20–60 m from the axis)")
    cbar.outline.set_visible(False)
    ax_map.set_title("110 spans, coloured by wind exposure")

    # profile
    ground = t["ground_alt_m"]
    top = ground + t["dsm_height_above_ground_m"]
    ax_prof.fill_between(
        t["chainage_km"], ground.min() - 10, ground, color=style.SERIES[0], alpha=0.10, lw=0
    )
    ax_prof.plot(t["chainage_km"], ground, color=style.SERIES[0], label="Ground at tower (DTM)")
    ax_prof.scatter(
        t["chainage_km"],
        top,
        s=16,
        color=style.SERIES[1],
        edgecolor=style.SURFACE,
        linewidth=1.5,
        zorder=3,
        label="Highest DSM return at tower",
    )
    river_km = s.loc[s["crosses_river"], "chainage_mid_km"].iloc[0]
    ax_prof.axvline(river_km, color=style.MUTED, lw=1)
    ax_prof.text(
        river_km + 0.3, ground.min() - 5, "river crossing", fontsize=8, color=style.TEXT_SECONDARY
    )
    ax_prof.set_xlabel("Distance from Hawthorne TS (km)")
    ax_prof.set_ylabel("Elevation (m above sea level)")
    ax_prof.set_ylim(ground.min() - 10, top.max() + 10)
    ax_prof.set_xlim(0, t["chainage_km"].max())
    ax_prof.legend(loc="upper left")
    ax_prof.set_title("Terrain and tower heights along the line")

    fig.text(
        0.01,
        -0.02,
        f"Line and towers {OSM_CREDIT}; terrain {HRDEM_CREDIT}.",
        fontsize=7.5,
        color=style.TEXT_SECONDARY,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def _label_end(ax, x, y, text: str, dy: float = 0.0) -> None:
    ax.annotate(
        text,
        (x, y),
        xytext=(6, dy),
        textcoords="offset points",
        fontsize=8.5,
        color=style.TEXT,
        va="center",
    )


def ratings_week_figure(
    line_df, start: str, end: str, out: Path = FIGURES_DIR / "ratings_week.png"
) -> Path:
    """One week of hourly line ratings (MW per circuit): static, AAR, DLR."""
    import pandas as pd

    style.apply()
    d = line_df[(line_df["time"] >= pd.Timestamp(start)) & (line_df["time"] < pd.Timestamp(end))]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    series = [
        ("aar_mw_per_circuit", "Ambient-adjusted (AAR)", style.SERIES[1], 1.6),
        ("dlr_mw_per_circuit", "Dynamic (DLR), weakest span", style.SERIES[0], 2.0),
    ]
    ax.plot(d["time"], d["static_mw_per_circuit"], color=style.TEXT_SECONDARY, lw=1.5)
    _label_end(ax, d["time"].iloc[-1], d["static_mw_per_circuit"].iloc[-1], "Static")
    for col, label, color, lw in series:
        ax.plot(d["time"], d[col], color=color, lw=lw, label=label)
    ax.set_ylabel("Rating per circuit (MW)")
    ax.legend(loc="upper left", ncols=2)
    ax.set_xlim(d["time"].iloc[0], d["time"].iloc[-1])
    ax.set_title(f"Hourly line rating, {start} to {end} (UTC)")
    fig.text(
        0.01,
        -0.03,
        "Twin Drake ACSR (assumed), 93 °C, CIGRE TB 601 via linerate; "
        "weather ECCC HRDPS (dynamical.org).",
        fontsize=7.5,
        color=style.TEXT_SECONDARY,
    )
    fig.autofmt_xdate()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def ratio_distribution_figure(
    line_df, ratings, out: Path = FIGURES_DIR / "ratio_distribution.png"
) -> Path:
    """Cumulative distribution of line rating / static rating for each tier."""
    import numpy as np

    style.apply()
    static = line_df["static_a"].to_numpy()  # per hour: summer or winter value
    curves = [
        ("Dynamic (DLR)", line_df["dlr_a"] / static, style.SERIES[0]),
        (
            "DLR, min. 20° angle of attack",
            ratings["dlr_attack20"].min("span").values / static,
            style.SERIES[2],
        ),
        ("Ambient-adjusted (AAR)", line_df["aar_a"] / static, style.SERIES[1]),
    ]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    for label, ratio, color in curves:
        r = np.sort(np.asarray(ratio)[np.isfinite(ratio)])
        ax.plot(r, np.arange(1, len(r) + 1) / len(r) * 100, color=color, label=label)
    ax.axvline(1.0, color=style.TEXT_SECONDARY, lw=1)
    ax.text(1.005, 3, "static rating", fontsize=8, color=style.TEXT_SECONDARY)
    ax.set_xlabel("Line rating ÷ static rating")
    ax.set_ylabel("Share of hours at or below (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="lower right")
    ax.set_title("How often each rating beats the static assumption")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def bottleneck_map_figure(
    spans: gpd.GeoDataFrame,
    line_df,
    river: list,
    out: Path = FIGURES_DIR / "bottleneck_spans.png",
) -> Path:
    """Spans coloured by the share of hours they limit the whole line."""
    style.apply()
    share = line_df["limiting_span"].value_counts(normalize=True) * 100
    s = spans.to_crs(METRIC_CRS).copy()
    s["limiting_pct"] = s["span_id"].map(share).fillna(0.0)
    r = gpd.GeoSeries(river, crs=spans.crs).to_crs(METRIC_CRS)

    fig, ax = plt.subplots(figsize=(6.2, 6.4))
    ax.grid(False)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    r.plot(ax=ax, color=style.WATER, linewidth=6, capstyle="round")
    lc = LineCollection(
        [list(g.coords) for g in s.geometry],
        cmap=style.SEQUENTIAL_CMAP_VISIBLE,
        linewidths=5,
        capstyle="butt",
    )
    lc.set_array(s["limiting_pct"].to_numpy())
    lc.set_clim(0, max(5.0, float(s["limiting_pct"].max())))
    ax.add_collection(lc)
    top = s.nlargest(3, "limiting_pct")
    for _, row in top.iterrows():
        c = row.geometry.centroid
        ax.annotate(
            f"{row['span_id']}: {row['limiting_pct']:.0f} %",
            (c.x, c.y),
            xytext=(10, 0),
            textcoords="offset points",
            fontsize=8.5,
            color=style.TEXT,
            va="center",
        )
    minx, miny, maxx, maxy = s.total_bounds
    ax.set_xlim(minx - 6000, maxx + 6000)
    ax.set_ylim(miny - 1500, maxy + 1500)
    ax.set_aspect("equal")
    cbar = fig.colorbar(lc, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02)
    cbar.set_label("Share of hours this span limits the whole line (%)")
    cbar.outline.set_visible(False)
    n = int((s["limiting_pct"] > 0).sum())
    ax.set_title(f"The weakest span moves: {n} of {len(s)} spans limit the line at some hour")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def rating_skill_figure(
    skill: pd.DataFrame,
    point_skill: pd.DataFrame,
    out: Path = FIGURES_DIR / "rating_skill.png",
) -> Path:
    """Rating error by lead: forecast vs persistence (pseudo-analysis), and station point check."""
    style.apply()
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    ax.axvspan(0.5, 6.5, color=style.GRID, alpha=0.6, lw=0)
    ax.text(3.5, 1.0, "reference\nwindow", ha="center", fontsize=8, color=style.TEXT_SECONDARY)
    s = skill[skill.index >= 7]
    curves = [
        (
            s.index,
            s["mae_rel"] * 100,
            "HRDPS forecast vs reference DLR (whole line)",
            style.SERIES[0],
        ),
        (s.index, s["persistence_mae_rel"] * 100, "Persistence vs reference DLR", style.SERIES[1]),
        (
            point_skill.index,
            point_skill["mae_rel"] * 100,
            "HRDPS vs station-observed DLR (point check)",
            style.SERIES[2],
        ),
    ]
    for x, y, label, color in curves:
        ax.plot(x, y, color=color, label=label)
    ax.set_xlim(0.5, 48.5)
    ax.set_ylim(0, None)
    ax.set_xticks([1, 6, 12, 18, 24, 30, 36, 42, 48])
    ax.set_xlabel("Forecast lead time (h)")
    ax.set_ylabel("Mean absolute error (% of true rating)")
    ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.55), fontsize=8.5)
    ax.set_title("How wrong is the forecast rating?")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def secure_week_figure(
    week: pd.DataFrame,
    start: str,
    end: str,
    out: Path = FIGURES_DIR / "secure_dayahead_week.png",
) -> Path:
    """A test-period week: reference DLR, day-ahead forecast, secure forecasts, static (MW)."""
    style.apply()
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(week["time"], week["static_mw"], color=style.TEXT_SECONDARY, lw=1.5)
    _label_end(ax, week["time"].iloc[-1], week["static_mw"].iloc[-1], "Static")
    ax.plot(week["time"], week["truth_mw"], color=style.TEXT, lw=1.2, label="Reference DLR (truth)")
    ax.plot(
        week["time"],
        week["forecast_mw"],
        color=style.SERIES[0],
        lw=2,
        label="Day-ahead forecast DLR",
    )
    ax.plot(
        week["time"],
        week["secure_mw"],
        color=style.SERIES[1],
        lw=2,
        label="Day-ahead secure DLR (98 %)",
    )
    ax.set_ylabel("Rating per circuit (MW)")
    ax.set_xlim(week["time"].iloc[0], week["time"].iloc[-1])
    ax.legend(loc="upper left", ncols=3, fontsize=8.5)
    ax.set_title(f"Day-ahead forecasts (24-29 h lead), {start} to {end} (UTC)")
    fig.autofmt_xdate()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def station_weather_figure(
    weather: pd.DataFrame, out: Path = FIGURES_DIR / "station_weather_skill.png"
) -> Path:
    """Small multiples: temperature and wind MAE by lead, per station."""
    style.apply()
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharex=True)
    panels = [("temp_mae_c", "2 m temperature MAE (°C)"), ("wind_mae_ms", "10 m wind MAE (m/s)")]
    w = weather.reset_index()
    for ax, (col, title) in zip(axes, panels, strict=True):
        for k, (station, g) in enumerate(w.groupby("station")):
            g = g[g["lead_h"] >= 1]
            ax.plot(g["lead_h"], g[col], color=style.SERIES[k], label=station)
        ax.set_title(title)
        ax.set_xlabel("Lead time (h)")
        ax.set_ylim(0, None)
        ax.set_xticks([1, 12, 24, 36, 48])
    axes[0].legend(loc="lower right")
    fig.text(
        0.01,
        -0.04,
        "Observations: ECCC climate-hourly (Ottawa Intl A = YOW, Ottawa Gatineau A = YND).",
        fontsize=7.5,
        color=style.TEXT_SECONDARY,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def render_all() -> list[Path]:
    """Every docs/blog figure, from the processed outputs (no network)."""
    import pandas as pd
    import xarray as xr

    from dlr.config import LineConfig, StudyConfig
    from dlr.line.spans import fetch_river
    from dlr.thermal.rating import mw_per_circuit
    from dlr.thermal.run import LINE_REFERENCE_PATH, REFERENCE_RATINGS_STORE
    from dlr.verify.forecast import FORECAST_STORE
    from dlr.verify.run import OUT_DIR as VERIFY_DIR

    spans = gpd.read_parquet("data/processed/spans.geoparquet")
    towers = gpd.read_parquet("data/processed/towers.geoparquet")
    river = fetch_river(StudyConfig.from_yaml(Path("config/study.yaml")).bbox)
    ref = pd.read_parquet(LINE_REFERENCE_PATH)
    ratings = xr.open_zarr(REFERENCE_RATINGS_STORE)
    out = [
        span_model_figure(spans, towers, river),
        ratings_week_figure(ref, "2026-08-10", "2026-08-17"),
        ratio_distribution_figure(ref, ratings),
        bottleneck_map_figure(spans, ref, river),
    ]
    if (VERIFY_DIR / "summary.json").exists():
        from dlr.export.site import _day_ahead

        skill = pd.read_csv(VERIFY_DIR / "rating_skill_by_lead_base.csv", index_col=0)
        point = pd.read_csv(VERIFY_DIR / "station_point_dlr_skill_by_lead.csv", index_col=0)
        weather = pd.read_csv(VERIFY_DIR / "station_weather_skill_by_lead.csv")
        margins = pd.read_csv(VERIFY_DIR / "margins_base.csv", index_col=0).iloc[:, 0]
        voltage = LineConfig.from_yaml(Path("config/line.yaml")).voltage_kv.value
        da = _day_ahead(xr.open_zarr(FORECAST_STORE).load(), margins)
        week = ref.set_index("time").join(da, how="inner").reset_index()
        start, end = "2026-09-22", "2026-09-29"
        week = week[(week["time"] >= start) & (week["time"] < end)]
        week = pd.DataFrame(
            {
                "time": week["time"],
                "truth_mw": mw_per_circuit(week["dlr_a"], voltage),
                "forecast_mw": mw_per_circuit(week["line_dlr"], voltage),
                "secure_mw": mw_per_circuit(week["secure_a"], voltage),
                "static_mw": mw_per_circuit(week["static_a"], voltage),
            }
        )
        out += [
            rating_skill_figure(skill, point),
            secure_week_figure(week, start, end),
            station_weather_figure(weather.set_index(["station", "lead_h"])),
        ]
    if (VERIFY_DIR / "wind_summary.json").exists():
        import json

        wind = json.loads((VERIFY_DIR / "wind_summary.json").read_text(encoding="utf-8"))
        hourly = pd.read_csv(VERIFY_DIR / "wind_hourly_cxoa.csv", index_col=0)
        curves = pd.read_csv(VERIFY_DIR / "wind_floor_fit.csv", index_col=0)
        out += [
            wind_direction_spread_figure(hourly),
            wind_floor_fit_figure(curves, wind["fitted_floor_deg"]["all_hours"]),
        ]
    return out


def wind_direction_spread_figure(
    hourly: pd.DataFrame, out: Path = FIGURES_DIR / "wind_direction_spread.png"
) -> Path:
    """Intra-hour spread of 1-minute wind directions vs hourly mean speed (CXOA)."""
    import numpy as np
    import pandas as pd

    style.apply()
    h = hourly.dropna(subset=["dir_spread_deg", "ws_mean"])
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.scatter(h["ws_mean"], h["dir_spread_deg"], s=10, color=style.SERIES[0], alpha=0.35, lw=0)
    bins = np.arange(0, h["ws_mean"].max() + 0.5, 0.5)
    grouped = h.groupby(pd.cut(h["ws_mean"], bins), observed=True)["dir_spread_deg"]
    med = grouped.median()
    counts = grouped.size()
    centres = [b.mid for b in med.index]
    keep = counts.to_numpy() >= 10
    ax.plot(np.array(centres)[keep], med.to_numpy()[keep], color=style.TEXT, lw=2)
    _label_end(ax, np.array(centres)[keep][-1], med.to_numpy()[keep][-1], "median")
    ax.set_xlabel("Hourly mean wind speed at 10 m (m/s)")
    ax.set_ylabel("Spread of 1-minute directions (°)")
    ax.set_ylim(0, 60)
    ax.set_title("Within an hour, the wind direction swings — most in light wind")
    fig.text(
        0.01,
        -0.03,
        "Ottawa CDA RCS (CXOA), ECCC SWOB minute observations, "
        "hours with ≥45 valid minutes; circular standard deviation.",
        fontsize=7.5,
        color=style.TEXT_SECONDARY,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def wind_floor_fit_figure(
    curves: pd.DataFrame,
    fitted_deg: float,
    out: Path = FIGURES_DIR / "wind_floor_fit.png",
) -> Path:
    """Hourly-vector line rating ÷ minute-resolved rating as the attack-angle floor grows."""
    style.apply()
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    series = [
        ("all", "All hours", style.SERIES[0], 2.0),
        ("low_wind", "Wind below 2 m/s", style.SERIES[1], 1.6),
        ("high_wind", "Wind 2 m/s and above", style.SERIES[2], 1.6),
    ]
    for col, label, color, lw in series:
        ax.plot(curves.index, curves[col], color=color, lw=lw, label=label)
    ax.axhline(1.0, color=style.TEXT_SECONDARY, lw=1)
    ax.axvline(fitted_deg, color=style.MUTED, lw=1)
    ax.text(
        fitted_deg + 0.8,
        curves["all"].min() + 0.005,
        f"calibrated floor ≈ {fitted_deg:.0f}°",
        fontsize=8.5,
        color=style.TEXT,
    )
    ax.axvline(20, color=style.GRID, lw=1)
    ax.text(
        20.8, curves["all"].min() + 0.005, "20° heuristic", fontsize=8.5, color=style.TEXT_SECONDARY
    )
    ax.set_xlim(0, 40)
    ax.set_xlabel("Minimum angle of attack applied to the hourly wind (°)")
    ax.set_ylabel("Hourly-wind rating ÷ minute-resolved rating\n(median, whole line)")
    ax.legend(loc="upper left")
    ax.set_title("Which floor makes hourly wind cool the line as much as real wind?")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
