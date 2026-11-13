"""The loft op. The geometry is the golden case loft_square_frustum; these
cover a smooth skin, combining, a sketch on a lofted side, and the errors.
"""

import math

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.regions import ProfileError
from sprocket_engine.replay import replay


def circle(sketch_id, z, radius):
    return {
        "id": sketch_id,
        "type": "sketch",
        "plane": {"name": "XY", "offset": z},
        "circle": {"radius": radius},
    }


def square(sketch_id, z, size):
    return {
        "id": sketch_id,
        "type": "sketch",
        "plane": {"name": "XY", "offset": z},
        "rect": {"width": size, "height": size},
    }


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def test_two_circles_loft_to_a_cone_frustum():
    part, _ = build(
        [
            circle("a", 0, 10),
            circle("b", 10, 5),
            {"id": "l", "type": "loft", "sections": ["a", "b"]},
        ]
    )
    assert part.volume == pytest.approx(math.pi * 10 / 3 * (100 + 50 + 25), rel=1e-4)


def test_a_smooth_loft_bulges_past_a_ruled_one_through_a_wide_middle():
    sections = [circle("a", 0, 5), circle("b", 10, 10), circle("c", 20, 5)]
    smooth, _ = build(
        sections + [{"id": "l", "type": "loft", "sections": ["a", "b", "c"]}]
    )
    ruled, _ = build(
        sections
        + [{"id": "l", "type": "loft", "sections": ["a", "b", "c"], "ruled": True}]
    )
    two_frustums = 2 * math.pi * 10 / 3 * (25 + 50 + 100)
    assert ruled.volume == pytest.approx(two_frustums, rel=1e-4)
    assert smooth.volume > ruled.volume


def test_cut_mode_removes_the_loft_from_the_base():
    block = [
        square("bs", 0, 30),
        {"id": "block", "type": "extrude", "profile": "bs", "distance": 10},
    ]
    loft = {
        "id": "l",
        "type": "loft",
        "sections": ["a", "b"],
        "ruled": True,
        "base": "block",
        "mode": "cut",
    }
    part, ctx = build(block + [square("a", 0, 20), square("b", 10, 10), loft])
    assert part.volume == pytest.approx(30 * 30 * 10 - 7000 / 3, rel=1e-6)
    assert [body["id"] for body in ctx.bodies] == ["block"]


def test_a_sketch_goes_on_the_flat_side_of_a_ruled_loft():
    """The side is a B-spline surface that happens to be flat."""
    side = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "X", "end": "max"}],
        "expected_count": 1,
    }
    features = [
        square("a", 0, 20),
        square("b", 10, 10),
        {"id": "l", "type": "loft", "sections": ["a", "b"], "ruled": True},
        {
            "id": "s",
            "type": "sketch",
            "plane": {"feature": "l", "face": side},
            "circle": {"radius": 1},
        },
    ]
    _, ctx = build(features)
    disc = ctx.results["s"].faces()[0]
    normal = disc.normal_at(disc.center())
    assert pytest.approx((10 / math.sqrt(125), 5 / math.sqrt(125))) == (
        normal.X,
        normal.Z,
    )


def test_a_section_with_two_regions_is_refused_by_name():
    two = {
        "id": "a",
        "type": "sketch",
        "plane": "XY",
        "entities": [
            {"id": "e1", "kind": "circle", "c": [0, 0], "r": 2},
            {"id": "e2", "kind": "circle", "c": [10, 0], "r": 2},
        ],
        "constraints": [],
    }
    with pytest.raises(ProfileError, match="'a' has 2 closed regions"):
        build(
            [
                two,
                circle("b", 10, 5),
                {"id": "l", "type": "loft", "sections": ["a", "b"]},
            ]
        )


def test_a_section_cannot_be_deleted_while_a_loft_uses_it():
    document = Document(
        [
            circle("a", 0, 10),
            circle("b", 10, 5),
            {"id": "l", "type": "loft", "sections": ["a", "b"]},
        ]
    )
    with pytest.raises(DocumentError, match="'l' still reference it"):
        document.apply_edits([{"op": "delete", "id": "b"}], None, "test")
