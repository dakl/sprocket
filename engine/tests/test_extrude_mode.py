"""`mode` on extrude: the sign of `distance` picks a direction, `mode` the combine.

Before `mode`, a base plus a negative distance always cut, so a part could not
grow downward from a sketch on its bottom face.
"""

import pytest

from sprocket_engine.replay import replay

BOX = [
    {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
    {"id": "f3", "type": "sketch", "plane": "XY", "rect": {"width": 10, "height": 10}},
]
BOX_VOLUME = 20 * 20 * 10
PAD_VOLUME = 10 * 10 * 4


def build(**extrude):
    feature = {"id": "f4", "type": "extrude", "profile": "f3", "base": "f2", **extrude}
    return replay({"features": BOX + [feature]})


def test_join_with_a_negative_distance_grows_the_part_below_the_sketch_plane():
    part = build(distance=-4, mode="join")
    assert part.volume == pytest.approx(BOX_VOLUME + PAD_VOLUME)
    assert pytest.approx(-4) == part.bounding_box().min.Z


def test_cut_with_a_positive_distance_cuts_up_into_the_part():
    part = build(distance=4, mode="cut")
    assert part.volume == pytest.approx(BOX_VOLUME - PAD_VOLUME)


def test_intersect_keeps_only_the_overlap():
    part = build(distance=4, mode="intersect")
    assert part.volume == pytest.approx(PAD_VOLUME)


def test_no_mode_keeps_the_sign_rule_for_old_documents():
    # A negative distance cuts, here into the air below the box, which is the gap `mode` closes.
    legacy = build(distance=-4)
    assert legacy.volume == pytest.approx(BOX_VOLUME)
    assert pytest.approx(0) == legacy.bounding_box().min.Z


def test_symmetric_extrudes_both_ways():
    part = replay(
        {
            "features": [
                BOX[0],
                {
                    "id": "f2",
                    "type": "extrude",
                    "profile": "f1",
                    "distance": 5,
                    "symmetric": True,
                },
            ]
        }
    )
    box = part.bounding_box()
    assert pytest.approx((-5, 5)) == (box.min.Z, box.max.Z)


def test_new_takes_no_base_and_combines_need_one():
    with pytest.raises(ValueError, match="takes no base"):
        build(distance=4, mode="new")
    with pytest.raises(ValueError, match="needs a base"):
        replay(
            {
                "features": [
                    BOX[0],
                    {
                        "id": "f2",
                        "type": "extrude",
                        "profile": "f1",
                        "distance": 5,
                        "mode": "join",
                    },
                ]
            }
        )


def test_a_plane_facing_down_has_v_along_world_minus_y():
    """Documented in the sketch schema: v = normal x x_dir."""
    down = {"origin": [0, 0, 0], "normal": [0, 0, -1], "x_dir": [1, 0, 0]}
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": down,
        "rect": {"width": 2, "height": 2, "center": [0, 5]},
    }
    part = replay(
        {
            "features": [
                sketch,
                {"id": "e", "type": "extrude", "profile": "s", "distance": 1},
            ]
        }
    )
    assert pytest.approx(-5) == part.center().Y
    assert pytest.approx(-1) == part.bounding_box().min.Z
