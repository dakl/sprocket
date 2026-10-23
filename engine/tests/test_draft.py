"""The draft op. The positive-angle case is the golden case draft_box_sides."""

import math

import pytest

from sprocket_engine.document import Document
from sprocket_engine.model import JSONObject
from sprocket_engine.replay import replay

BOX: list[JSONObject] = [
    {"id": "bs", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 10}},
    {"id": "box", "type": "extrude", "profile": "bs", "distance": 10},
]
SIDES: JSONObject = {
    "select": [{"op": "faces"}, {"op": "within", "box": [-11, -6, 1, 11, 6, 9]}],
    "expected_count": 4,
}
TOP: JSONObject = {
    "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
    "expected_count": 1,
}


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def top_size(part):
    top = sorted(part.faces(), key=lambda face: face.center().Z)[-1]
    size = top.bounding_box().size
    return size.X, size.Y


def test_a_negative_angle_leans_the_walls_outward() -> None:
    part, _ = build(
        BOX
        + [
            {
                "id": "d",
                "type": "draft",
                "base": "box",
                "faces": SIDES,
                "angle": -5,
                "neutral": "XY",
            }
        ]
    )
    grow = 20 * math.tan(math.radians(5))
    assert top_size(part) == pytest.approx((20 + grow, 10 + grow))


def test_a_neutral_plane_on_the_top_face_keeps_the_top_and_grows_the_base() -> None:
    neutral: JSONObject = {"feature": "box", "face": TOP}
    part, _ = build(
        BOX
        + [
            {
                "id": "d",
                "type": "draft",
                "base": "box",
                "faces": SIDES,
                "angle": 5,
                "neutral": neutral,
            }
        ]
    )
    assert top_size(part) == pytest.approx((20, 10))
    assert (
        pytest.approx(20 + 20 * math.tan(math.radians(5))) == part.bounding_box().size.X
    )


def test_drafting_a_face_that_lies_in_the_pull_plane_fails_with_a_reason() -> None:
    with pytest.raises(ValueError, match="draft d failed"):
        _ = build(
            BOX
            + [
                {
                    "id": "d",
                    "type": "draft",
                    "base": "box",
                    "faces": TOP,
                    "angle": 5,
                    "neutral": "XY",
                }
            ]
        )
