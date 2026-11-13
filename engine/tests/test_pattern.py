import math

import pytest

from sprocket_engine.replay import replay
from sprocket_engine.ops.sketch import sketch_entities

BOARD = [
    {
        "id": "bs",
        "type": "sketch",
        "plane": "XY",
        "rect": {"width": 30, "height": 10, "center": [15, 5]},
    },
    {"id": "board", "type": "extrude", "profile": "bs", "distance": 1},
    # One 1 x 1 pin, 2 mm tall, standing on the board at (2, 2).
    {
        "id": "ps",
        "type": "sketch",
        "plane": {"name": "XY", "offset": 1},
        "rect": {"width": 1, "height": 1, "center": [2, 2]},
    },
    {"id": "pin", "type": "extrude", "profile": "ps", "distance": 2},
]
GRID = {
    "linear": {"direction": [1, 0, 0], "count": 5, "spacing": 3},
    "linear2": {"direction": [0, 1, 0], "count": 2, "spacing": 4},
}


def build(*features):
    return replay({"features": BOARD + list(features)})


def test_a_grid_of_pins_joins_the_board():
    part = build(
        {"id": "pp", "type": "pattern", "tool": "pin", "base": "board", **GRID}
    )
    assert part.volume == pytest.approx(300 + 10 * 2)
    assert len(part.solids()) == 1
    box = part.bounding_box()
    assert (box.max.X, box.max.Y) == pytest.approx(
        (30, 10)
    )  # the board edge, pins inside it
    assert len(part.faces()) == 6 + 10 * 5  # the board, then 4 walls and a top per pin


def test_cut_mode_drills_the_copies_out():
    tall = {
        "id": "pin",
        "type": "extrude",
        "profile": "ps",
        "distance": -1,
    }  # 1 mm down, through the board
    part = replay(
        {
            "features": BOARD[:3]
            + [
                tall,
                {
                    "id": "pp",
                    "type": "pattern",
                    "tool": "pin",
                    "base": "board",
                    "mode": "cut",
                    **GRID,
                },
            ]
        }
    )
    assert part.volume == pytest.approx(300 - 10)


def test_a_circular_pattern_with_no_base_is_a_new_body_of_copies():
    _, ctx = replay(
        {
            "features": BOARD
            + [
                {
                    "id": "pp",
                    "type": "pattern",
                    "tool": "pin",
                    "circular": {"axis": "Z", "count": 4},
                }
            ]
        },
        return_context=True,
    )
    # The pin body ends in the pattern, which is a body of its own beside the board.
    assert [body["id"] for body in ctx.bodies] == ["board", "pp"]
    part = ctx.results["pp"]
    assert part.volume == pytest.approx(4 * 2)
    assert len(part.solids()) == 4
    centre = part.center()
    assert (centre.X, centre.Y) == pytest.approx((0, 0), abs=1e-9)


SQUARE = [
    {"id": "e1", "kind": "line", "p0": [0, 0], "p1": [2, 0]},
    {"id": "e2", "kind": "line", "p0": [2, 0], "p1": [2, 2]},
    {"id": "e3", "kind": "line", "p0": [2, 2], "p1": [0, 2]},
    {"id": "e4", "kind": "line", "p0": [0, 2], "p1": [0, 0]},
]


def test_a_sketch_pattern_copies_entities_before_regions_are_found():
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": "XY",
        "entities": SQUARE,
        "patterns": [
            {
                "entities": ["e1", "e2", "e3", "e4"],
                "linear": {"count": 3, "step": [5, 0]},
            }
        ],
    }
    whole = replay(
        {
            "features": [
                sketch,
                {"id": "x", "type": "extrude", "profile": "s", "distance": 1},
            ]
        }
    )
    assert whole.volume == pytest.approx(3 * 4)
    one = replay(
        {
            "features": [
                sketch,
                {
                    "id": "x",
                    "type": "extrude",
                    "distance": 1,
                    "profiles": [
                        {"sketch": "s", "bounded_by": ["e1#2", "e2#2", "e3#2", "e4#2"]}
                    ],
                },
            ]
        }
    )
    assert one.bounding_box().min.X == pytest.approx(10)


def test_a_circular_sketch_pattern_turns_copies_about_the_centre():
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": "XY",
        "entities": [{"id": "c", "kind": "circle", "c": [5, 0], "r": 1}],
        "patterns": [{"entities": ["c"], "circular": {"center": [0, 0], "count": 6}}],
    }
    centres = [entity["c"] for entity in sketch_entities(sketch)]
    assert len(centres) == 6
    assert centres[3] == pytest.approx([-5, 0], abs=1e-9)
    part = replay(
        {
            "features": [
                sketch,
                {"id": "x", "type": "extrude", "profile": "s", "distance": 1},
            ]
        }
    )
    assert part.volume == pytest.approx(6 * math.pi)


def test_a_sketch_pattern_must_name_real_entities():
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": "XY",
        "entities": SQUARE,
        "patterns": [{"entities": ["e9"], "linear": {"count": 2, "step": [5, 0]}}],
    }
    with pytest.raises(ValueError, match="e9"):
        replay({"features": [sketch]})
