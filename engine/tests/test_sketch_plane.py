"""A sketch's plane given as a face of an earlier feature, or as an offset origin plane."""

import json

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.replay import replay
from sprocket_engine.viewer_files import features_summary

BOX = [
    {"id": "s", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "b", "type": "extrude", "profile": "s", "distance": "=height"},
]


def face(axis, end):
    return {"select": [{"op": "faces"}, {"op": "at_end", "axis": axis, "end": end}], "expected_count": 1}


def pad_on(plane, center=(0, 0), distance=5, **extrude):
    return [
        {"id": "p", "type": "sketch", "plane": plane, "rect": {"width": 4, "height": 4, "center": list(center)}},
        {"id": "e", "type": "extrude", "profile": "p", "distance": distance, **extrude},
    ]


def build(features, height=10):
    document = Document(BOX + features, parameters={"height": height})
    return replay(document.for_replay())


def test_a_sketch_on_the_top_face_follows_the_face_when_the_box_grows():
    features = pad_on({"feature": "b", "face": face("Z", "max")}, base="b", mode="join")
    for height in (10, 14):
        part = build(features, height)
        assert part.bounding_box().max.Z == pytest.approx(height + 5)
        assert part.volume == pytest.approx(400 * height + 80)


def test_an_offset_origin_plane():
    part = build(pad_on({"name": "XY", "offset": 30}))
    box = part.bounding_box()
    assert (box.min.Z, box.max.Z) == pytest.approx((30, 35))


def test_on_a_side_face_v_is_world_up_and_coordinates_stay_world_aligned():
    part = build(pad_on({"feature": "b", "face": face("X", "max")}, center=(2, 3)))
    centre = part.center()
    assert (centre.X, centre.Y, centre.Z) == pytest.approx((12.5, 2, 3))


def test_on_the_bottom_face_u_is_x_and_v_is_minus_y():
    part = build(pad_on({"feature": "b", "face": face("Z", "min")}, center=(2, 3)))
    centre = part.center()
    assert (centre.X, centre.Y, centre.Z) == pytest.approx((2, -3, -2.5))


def test_a_face_plane_must_be_one_planar_face():
    every_face = {"select": [{"op": "faces"}]}
    with pytest.raises(Exception, match="expected exactly 1"):
        build(pad_on({"feature": "b", "face": every_face}))


def test_the_viewer_gets_an_explicit_plane_for_a_face_sketch(tmp_path):
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps({"parameters": {"height": 10}, "features": BOX + pad_on({"feature": "b", "face": face("Z", "max")})}))
    document = Document.load(path)
    _, ctx = replay(document.for_replay(), return_context=True)
    entry = next(item for item in features_summary(document, None, ctx) if item["id"] == "p")
    assert entry["plane"] == {"origin": [0, 0, 10], "normal": [0, 0, 1], "x_dir": [1, 0, 0]}
    assert entry["feature"]["plane"]["feature"] == "b"


def test_the_face_owner_cannot_be_deleted_while_a_sketch_sits_on_it():
    document = Document(BOX + pad_on({"feature": "b", "face": face("Z", "max")}), parameters={"height": 10})
    with pytest.raises(DocumentError, match="still reference it"):
        document.apply_edits([{"op": "delete", "id": "b"}], None, "test")
