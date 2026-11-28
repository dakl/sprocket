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
    _, ctx = build([*plate_with_hole(1.25), thread])
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
    _, ctx = build([*shaft(2), thread])
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
        build([*plate_with_hole(1.25), thread])


def test_a_flat_face_is_refused():
    top = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
        "expected_count": 1,
    }
    with pytest.raises(ValueError, match="needs a cylindrical face"):
        build(
            [
                *plate_with_hole(1.25),
                {
                    "id": "t",
                    "type": "thread",
                    "base": "tap",
                    "face": top,
                    "designation": "M3",
                },
            ]
        )


def test_the_viewer_gets_the_thread_to_draw():
    features = [
        *plate_with_hole(1.25),
        {
            "id": "t",
            "type": "thread",
            "base": "tap",
            "face": HOLE_FACE,
            "designation": "M3",
        },
    ]
    document = Document(features)
    _, ctx = replay(document.for_replay(), return_context=True)
    entry = next(item for item in features_summary(document, None, ctx) if item["id"] == "t")
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
    part, _ = build([*plate_with_hole(1.25), thread])
    assert part.is_valid
    assert len(part.solids()) == 1
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
    part, _ = build([*shaft(2), thread])
    assert part.is_valid
    assert len(part.solids()) == 1
    minor_radius = 2 - 0.6134 * 0.7
    assert math.pi * minor_radius**2 * 8 < part.volume < math.pi * 2**2 * 8


def test_a_partial_thread_starts_after_the_offset_and_from_end_flips_it() -> None:
    base = {"id": "t", "type": "thread", "base": "shaft", "face": SHAFT_FACE, "designation": "M4"}
    _, ctx = build([*shaft(2), base])
    whole = thread_geometry(base, ctx)
    part = thread_geometry({**base, "length": 3, "offset": 1}, ctx)
    flipped = thread_geometry({**base, "length": 3, "offset": 1, "from_end": True}, ctx)
    assert part["length"] == pytest.approx(3)
    assert part["direction"] == pytest.approx(whole["direction"])
    assert part["start"] == pytest.approx(
        [a + b for a, b in zip(whole["start"], whole["direction"], strict=True)]
    )
    assert flipped["direction"] == pytest.approx([-v for v in whole["direction"]])
    # The far end of an 8 mm face, less the 1 mm offset.
    assert flipped["start"] == pytest.approx(
        [a + 7 * b for a, b in zip(whole["start"], whole["direction"], strict=True)]
    )


def test_a_thread_longer_than_its_face_is_refused() -> None:
    thread = {
        "id": "t",
        "type": "thread",
        "base": "shaft",
        "face": SHAFT_FACE,
        "designation": "M4",
        "offset": 2,
        "length": 7,
    }
    with pytest.raises(ValueError, match="does not fit on a face 8 mm long"):
        build([*shaft(2), thread])


def test_the_hand_reaches_the_viewer() -> None:
    thread = {
        "id": "t",
        "type": "thread",
        "base": "shaft",
        "face": SHAFT_FACE,
        "designation": "M4",
        "hand": "left",
    }
    _, ctx = build([*shaft(2), thread])
    assert thread_geometry(thread, ctx)["hand"] == "left"


def block_with_hole(radius: float, height: float = 12, side: float = 18) -> list[dict]:
    return [
        {"id": "bs", "type": "sketch", "plane": "XY", "rect": {"width": side, "height": side}},
        {"id": "block", "type": "extrude", "profile": "bs", "distance": height},
        {
            "id": "hs",
            "type": "sketch",
            "plane": {"name": "XY", "offset": height},
            "circle": {"radius": radius},
        },
        {
            "id": "tap",
            "type": "extrude",
            "profile": "hs",
            "base": "block",
            "mode": "cut",
            "distance": -height,
        },
    ]


BLOCK_HOLE_FACE = {
    "select": [{"op": "faces"}, {"op": "within", "box": [-4, -4, 1, 4, 4, 11]}],
    "expected_count": 1,
}


def m6_teeth(extra: dict | None = None) -> tuple[float, float]:
    """An M6 x 12 internal thread's teeth volume, and the annulus they sit in.

    The teeth are what the thread leaves past a plain 6 mm bore; the annulus
    runs from the nominal to the minor diameter.
    """
    thread = {
        "id": "t",
        "type": "thread",
        "base": "tap",
        "face": BLOCK_HOLE_FACE,
        "designation": "M6",
        "modeled": True,
        **(extra or {}),
    }
    part, _ = build([*block_with_hole(2.5), thread])
    assert part.is_valid
    assert len(part.solids()) == 1
    plain = 18 * 18 * 12 - math.pi * 3**2 * 12
    annulus = math.pi * (3**2 - (3 - 1.0825 / 2) ** 2) * 12
    return part.volume - plain, annulus


def test_a_long_modeled_internal_thread_keeps_all_its_teeth() -> None:
    # bd_warehouse's own internal thread lost one turn in four here, as loose solids.
    pytest.importorskip("bd_warehouse")
    teeth, annulus = m6_teeth()
    # ISO teeth fill a little over half the annulus.
    assert 0.45 * annulus < teeth < 0.65 * annulus


def test_clearance_opens_a_modeled_hole_thread() -> None:
    pytest.importorskip("bd_warehouse")
    nominal, _ = m6_teeth()
    loose, _ = m6_teeth({"clearance": 0.4})
    # 0.2 mm off each flank and crest takes material out of every turn.
    assert loose < nominal - 5


def test_a_chamfered_left_hand_hole_thread_is_one_valid_solid() -> None:
    pytest.importorskip("bd_warehouse")
    plain, _ = m6_teeth({"hand": "left"})
    chamfered, _ = m6_teeth({"hand": "left", "ends": "chamfer"})
    assert chamfered < plain


def test_clearance_thins_a_modeled_shaft_thread() -> None:
    pytest.importorskip("bd_warehouse")
    base = {"id": "t", "type": "thread", "base": "shaft", "face": SHAFT_FACE, "designation": "M4"}
    nominal, _ = build([*shaft(2), {**base, "modeled": True}])
    thin, _ = build([*shaft(2), {**base, "modeled": True, "clearance": 0.3}])
    assert thin.is_valid
    assert thin.volume < nominal.volume - 1


def test_a_partial_chamfered_shaft_thread_leaves_the_shank_whole() -> None:
    pytest.importorskip("bd_warehouse")
    thread = {
        "id": "t",
        "type": "thread",
        "base": "shaft",
        "face": SHAFT_FACE,
        "designation": "M4",
        "modeled": True,
        "length": 4,
        "ends": "chamfer",
    }
    part, _ = build([*shaft(2), thread])
    assert part.is_valid
    assert len(part.solids()) == 1
    shank = math.pi * 2**2 * 4
    assert shank < part.volume < math.pi * 2**2 * 8
