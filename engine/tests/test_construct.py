"""Construct features: a plane, an axis and a point that later features name."""

import math

import pytest

from conftest import running
from sprocket_engine.document import Document, DocumentError
from sprocket_engine.replay import build_shape, replay
from sprocket_engine.rpc import Client

BOX = [
    {"id": "bs", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 30}},
    {"id": "box", "type": "extrude", "profile": "bs", "distance": 10},
]
PEG = [
    {
        "id": "ps",
        "type": "sketch",
        "plane": {"name": "XY", "offset": 30},
        "rect": {"width": 2, "height": 2, "center": [5, 0]},
    },
    {"id": "peg", "type": "extrude", "profile": "ps", "distance": 2},
]
YZ_AT_X30 = {"id": "wall", "type": "plane", "plane": "YZ", "offset": 30}


def build(features):
    return replay(Document(features).for_replay(), return_context=True)


def test_construct_features_make_no_body_and_a_document_of_them_is_empty():
    shape, failed, ctx = build_shape(
        [YZ_AT_X30, {"id": "p", "type": "point", "at": [1, 2, 3]}], 2, None
    )
    assert shape is None and failed is None
    shape, ctx = build(BOX + [YZ_AT_X30, {"id": "z", "type": "axis", "axis": "Z"}])
    assert [body["id"] for body in ctx.bodies] == ["box"]
    assert shape.volume == pytest.approx(12000)


def test_a_sketch_on_a_plane_feature_sits_on_it_and_follows_when_it_moves():
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": {"construct": "wall"},
        "rect": {"width": 4, "height": 4},
    }
    _, ctx = build(
        BOX
        + [
            YZ_AT_X30,
            sketch,
            {"id": "cut", "type": "extrude", "profile": "s", "distance": 3},
        ]
    )
    box = ctx.results["cut"].bounding_box()
    assert (box.min.X, box.max.X) == pytest.approx((30, 33))
    moved = {**YZ_AT_X30, "offset": 12}
    _, ctx = build(
        BOX
        + [
            moved,
            sketch,
            {"id": "cut", "type": "extrude", "profile": "s", "distance": 3},
        ]
    )
    assert ctx.results["cut"].bounding_box().min.X == pytest.approx(12)


def test_a_plane_feature_can_start_from_a_face_or_another_plane_feature():
    top = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
        "expected_count": 1,
    }
    on_top = {
        "id": "up",
        "type": "plane",
        "plane": {"feature": "box", "face": top},
        "offset": 5,
    }
    again = {"id": "up2", "type": "plane", "plane": {"construct": "up", "offset": 1}}
    sketch = {
        "id": "s",
        "type": "sketch",
        "plane": {"construct": "up2"},
        "rect": {"width": 4, "height": 4},
    }
    _, ctx = build(
        BOX
        + [
            on_top,
            again,
            sketch,
            {"id": "e", "type": "extrude", "profile": "s", "distance": 1},
        ]
    )
    box = ctx.results["e"].bounding_box()
    assert (box.min.Z, box.max.Z) == pytest.approx((16, 17))


def test_mirror_across_a_plane_feature():
    mirror = {
        "id": "m",
        "type": "mirror",
        "tool": "peg",
        "plane": {"construct": "wall"},
    }
    _, ctx = build(BOX + PEG + [YZ_AT_X30, mirror])
    box = ctx.results["m"].bounding_box()
    # The peg is at x 4..6, so its mirror about x = 30 is at 54..56.
    assert (box.min.X, box.max.X) == pytest.approx((4, 56))


def test_an_axis_follows_a_cylindrical_face_and_a_pattern_turns_about_it():
    hole_sketch = {
        "id": "hs",
        "type": "sketch",
        "plane": "XY",
        "circle": {"radius": 3, "center": [10, 0]},
    }
    post = {"id": "post", "type": "extrude", "profile": "hs", "distance": 8}
    pin = [
        {
            "id": "ns",
            "type": "sketch",
            "plane": "XY",
            "circle": {"radius": 1, "center": [20, 0]},
        },
        {"id": "nib", "type": "extrude", "profile": "ns", "distance": 8},
    ]
    features = [hole_sketch, post, *pin]
    # The post's side wall is the face nearest its seam, where OCCT puts a cylinder's centre.
    axis = {
        "id": "post_axis",
        "type": "axis",
        "axis": {
            "base": "post",
            "face": {
                "select": [{"op": "faces"}, {"op": "nearest_to", "point": [7, 0, 4]}]
            },
        },
    }
    ring = {
        "id": "ring",
        "type": "pattern",
        "tool": "nib",
        "circular": {"axis": {"construct": "post_axis"}, "count": 4},
    }
    _, ctx = build(features + [axis, ring])
    assert ctx.results["post_axis"] is not None
    result = ctx.results["ring"]
    assert len(result.solids()) == 4
    assert result.volume == pytest.approx(4 * math.pi * 1 * 8)
    # The ring is about the post's axis at x = 10, so the pins sit 10 mm from it: x = 20, 10, 0 and 10.
    xs = sorted(round(solid.center().X, 3) for solid in result.solids())
    assert xs == pytest.approx([0, 10, 10, 20])


def test_a_construct_axis_is_a_pattern_direction():
    row = {
        "id": "row",
        "type": "pattern",
        "tool": "peg",
        "linear": {"direction": {"construct": "x2"}, "count": 3, "spacing": 10},
    }
    axis = {
        "id": "x2",
        "type": "axis",
        "axis": {"origin": [0, 0, 0], "direction": [1, 0, 0]},
    }
    _, ctx = build(BOX + PEG + [axis, row])
    box = ctx.results["row"].bounding_box()
    assert (box.min.X, box.max.X) == pytest.approx((4, 26))
    # A named axis works as a direction too.
    row_y = {
        "id": "row",
        "type": "pattern",
        "tool": "peg",
        "linear": {"direction": "Y", "count": 2, "spacing": 5},
    }
    _, ctx = build(BOX + PEG + [row_y])
    assert ctx.results["row"].bounding_box().max.Y == pytest.approx(6)


