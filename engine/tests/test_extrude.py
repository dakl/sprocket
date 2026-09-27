import pytest

from sprocket_engine.replay import replay

SKETCH = {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 20, "center": [0, 0]}}


def test_negative_distance_flips_direction_instead_of_failing_validation():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [SKETCH, {"id": "f2", "type": "extrude", "profile": "f1", "distance": -10}],
    }
    part = replay(document)
    assert part.volume == pytest.approx(40 * 20 * 10, rel=1e-6)


# A 20x20x10 box, plus a 5x5 sketch on its top face at z=10. This is what the
# viewer writes when the user draws on a part face and extrudes the result.
BOX = [
    {"id": "f1", "type": "sketch", "plane": "XY", "polygon": [[0, 0], [20, 0], [20, 20], [0, 20]]},
    {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
]
FACE_PLANE = {"origin": [5, 5, 10], "normal": [0, 0, 1], "x_dir": [0, -1, 0]}
# The sketcher emits whichever winding the user drew, so both must behave the
# same. A clockwise polygon used to build a face pointing into the part, so a
# positive distance fused an invisible boss inside the solid.
FACE_SKETCH_CW = {"id": "f3", "type": "sketch", "plane": FACE_PLANE, "polygon": [[0, 0], [-5, 0], [-5, 5], [0, 5]]}
FACE_SKETCH_CCW = dict(FACE_SKETCH_CW, polygon=[[0, 0], [0, 5], [-5, 5], [-5, 0]])

BOX_VOLUME = 20 * 20 * 10
BOSS_VOLUME = 5 * 5 * 3


def _sketch_on_face(distance, sketch):
    return {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": BOX + [sketch, {"id": "f4", "type": "extrude", "profile": "f3", "base": "f2", "distance": distance}],
    }


@pytest.mark.parametrize("sketch", [FACE_SKETCH_CW, FACE_SKETCH_CCW], ids=["cw", "ccw"])
def test_positive_distance_fuses_a_boss_out_of_the_face(sketch):
    part = replay(_sketch_on_face(3, sketch))
    assert part.volume == pytest.approx(BOX_VOLUME + BOSS_VOLUME, rel=1e-6)
    assert part.bounding_box().max.Z == pytest.approx(13, abs=1e-6)


@pytest.mark.parametrize("sketch", [FACE_SKETCH_CW, FACE_SKETCH_CCW], ids=["cw", "ccw"])
def test_negative_distance_cuts_a_pocket_into_the_face(sketch):
    part = replay(_sketch_on_face(-3, sketch))
    assert part.volume == pytest.approx(BOX_VOLUME - BOSS_VOLUME, rel=1e-6)
    assert part.bounding_box().max.Z == pytest.approx(10, abs=1e-6)


def test_extrude_without_a_base_returns_the_new_solid_alone():
    document = {
        "schema_version": 1,
        "units": {"length": "mm"},
        "features": [FACE_SKETCH_CW, {"id": "f4", "type": "extrude", "profile": "f3", "distance": 3}],
    }
    part = replay(document)
    assert part.volume == pytest.approx(BOSS_VOLUME, rel=1e-6)
