"""The thread op. The cosmetic tap-hole case is the golden case thread_cosmetic_m3."""

import math

import pytest

from sprocket_engine.document import Document
from sprocket_engine.ops.thread import thread_geometry
from sprocket_engine.replay import replay
from sprocket_engine.viewer_files import features_summary

HOLE_FACE = {
    "select": [{"op": "faces"}, {"op": "within", "box": [-2, -2, 1, 2, 2, 3]}],
    "expected_count": 1,
}


def plate_with_hole(radius):
    return [
        {
            "id": "ps",
            "type": "sketch",
            "plane": "XY",
            "rect": {"width": 10, "height": 10},
        },
        {"id": "plate", "type": "extrude", "profile": "ps", "distance": 4},
        {
            "id": "hs",
            "type": "sketch",
            "plane": {"name": "XY", "offset": 4},
            "circle": {"radius": radius},
        },
        {
            "id": "tap",
            "type": "extrude",
            "profile": "hs",
            "base": "plate",
            "mode": "cut",
            "distance": -4,
        },
    ]


def shaft(radius, length=8):
    return [
        {"id": "ss", "type": "sketch", "plane": "XY", "circle": {"radius": radius}},
        {"id": "shaft", "type": "extrude", "profile": "ss", "distance": length},
    ]


SHAFT_FACE = {
    "select": [{"op": "faces"}, {"op": "within", "box": [-3, -3, 1, 3, 3, 7]}],
    "expected_count": 1,
}


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def test_a_hole_gets_an_internal_thread_along_its_axis():
    thread = {
        "id": "t",
        "type": "thread",
        "base": "tap",
        "face": HOLE_FACE,
        "designation": "M3",
    }
    _, ctx = build(plate_with_hole(1.25) + [thread])
    geometry = thread_geometry(thread, ctx)
    assert geometry["internal"] is True
    assert geometry["radius"] == pytest.approx(1.25)
    assert geometry["length"] == pytest.approx(4)
    assert geometry["pitch"] == 0.5
    assert abs(geometry["direction"][2]) == pytest.approx(1)
    assert geometry["start"][:2] == pytest.approx([0, 0])


def test_a_shaft_gets_an_external_thread_and_a_fine_pitch_is_kept():
    thread = {
        "id": "t",
        "type": "thread",
        "base": "shaft",
        "face": SHAFT_FACE,
        "designation": "M4",
        "pitch": 0.5,
    }
    _, ctx = build(shaft(2) + [thread])
    geometry = thread_geometry(thread, ctx)
    assert (geometry["internal"], geometry["pitch"], geometry["length"]) == (
        False,
        0.5,
        pytest.approx(8),
    )


def test_a_face_the_wrong_size_is_refused_with_the_range():
    thread = {
        "id": "t",
        "type": "thread",
        "base": "tap",
        "face": HOLE_FACE,
        "designation": "M4",
    }
    with pytest.raises(
        ValueError,
        match="a M4 hole is 3.125 to 4.05 mm across, and this face is 2.5 mm",
    ):
        build(plate_with_hole(1.25) + [thread])


def test_a_flat_face_is_refused():
    top = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
        "expected_count": 1,
    }
    with pytest.raises(ValueError, match="needs a cylindrical face"):
        build(
            plate_with_hole(1.25)
            + [
                {
                    "id": "t",
                    "type": "thread",
                    "base": "tap",
                    "face": top,
                    "designation": "M3",
                }
            ]
        )


def test_the_viewer_gets_the_thread_to_draw():
    features = plate_with_hole(1.25) + [
        {
            "id": "t",
            "type": "thread",
            "base": "tap",
            "face": HOLE_FACE,
            "designation": "M3",
        }
    ]
    document = Document(features)
    _, ctx = replay(document.for_replay(), return_context=True)
    entry = next(
        item for item in features_summary(document, None, ctx) if item["id"] == "t"
    )
    assert entry["thread"]["designation"] == "M3"
    assert entry["thread"]["internal"] is True


def test_a_modeled_internal_thread_fills_between_the_tap_hole_and_the_nominal():
    pytest.importorskip("bd_warehouse")
    thread = {
        "id": "t",
        "type": "thread",
        "base": "tap",
        "face": HOLE_FACE,
        "designation": "M3",
        "modeled": True,
    }
    part, _ = build(plate_with_hole(1.25) + [thread])
    assert part.is_valid and len(part.solids()) == 1
    nominal_hole = 400 - math.pi * 1.5**2 * 4
    tap_hole = 400 - math.pi * 1.25**2 * 4
    assert nominal_hole < part.volume < tap_hole


def test_a_modeled_external_thread_lies_between_the_core_and_the_shaft():
    pytest.importorskip("bd_warehouse")
    thread = {
        "id": "t",
        "type": "thread",
        "base": "shaft",
        "face": SHAFT_FACE,
        "designation": "M4",
        "modeled": True,
    }
    part, _ = build(shaft(2) + [thread])
    assert part.is_valid and len(part.solids()) == 1
    minor_radius = 2 - 0.6134 * 0.7
    assert math.pi * minor_radius**2 * 8 < part.volume < math.pi * 2**2 * 8
