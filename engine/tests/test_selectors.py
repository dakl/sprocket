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

TOP_EDGES = {"from": "part", "select": [{"op": "edges"}, {"op": "at_end", "axis": "Z", "end": "max"}]}

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


def test_new_since_keeps_what_a_feature_made_or_changed(tmp_path: Path):
    """After a fillet, the new faces are the round ones and the faces it trimmed.
    The bottom face was not touched, so it is not new."""
    document = {
        "features": RECT_EXTRUDE["features"]
        + [{"id": "r1", "type": "fillet", "base": RECT_EXTRUDE["features"][-1]["id"], "radius": 1, "edges": TOP_EDGES}],
        "rollback_index": len(RECT_EXTRUDE["features"]) + 1,
    }
    part, ctx = replay(document, cache_dir=tmp_path / "cache", return_context=True)
    new = resolve_selector(part, {"select": [{"op": "faces"}, {"op": "new_since", "feature": "r1"}]}, ctx)
    kinds = [face.geom_type.name for face in new]
    assert kinds.count("CYLINDER") >= 4
    assert len(new) < len(part.faces())
    bottom = resolve_selector(part, {"select": [{"op": "faces"}, {"op": "sort_by", "axis": "Z"}, {"op": "first"}]})[0]
    assert not any(face.is_same(bottom) for face in new)


def test_new_since_can_be_followed_by_other_steps(tmp_path: Path):
    part, ctx = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache", return_context=True)
    top = {"select": [{"op": "faces"}, {"op": "new_since", "feature": RECT_EXTRUDE["features"][-1]["id"]}, {"op": "sort_by", "axis": "Z"}, {"op": "last"}]}
    assert len(resolve_selector(part, top, ctx)) == 1


def test_new_since_without_a_context_says_what_is_missing(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    with pytest.raises(SelectorError, match="not a feature before this one"):
        resolve_selector(part, {"select": [{"op": "faces"}, {"op": "new_since", "feature": "f1"}]})


def test_at_end_keeps_everything_level_with_the_highest_or_lowest(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    top = resolve_selector(part, {"select": [{"op": "edges"}, {"op": "at_end", "axis": "Z", "end": "max"}]})
    bottom = resolve_selector(part, {"select": [{"op": "edges"}, {"op": "at_end", "axis": "Z", "end": "min"}]})
    assert len(top) == len(bottom) == 4
    assert {round(edge.center().Z, 6) for edge in top} == {10.0}
    assert {round(edge.center().Z, 6) for edge in bottom} == {0.0}


def test_a_count_that_does_not_match_is_an_error(tmp_path: Path):
    part = replay(RECT_EXTRUDE, cache_dir=tmp_path / "cache")
    with pytest.raises(SelectorError, match="expected 3"):
        resolve_selector(part, {**VERTICAL_EDGES, "expected_count": 3})
    assert len(resolve_selector(part, {**VERTICAL_EDGES, "expected_count": 4})) == 4
