"""Sync the blog post into a local clone of the blog (files only, no git).

The blog is https://p-brunet.github.io/ (repo p-brunet/p-brunet.github.io).

The post draft lives in this repo (`docs/blog/`) and references its figures by
`raw.githubusercontent.com` URLs on this repo, like the other posts. `sync`:

1. copies the post to `posts/`;
2. adds a card to `projects/modelling.md` and `projects/maps.md` (once);
3. adds the new open data sources to `data-sources.md` (once).
"""

from __future__ import annotations

import re
from pathlib import Path

POST_SOURCE = Path("docs/blog/dlr-ottawa-outaouais.md")
POST_SLUG = "dlr-ottawa-outaouais"
RAW = "https://raw.githubusercontent.com/p-brunet/dlr-demo/main"
THUMBNAIL = f"{RAW}/docs/figures/bottleneck_spans.png"
CARD_DATE = "October 2026"
CRLF, LF = "\r\n", "\n"

CARD = f"""
:::{{card}} How Much More Could This Power Line Carry?
:link: /posts/{POST_SLUG}
:header: ![thumbnail]({THUMBNAIL})
{CARD_DATE} — Sensorless dynamic line rating of the Ontario–Québec HVDC tie line from
open weather forecasts, LiDAR terrain and OpenStreetMap, with forecast verification
and an interactive story map.

<span class="badge badge-python">Python</span>
<span class="badge badge-gis">GIS</span>
<span class="badge badge-rasterio">rasterio</span>
<span class="badge badge-matplotlib">matplotlib</span>
:::
"""

SOURCES = {
    "## LiDAR & Elevation": [
        "- [NRCan HRDEM (datacube STAC)](https://datacube.services.geo.ca/stac/api) — "
        "1 m LiDAR-derived DTM/DSM mosaics as cloud-optimized GeoTIFFs (Canada)",
    ],
    "### Infrastructure": [
        "- [OpenStreetMap power lines (Overpass API)](https://overpass-api.de/) — transmission "
        "line ways, towers and substations",
        "- [IESO Intertie Schedule and Flow](https://reports-public.ieso.ca/public/"
        "IntertieScheduleFlow/) — hourly/5-min Ontario intertie flows (rolling ~3 months)",
    ],
    "## Weather": [
        "- [ECCC HRDPS via dynamical.org](https://dynamical.org/) — 2.5 km Canadian weather "
        "forecasts (0–48 h) as cloud-native Zarr",
        "- [MSC GeoMet OGC API](https://api.weather.gc.ca/) — ECCC hourly station observations "
        "(climate-hourly)",
    ],
}


def _write_like(page: Path, text: str) -> None:
    """Write `text` keeping the file's existing line endings (the blog clone is LF)."""
    crlf = page.exists() and CRLF.encode() in page.read_bytes()
    with page.open("w", encoding="utf-8", newline=CRLF if crlf else LF) as f:
        f.write(text)


def _add_card(page: Path) -> bool:
    text = page.read_text(encoding="utf-8")
    if f"/posts/{POST_SLUG}" in text:
        return False
    # insert as the first card of the grid
    marker = "::::{grid} 1 1 2 2\n"
    if marker not in text:
        raise ValueError(f"no card grid found in {page}")
    _write_like(page, text.replace(marker, marker + CARD, 1))
    return True


def _add_sources(page: Path) -> bool:
    text = page.read_text(encoding="utf-8")
    changed = False
    for heading, lines in SOURCES.items():
        new = [line for line in lines if line.split("](")[1].split(")")[0] not in text]
        if not new:
            continue
        if heading not in text:
            # new top-level section before the closing note
            text = re.sub(
                r"\n---\n\n\*This list grows",
                f"\n---\n\n{heading}\n\n" + "\n".join(new) + "\n\n---\n\n*This list grows",
                text,
                count=1,
            )
        else:
            start = text.index(heading) + len(heading)
            nxt = re.search(r"\n(#{2,3} |---)", text[start:])
            end = start + (nxt.start() if nxt else len(text) - start)
            block = text[start:end].rstrip("\n")
            text = text[:start] + block + "\n" + "\n".join(new) + "\n" + text[end:]
        changed = True
    if changed:
        text = re.sub(r"Last updated: [A-Za-z]+ \d{4}", f"Last updated: {CARD_DATE}", text)
        _write_like(page, text)
    return changed


def sync(blog: Path) -> list[Path]:
    if not (blog / "myst.yml").exists():
        raise FileNotFoundError(f"{blog} does not look like a blog clone (no myst.yml)")
    written = []
    target = blog / "posts" / f"{POST_SLUG}.md"
    _write_like(target, POST_SOURCE.read_text(encoding="utf-8"))
    written.append(target)
    for page in (blog / "projects" / "modelling.md", blog / "projects" / "maps.md"):
        if _add_card(page):
            written.append(page)
    if _add_sources(blog / "data-sources.md"):
        written.append(blog / "data-sources.md")
    return written
