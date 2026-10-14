from pathlib import Path

import pytest

from dlr.export import blog

GRID_PAGE = """---
title: Modeling
---

# Modeling

::::{grid} 1 1 2 2

:::{card} Existing post
:link: /posts/existing
:::

::::
"""

SOURCES_PAGE = """# Data Sources

## LiDAR & Elevation

- [VCGI (Vermont)](https://geodata.vermont.gov/) — tiles


---

## Energy

### Infrastructure

- [ENTSO-E](https://transparency.entsoe.eu/) — flows

### Renewables


---

*This list grows with each project. Last updated: May 2026.*
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def fake_blog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _write(tmp_path / "myst.yml", "version: 1\n")
    (tmp_path / "posts").mkdir()
    (tmp_path / "projects").mkdir()
    for name in ("modelling.md", "maps.md"):
        _write(tmp_path / "projects" / name, GRID_PAGE)
    _write(tmp_path / "data-sources.md", SOURCES_PAGE)
    post = tmp_path / "draft.md"
    _write(post, "---\ntitle: test\n---\n")
    monkeypatch.setattr(blog, "POST_SOURCE", post)
    return tmp_path


def test_sync_writes_post_cards_and_sources_once(fake_blog: Path) -> None:
    first = blog.sync(fake_blog)
    assert len(first) == 4
    modelling = (fake_blog / "projects" / "modelling.md").read_text(encoding="utf-8")
    assert modelling.index(f"/posts/{blog.POST_SLUG}") < modelling.index("/posts/existing")
    sources_path = fake_blog / "data-sources.md"
    sources = sources_path.read_text(encoding="utf-8")
    assert blog.CRLF.encode() not in sources_path.read_bytes()  # LF preserved
    assert "## Weather" in sources and "datacube.services.geo.ca" in sources
    assert sources.index("overpass-api.de") < sources.index("### Renewables")
    assert "Last updated: October 2026" in sources

    second = blog.sync(fake_blog)
    assert second == [fake_blog / "posts" / f"{blog.POST_SLUG}.md"]  # idempotent
    assert (fake_blog / "projects" / "modelling.md").read_text(encoding="utf-8") == modelling


def test_sync_refuses_a_directory_that_is_not_the_blog(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        blog.sync(tmp_path)
