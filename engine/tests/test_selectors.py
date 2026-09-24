import json
from pathlib import Path

import pytest

from sprocket_engine.replay import replay
from sprocket_engine.selectors import SelectorError, resolve_selector

RECT_EXTRUDE = json.loads(
    (Path(__file__).parent / "golden" / "rect_extrude" / "document.json").read_text()
)

# The exact example from SPEC.md's "References and topological naming" section.
VERTICAL_EDGES = {
    "from": "part",
    "select": [
        {"op": "edges"},
        {"op": "filter_by", "axis": "Z"},
        {"op": "sort_by", "axis": "Z"},
        {"op": "slice", "start": -4},
    ],
}

TOP_FACE = {
    "from": "part",
    "select": [{"op": "faces"}, {"op": "sort_by", "axis": "Z"}, {"op": "last"}],
}


def test_spec_example_selects_the_four_vertical_edges(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    edges = resolve_selector(part, VERTICAL_EDGES)
    assert len(edges) == 4
    assert all(e.length == pytest.approx(10.0) for e in edges)  # extrude depth


def test_top_face_selector_identifies_the_correct_face(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    faces = resolve_selector(part, TOP_FACE)
    assert len(faces) == 1
    assert faces[0].center().Z == pytest.approx(10.0)  # extrude depth, not the base


def test_selector_survives_an_upstream_dimension_change(tmp_path: Path):
    """The whole point of a selector: it re-resolves after an edit instead of
    pointing at a stale index. Bump the extrude depth and confirm both
    selectors still find the same semantic geometry, not just any 4 edges."""
    cache_dir = tmp_path / "cache"
    changed = json.loads(json.dumps(RECT_EXTRUDE))
    changed["features"][1]["distance"] = 25
    part = replay(changed, cache_dir=cache_dir)

    edges = resolve_selector(part, VERTICAL_EDGES)
    assert len(edges) == 4
    assert all(e.length == pytest.approx(25.0) for e in edges)

    faces = resolve_selector(part, TOP_FACE)
    assert len(faces) == 1
    assert faces[0].center().Z == pytest.approx(25.0)


def test_unknown_selector_op_raises(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    with pytest.raises(SelectorError, match="unknown selector op"):
        resolve_selector(part, {"from": "part", "select": [{"op": "faces"}, {"op": "bogus"}]})
