import math

import pytest

from sprocket_engine.replay import replay

BOX = [
    {"id": "s", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "b", "type": "extrude", "profile": "s", "distance": 10},
]
TOP = {
    "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
    "expected_count": 1,
}


def build(*features):
    return replay({"features": BOX + list(features)})


# ---- shell -----------------------------------------------------------------


def test_shell_open_at_the_top_makes_a_tray():
    part = build({"id": "h", "type": "shell", "base": "b", "thickness": 2, "open": TOP})
    assert part.volume == pytest.approx(4000 - 16 * 16 * 8)
    assert pytest.approx(10) == part.bounding_box().max.Z


def test_shell_with_no_open_face_is_a_closed_hollow():
    part = build({"id": "h", "type": "shell", "base": "b", "thickness": 2})
    assert part.volume == pytest.approx(4000 - 16 * 16 * 6)


def test_outward_shell_keeps_the_inside_and_grows_the_outside():
    part = build(
        {
            "id": "h",
            "type": "shell",
            "base": "b",
            "thickness": 2,
            "open": TOP,
            "outward": True,
        }
    )
    assert part.volume == pytest.approx(24 * 24 * 12 - 4000)
    box = part.bounding_box()
    assert pytest.approx((-12, -2, 10)) == (box.min.X, box.min.Z, box.max.Z)


def test_shell_open_selector_must_match_faces():
    nothing = {
        "select": [
            {"op": "faces"},
            {"op": "within", "box": [100, 100, 100, 101, 101, 101]},
        ]
    }
    with pytest.raises(Exception, match="matched no faces"):
        build(
            {"id": "h", "type": "shell", "base": "b", "thickness": 2, "open": nothing}
        )


# ---- hole ------------------------------------------------------------------

HOLE = {"id": "h", "type": "hole", "base": "b", "points": [[0, 0, 10]], "diameter": 4}


def test_a_simple_hole_of_given_depth():
    assert build({**HOLE, "depth": 5}).volume == pytest.approx(4000 - math.pi * 4 * 5)


def test_a_through_hole_goes_all_the_way():
    part = build({**HOLE, "through": True})
    assert part.volume == pytest.approx(4000 - math.pi * 4 * 10)
    assert len(part.faces()) == 7


def test_a_counterbore_adds_the_head_cylinder():
    part = build(
        {
            **HOLE,
            "through": True,
            "kind": "counterbore",
            "head_diameter": 8,
            "head_depth": 3,
        }
    )
    assert part.volume == pytest.approx(
        4000 - math.pi * 4 * 10 - math.pi * (16 - 4) * 3
    )


def test_a_countersink_adds_a_90_degree_cone():
    part = build({**HOLE, "through": True, "kind": "countersink", "head_diameter": 8})
    # Cone height (4 - 2) / tan(45) = 2. Frustum pi*h/3*(R^2 + Rr + r^2) less the bore inside it.
    extra = math.pi * 2 / 3 * (16 + 8 + 4) - math.pi * 4 * 2
    assert part.volume == pytest.approx(4000 - math.pi * 4 * 10 - extra)
    assert {face.geom_type.name for face in part.faces()} == {
        "PLANE",
        "CYLINDER",
        "CONE",
    }


def test_holes_at_the_circle_centres_of_a_sketch_drill_against_its_normal():
    on_top = {"origin": [0, 0, 10], "normal": [0, 0, 1], "x_dir": [1, 0, 0]}
    marks = {
        "id": "m",
        "type": "sketch",
        "plane": on_top,
        "entities": [
            {"id": "e1", "kind": "circle", "c": [5, 5], "r": 1},
            {"id": "e2", "kind": "circle", "c": [-5, -5], "r": 1},
            {"id": "e3", "kind": "circle", "c": [5, -5], "r": 1, "construction": True},
        ],
    }
    part = build(
        marks,
        {
            "id": "h",
            "type": "hole",
            "base": "b",
            "sketch": "m",
            "diameter": 4,
            "depth": 6,
        },
    )
    assert part.volume == pytest.approx(4000 - 2 * math.pi * 4 * 6)
    picked = build(
        marks,
        {
            "id": "h",
            "type": "hole",
            "base": "b",
            "sketch": "m",
            "entities": ["e3"],
            "diameter": 4,
            "depth": 6,
        },
    )
    assert picked.volume == pytest.approx(4000 - math.pi * 4 * 6)


def test_a_counterbore_without_its_head_is_refused():
    with pytest.raises(ValueError, match="head_diameter"):
        build({**HOLE, "depth": 5, "kind": "counterbore"})
