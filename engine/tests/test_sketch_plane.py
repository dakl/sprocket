"""A sketch's plane given as a face of an earlier feature, or as an offset origin plane."""

import json
from pathlib import Path

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.model import JSONObject
from sprocket_engine.replay import replay
from sprocket_engine.viewer_files import features_summary

BOX: list[JSONObject] = [
    {"id": "s", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "b", "type": "extrude", "profile": "s", "distance": "=height"},
]


def face(axis, end):
    return {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": axis, "end": end}],
        "expected_count": 1,
    }


def pad_on(plane, center=(0, 0), distance=5, **extrude):
    return [
        {
            "id": "p",
            "type": "sketch",
            "plane": plane,
            "rect": {"width": 4, "height": 4, "center": list(center)},
        },
        {"id": "e", "type": "extrude", "profile": "p", "distance": distance, **extrude},
    ]


def build(features, height=10):
    document = Document(BOX + features, parameters={"height": height})
    return replay(document.for_replay())


def pad(features):
    """The pad alone: without a base it is a body of its own beside the box."""
    document = Document(BOX + features, parameters={"height": 10})
    part, ctx = replay(document.for_replay(), return_context=True)
    assert [body.id for body in ctx.bodies] == ["b", "e"]
    return ctx.results["e"]


def test_a_sketch_on_the_top_face_follows_the_face_when_the_box_grows() -> None:
    features = pad_on({"feature": "b", "face": face("Z", "max")}, base="b", mode="join")
    for height in (10, 14):
        part = build(features, height)
        assert pytest.approx(height + 5) == part.bounding_box().max.Z
        assert part.volume == pytest.approx(400 * height + 80)


def test_an_offset_origin_plane() -> None:
    part = pad(pad_on({"name": "XY", "offset": 30}))
    box = part.bounding_box()
    assert pytest.approx((30, 35)) == (box.min.Z, box.max.Z)


def test_on_a_side_face_v_is_world_up_and_coordinates_stay_world_aligned() -> None:
    part = pad(pad_on({"feature": "b", "face": face("X", "max")}, center=(2, 3)))
    centre = part.center()
    assert pytest.approx((12.5, 2, 3)) == (centre.X, centre.Y, centre.Z)


def test_on_the_bottom_face_u_is_x_and_v_is_minus_y() -> None:
    part = pad(pad_on({"feature": "b", "face": face("Z", "min")}, center=(2, 3)))
    centre = part.center()
    assert pytest.approx((2, -3, -2.5)) == (centre.X, centre.Y, centre.Z)


def test_a_face_plane_must_be_one_planar_face() -> None:
    every_face: JSONObject = {"select": [{"op": "faces"}]}
    with pytest.raises(Exception, match="expected exactly 1"):
        _ = build(pad_on({"feature": "b", "face": every_face}))


def test_the_viewer_gets_an_explicit_plane_for_a_face_sketch(tmp_path: Path) -> None:
    path = tmp_path / "part.sprocket"
    _ = path.write_text(
        json.dumps(
            {
                "parameters": {"height": 10},
                "features": BOX + pad_on({"feature": "b", "face": face("Z", "max")}),
            }
        )
    )
    document = Document.load(path)
    _, ctx = replay(document.for_replay(), return_context=True)
    entry = next(
        item for item in features_summary(document, None, ctx) if item["id"] == "p"
    )
    assert entry["plane"] == {
        "origin": [0, 0, 10],
        "normal": [0, 0, 1],
        "x_dir": [1, 0, 0],
    }
    assert entry["feature"]["plane"]["feature"] == "b"


def test_the_face_owner_cannot_be_deleted_while_a_sketch_sits_on_it() -> None:
    document = Document(
        BOX + pad_on({"feature": "b", "face": face("Z", "max")}),
        parameters={"height": 10},
    )
    with pytest.raises(DocumentError, match="still reference it"):
        _ = document.apply_edits([{"op": "delete", "id": "b"}], None, "test")


def test_a_picked_face_becomes_a_plane_that_follows_it(tmp_path: Path) -> None:
    """The viewer's flow: pick a face, ask the daemon for a plane reference,
    sketch on it. A taller box carries the sketch up with its top face.
    """
    from conftest import running

    from sprocket_engine.rpc import Client

    with (
        running({"features": BOX, "parameters": {"height": 10}}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        faces = client.call("describe_geometry")["faces"]
        top = next(item["handle"] for item in faces if item.get("normal") == [0, 0, 1])
        found = client.call("face_sketch_plane", handle=top)
        assert found["plane"]["feature"] == "b"
        assert found["resolved"]["origin"][2] == pytest.approx(10)
        sketch: JSONObject = {
            "id": "p",
            "type": "sketch",
            "plane": found["plane"],
            "rect": {"width": 4, "height": 4},
        }
        pad: JSONObject = {
            "id": "e",
            "type": "extrude",
            "profile": "p",
            "base": "b",
            "distance": 5,
        }
        _ = client.call(
            "apply_edit",
            edits=[
                {"op": "insert", "feature": sketch},
                {"op": "insert", "feature": pad},
            ],
        )
        assert client.call("measure")["bounding_box"]["max"][2] == pytest.approx(15)
        _ = client.call("set_parameter", name="height", value=30)
        assert client.call("measure")["bounding_box"]["max"][2] == pytest.approx(35)
        # A side face works too, and its plane is the face, wherever it is.
        faces = client.call("describe_geometry")["faces"]
        side = next(
            item["handle"]
            for item in faces
            if item.get("normal") == [1, 0, 0] and item["centre"][0] == 10
        )
        side_plane = client.call("face_sketch_plane", handle=side)["resolved"]
        assert side_plane["normal"] == pytest.approx([1, 0, 0])
        assert side_plane["origin"][0] == pytest.approx(10)
        with pytest.raises(Exception, match="face handle"):
            _ = client.call("face_sketch_plane", handle="e1")


def test_a_face_of_the_second_body_names_that_body(tmp_path: Path) -> None:
    from conftest import running

    from sprocket_engine.rpc import Client

    second: list[JSONObject] = [
        {
            "id": "s2",
            "type": "sketch",
            "plane": {"name": "XY", "offset": 50},
            "rect": {"width": 6, "height": 6},
        },
        {"id": "c", "type": "extrude", "profile": "s2", "distance": 3},
    ]
    with (
        running(
            {"features": BOX + second, "parameters": {"height": 10}}, tmp_path
        ) as daemon,
        Client(daemon.socket_path) as client,
    ):
        faces = client.call("describe_geometry")["faces"]
        top = max(
            (item for item in faces if item.get("normal") == [0, 0, 1]),
            key=lambda item: item["centre"][2],
        )
        found = client.call("face_sketch_plane", handle=top["handle"])
        assert found["plane"]["feature"] == "c"
        assert found["resolved"]["origin"][2] == pytest.approx(53)
