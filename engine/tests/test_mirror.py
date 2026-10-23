"""The mirror op: a solid reflected about a plane and combined."""

import pytest

from sprocket_engine.document import Document
from sprocket_engine.model import JSONObject
from sprocket_engine.replay import replay

PLATE: list[JSONObject] = [
    {
        "id": "ps",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 20, "height": 10, "center": [10, 0]},
    },
    {"id": "plate", "type": "extrude", "profile": "ps", "distance": 3},
]
PEG: list[JSONObject] = [
    {
        "id": "gs",
        "type": "sketch",
        "plane": {"name": "XY", "offset": 30},
        "rect": {"width": 2, "height": 2, "center": [5, 0]},
    },
    {"id": "peg", "type": "extrude", "profile": "gs", "distance": 2},
]


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def test_without_a_base_the_original_and_its_mirror_are_a_new_body_and_the_tool_body_goes() -> (
    None
):
    part, ctx = build(
        PLATE + PEG + [{"id": "m", "type": "mirror", "tool": "peg", "plane": "YZ"}]
    )
    assert [body.id for body in ctx.bodies] == ["plate", "m"]
    pegs = ctx.results["m"]
    assert len(pegs.solids()) == 2
    assert pegs.volume == pytest.approx(16)
    box = pegs.bounding_box()
    assert pytest.approx((-6, 6)) == (box.min.X, box.max.X)


def test_a_face_plane_mirrors_about_that_face() -> None:
    """The plate's +X face is at x = 20, so a peg at x = 5 lands at x = 35."""
    face: JSONObject = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "X", "end": "max"}],
        "expected_count": 1,
    }
    part, ctx = build(
        PLATE
        + PEG
        + [
            {
                "id": "m",
                "type": "mirror",
                "tool": "peg",
                "plane": {"feature": "plate", "face": face},
            }
        ]
    )
    box = ctx.results["m"].bounding_box()
    assert pytest.approx((4, 36)) == (box.min.X, box.max.X)


def test_cut_removes_the_original_and_the_mirror_from_the_base() -> None:
    hole_s: JSONObject = {
        "id": "hs",
        "type": "sketch",
        "plane": "XY",
        "circle": {"radius": 1, "center": [15, 0]},
    }
    hole: JSONObject = {"id": "h", "type": "extrude", "profile": "hs", "distance": 3}
    plate2: JSONObject = {
        "id": "p2s",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 40, "height": 10},
    }
    wide: JSONObject = {
        "id": "wide",
        "type": "extrude",
        "profile": "p2s",
        "distance": 3,
    }
    part, ctx = build(
        [
            plate2,
            wide,
            hole_s,
            hole,
            {
                "id": "m",
                "type": "mirror",
                "tool": "h",
                "base": "wide",
                "mode": "cut",
                "plane": "YZ",
            },
        ]
    )
    assert part.volume == pytest.approx(
        40 * 10 * 3 - 2 * 3.141592653589793 * 3, rel=1e-6
    )
    assert [body.id for body in ctx.bodies] == ["wide"]


def test_the_mirror_plane_feature_is_a_reference_that_cannot_be_deleted() -> None:
    from sprocket_engine.document import DocumentError

    face: JSONObject = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "X", "end": "max"}],
        "expected_count": 1,
    }
    document = Document(
        PLATE
        + PEG
        + [
            {
                "id": "m",
                "type": "mirror",
                "tool": "peg",
                "plane": {"feature": "plate", "face": face},
            }
        ]
    )
    with pytest.raises(DocumentError):
        _ = document.apply_edits([{"op": "delete", "id": "plate"}], None, "test")
