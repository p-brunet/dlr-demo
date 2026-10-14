"""Shared matplotlib style for every figure (docs, blog, thumbnail).

Colours are the validated reference palette (light mode): categorical slots
in fixed order, a single-hue sequential ramp for magnitudes, recessive
hairline grid, neutral ink for all text.
"""

from __future__ import annotations

import matplotlib as mpl
from cycler import cycler
from matplotlib.colors import LinearSegmentedColormap

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"

# categorical slots, fixed order: blue, orange, aqua, yellow, magenta, green, violet, red
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED = "#9a9993"

SEQUENTIAL_BLUE = [
    "#cde2fb",
    "#b7d3f6",
    "#9ec5f4",
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#1c5cab",
    "#184f95",
    "#104281",
    "#0d366b",
]
SEQUENTIAL_CMAP = LinearSegmentedColormap.from_list("dlr_blue", SEQUENTIAL_BLUE)
# same ramp starting at a step that stays visible as a thin line on the surface
SEQUENTIAL_CMAP_VISIBLE = LinearSegmentedColormap.from_list("dlr_blue_vis", SEQUENTIAL_BLUE[3:])
WATER = "#dcdbd6"  # neutral, so it never reads as a value on the blue ramp

# diverging: blue <-> red through a neutral grey midpoint
DIVERGING_CMAP = LinearSegmentedColormap.from_list(
    "dlr_div", ["#104281", "#3987e5", "#f0efec", "#e66767", "#a62a2a"]
)


def apply() -> None:
    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.size": 10,
            "text.color": TEXT,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.edgecolor": GRID,
            "axes.linewidth": 1.0,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "grid.linestyle": "-",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.prop_cycle": cycler(color=SERIES),
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "lines.linewidth": 2.0,
            "lines.solid_capstyle": "round",
            "legend.frameon": False,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )
