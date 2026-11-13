"""The primitive and move ops. The golden cases primitive_box_cylinder_cut and
move_copy_rotated cover a box, a cylinder cut, and a rotated copy."""

import math

import pytest

from sprocket_engine.document import Document
from sprocket_engine.replay import replay


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def volume(features):
    part, _ = build(features)
    return part.volume


@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        ({"shape": "sphere", "radius": 4}, 4 / 3 * math.pi * 64),
        ({"shape": "cone", "radius": 5, "height": 12}, math.pi * 25 * 12 / 3),
        (
            {"shape": "cone", "radius": 5, "top_radius": 2, "height": 9},
            math.pi * 9 / 3 * (25 + 10 + 4),
        ),
        (
            {"shape": "torus", "major_radius": 10, "minor_radius": 2},
            2 * math.pi**2 * 10 * 4,
        ),
        ({"shape": "cylinder", "radius": 3, "height": 7}, math.pi * 9 * 7),
    ],
)
def test_each_shape_has_its_textbook_volume(feature, expected):
    assert volume([{"id": "p", "type": "primitive", **feature}]) == pytest.approx(
        expected, rel=1e-6
    )


def test_a_box_stands_on_an_offset_plane_and_at_moves_it_along_the_plane():
    part, _ = build(
        [
            {
                "id": "p",
                "type": "primitive",
                "shape": "box",
                "width": 10,
                "depth": 4,
                "height": 2,
                "plane": {"name": "XZ", "offset": 3},
                "at": [5, 1],
            }
        ]
    )
    box = part.bounding_box()
    # XZ: u is X, v is Z, and the normal is -Y, so the box rises toward -Y from y = -3.
    assert (box.min.X, box.max.X) == pytest.approx((0, 10))
    assert (box.min.Z, box.max.Z) == pytest.approx((-1, 3))
    assert sorted((box.min.Y, box.max.Y)) == pytest.approx(sorted((-3, -5)))


def test_a_sphere_is_centred_on_the_plane():
    part, _ = build(
        [
            {
                "id": "p",
                "type": "primitive",
                "shape": "sphere",
                "radius": 2,
                "plane": {"name": "XY", "offset": 10},
            }
        ]
    )
    centre = part.center()
    assert (centre.X, centre.Y, centre.Z) == pytest.approx((0, 0, 10))


def test_a_missing_size_or_a_fat_torus_says_what_is_wrong():
    with pytest.raises(ValueError, match="needs width"):
        build(
            [{"id": "p", "type": "primitive", "shape": "box", "depth": 1, "height": 1}]
        )
    with pytest.raises(ValueError, match="smaller than its major_radius"):
        build(
            [
                {
                    "id": "p",
                    "type": "primitive",
                    "shape": "torus",
                    "major_radius": 2,
                    "minor_radius": 2,
                }
            ]
        )


def test_intersect_keeps_the_common_part():
    features = [
        {
            "id": "a",
            "type": "primitive",
            "shape": "box",
            "width": 10,
            "depth": 10,
            "height": 10,
        },
        {
            "id": "b",
            "type": "primitive",
            "shape": "box",
            "width": 10,
            "depth": 10,
            "height": 10,
            "at": [5, 0],
            "base": "a",
            "mode": "intersect",
        },
    ]
    assert volume(features) == pytest.approx(500)


BAR = {
    "id": "bar",
    "type": "primitive",
    "shape": "box",
    "width": 20,
    "depth": 10,
    "height": 5,
}


def test_move_with_base_moves_the_body_itself():
    part, ctx = build(
        [BAR, {"id": "m", "type": "move", "base": "bar", "translate": [0, 0, 7]}]
    )
    assert len(ctx.bodies) == 1
    assert part.bounding_box().min.Z == pytest.approx(7)


def test_move_with_tool_leaves_the_original_and_makes_a_new_body():
    part, ctx = build(
        [BAR, {"id": "m", "type": "move", "tool": "bar", "translate": [0, 30, 0]}]
    )
    assert [body["id"] for body in ctx.bodies] == ["bar", "m"]
    assert part.volume == pytest.approx(2000)


def test_move_rotates_about_an_axis_feature():
    axis = {
        "id": "ax",
        "type": "axis",
        "axis": {"origin": [10, 0, 0], "direction": [0, 0, 1]},
    }
    part, _ = build(
        [
            BAR,
            axis,
            {
                "id": "m",
                "type": "move",
                "base": "bar",
                "rotate": {"axis": {"construct": "ax"}, "angle": 180},
            },
        ]
    )
    box = part.bounding_box()
    # Half a turn about x = 10 sends x -10..10 to 10..30.
    assert (box.min.X, box.max.X) == pytest.approx((10, 30))


def test_move_of_something_that_is_not_a_solid_is_refused():
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 1, "height": 1},
    }
    with pytest.raises(ValueError, match="not a solid"):
        build(
            [sketch, {"id": "m", "type": "move", "base": "s", "translate": [1, 0, 0]}]
        )