def test_an_axis_that_does_not_resolve_fails_that_feature_by_name():
    bad = {
        "id": "a",
        "type": "axis",
        "axis": {
            "base": "box",
            "edge": {
                "select": [
                    {"op": "edges"},
                    {"op": "filter_by", "axis": "Z"},
                    {"op": "slice", "start": 0, "stop": 2},
                ]
            },
        },
    }
    with pytest.raises(Exception) as caught:
        build(BOX + [bad])
    assert caught.value.feature_id == "a"
    curved = {
        "id": "c",
        "type": "axis",
        "axis": {"base": "box", "face": {"select": [{"op": "faces"}, {"op": "first"}]}},
    }
    with pytest.raises(Exception, match="cylindrical"):
        build(BOX + [curved])


def test_a_construct_feature_a_later_one_names_cannot_be_deleted():
    mirror = {
        "id": "m",
        "type": "mirror",
        "tool": "peg",
        "plane": {"construct": "wall"},
    }
    document = Document(BOX + PEG + [YZ_AT_X30, mirror])
    with pytest.raises(DocumentError):
        document.apply_edits([{"op": "delete", "id": "wall"}], None, "test")
    spin = {
        "id": "ring",
        "type": "pattern",
        "tool": "peg",
        "circular": {
            "axis": {"base": "box", "face": {"select": [{"op": "faces"}]}},
            "count": 2,
        },
    }
    document = Document(BOX + PEG + [spin])
    with pytest.raises(DocumentError):
        document.apply_edits([{"op": "delete", "id": "box"}], None, "test")


def test_the_viewer_files_describe_each_construct_feature(tmp_path):
    from sprocket_engine.viewer_files import features_summary

    features = BOX + [
        YZ_AT_X30,
        {
            "id": "z",
            "type": "axis",
            "axis": {"origin": [1, 2, 3], "direction": [0, 0, 1]},
        },
        {"id": "p", "type": "point", "at": [1, 2, 3]},
    ]
    document = Document(features)
    _, failed, ctx = build_shape(document.for_replay()["features"], 5, None)
    entries = {entry["id"]: entry for entry in features_summary(document, failed, ctx)}
    assert entries["wall"]["construct"] == {
        "kind": "plane",
        "origin": [30, 0, 0],
        "normal": [1, 0, 0],
        "x_dir": pytest.approx([0, 1, 0], abs=1e-9),
    }
    assert entries["z"]["construct"] == {
        "kind": "axis",
        "origin": [1, 2, 3],
        "direction": [0, 0, 1],
    }
    assert entries["p"]["construct"] == {"kind": "point", "at": [1, 2, 3]}


def test_preview_says_where_a_construct_feature_lands_even_with_no_part(tmp_path):
    with running({"features": [], "rollback_index": 0}, tmp_path) as daemon:
        with Client(daemon.socket_path) as client:
            result = client.call(
                "preview_feature",
                feature={"id": "w", "type": "plane", "plane": "XY", "offset": 7},
            )
            assert result["would_build"] and result["construct"]["origin"] == [0, 0, 7]
            with pytest.raises(Exception, match="nope"):
                client.call(
                    "preview_feature",
                    feature={"id": "a", "type": "axis", "axis": {"construct": "nope"}},
                )
    with running({"features": BOX, "rollback_index": 2}, tmp_path) as daemon:
        with Client(daemon.socket_path) as client:
            flat = {
                "id": "a",
                "type": "axis",
                "axis": {
                    "base": "box",
                    "face": {"select": [{"op": "faces"}, {"op": "first"}]},
                },
            }
            broken = client.call("preview_feature", feature=flat)
            assert not broken["would_build"] and "cylindrical" in broken["reason"]


def test_pick_axis_names_a_cylindrical_face_and_a_circular_edge_by_the_body_they_are_on(
    tmp_path,
):
    post = [
        {
            "id": "hs",
            "type": "sketch",
            "plane": "XY",
            "circle": {"radius": 3, "center": [10, 0]},
        },
        {"id": "post", "type": "extrude", "profile": "hs", "distance": 8},
    ]
    with running({"features": BOX + post, "rollback_index": 4}, tmp_path) as daemon:
        with Client(daemon.socket_path) as client:
            geometry = client.call("describe_geometry")
            round_face = next(
                face["handle"]
                for face in geometry["faces"]
                if face["kind"] == "CYLINDER"
            )
            axis = client.call("pick_axis", handle=round_face)
            assert axis["axis"]["base"] == "post" and "face" in axis["axis"]
            assert axis["resolved"]["origin"][:2] == pytest.approx([10, 0]) and abs(
                axis["resolved"]["direction"][2]
            ) == pytest.approx(1)
            circle = next(
                edge["handle"] for edge in geometry["edges"] if edge["kind"] == "CIRCLE"
            )
            around = client.call("pick_axis", handle=circle)
            assert around["axis"]["base"] == "post" and "edge" in around["axis"]
            assert around["resolved"]["origin"][:2] == pytest.approx([10, 0])
            line = next(
                edge["handle"] for edge in geometry["edges"] if edge["kind"] == "LINE"
            )
            assert (
                client.call("pick_axis", handle=line)["resolved"]["direction"]
                is not None
            )
            flat = next(
                face["handle"] for face in geometry["faces"] if face["kind"] == "PLANE"
            )
            with pytest.raises(Exception, match="no axis"):
                client.call("pick_axis", handle=flat)
