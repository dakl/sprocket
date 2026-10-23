"""Several bodies in one document: see SPEC.md, "M5, bodies"."""

import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest
from build123d import Mesher, import_step
from conftest import running

from sprocket_engine.cli import build_document
from sprocket_engine.document import Document, DocumentError
from sprocket_engine.export import write_3mf, write_step, write_stl_dir
from sprocket_engine.mesh import NO_COLOUR, export_mesh
from sprocket_engine.model import JSONObject
from sprocket_engine.replay import replay, with_settings
from sprocket_engine.rpc import Client

CASE: list[JSONObject] = [
    {"id": "cs", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "case", "type": "extrude", "profile": "cs", "distance": 10},
    {
        "id": "ls",
        "type": "sketch",
        "plane": "XY",
        "circle": {"radius": 3, "center": [30, 0]},
    },
    {"id": "lid", "type": "extrude", "profile": "ls", "distance": 2},
]
SETTINGS: JSONObject = {
    "case": {"name": "Case", "color": "#ff0000"},
    "lid": {"name": "Lid", "visible": False},
}


def bodies_of(features, settings=None):
    part, ctx = replay({"features": features}, return_context=True)
    return part, with_settings(ctx.bodies, settings or {})


def test_two_unjoined_extrudes_are_two_bodies_in_one_compound() -> None:
    part, bodies = bodies_of(CASE)
    assert [body.id for body in bodies] == ["case", "lid"]
    assert part.volume == pytest.approx(4000 + math.pi * 9 * 2)
    # Face order runs body by body, which the mesh and picking rely on.
    expected = bodies[0].shape.faces() + bodies[1].shape.faces()
    assert all(a.is_same(b) for a, b in zip(part.faces(), expected, strict=True))


def test_a_boolean_ends_its_tools_body() -> None:
    joined = CASE + [
        {
            "id": "u",
            "type": "boolean",
            "operation": "union",
            "base": "case",
            "tool": "lid",
        }
    ]
    _, bodies = bodies_of(joined)
    assert [(body.id, body.tip) for body in bodies] == [("case", "u")]


def test_a_feature_on_an_older_chain_member_replaces_the_body_as_before() -> None:
    fillet: JSONObject = {
        "id": "r",
        "type": "fillet",
        "base": "case",
        "radius": 1,
        "edges": {"select": [{"op": "edges"}, {"op": "filter_by", "axis": "Z"}]},
    }
    chamfer: JSONObject = {
        "id": "c",
        "type": "chamfer",
        "base": "case",
        "length": 1,
        "edges": {"select": [{"op": "edges"}, {"op": "filter_by", "axis": "Z"}]},
    }
    _, bodies = bodies_of(CASE[:2] + [fillet, chamfer])
    assert [(body.id, body.tip) for body in bodies] == [("case", "c")]


def read_mesh(path):
    raw = path.read_bytes()
    (length,) = struct.unpack("<I", raw[:4])
    header = json.loads(raw[4 : 4 + length].rstrip(b"\0"))
    entry = header["buffers"].get("face_colors")
    colours = None
    if entry:
        start = 4 + length + entry["offset"]
        colours = np.frombuffer(raw[start : start + 4 * entry["length"]], dtype="<u4")
    return header, colours


def test_the_mesh_lists_bodies_in_face_order_with_colours(tmp_path: Path) -> None:
    part, bodies = bodies_of(CASE, SETTINGS)
    lid_top: JSONObject = {
        "color": "#00ff00",
        "select": {
            "select": [
                {"op": "faces"},
                {"op": "within", "box": [27, -3, 1.9, 33, 3, 2.1]},
            ]
        },
    }
    header, colours = read_mesh_after(export_mesh, part, bodies, [lid_top], tmp_path)
    assert [
        (body["name"], body["faces"], body["visible"]) for body in header["bodies"]
    ] == [("Case", 6, True), ("Lid", 3, False)]
    assert [body["tip"] for body in header["bodies"]] == ["case", "lid"]
    assert colours[:6].tolist() == [0xFF0000] * 6
    # The appearance indexes the part's faces, so it must land on the lid's top.
    top = next(
        index
        for index, face in enumerate(part.faces())
        if pytest.approx(2) == face.center().Z and pytest.approx(30) == face.center().X
    )
    assert colours[top] == 0x00FF00
    assert sorted(colours[6:].tolist()) == [0x00FF00, NO_COLOUR, NO_COLOUR]


def read_mesh_after(export, part, bodies, appearances, tmp_path: Path):
    path = tmp_path / "part.mesh"
    export(part, path, appearances, bodies)
    return read_mesh(path)


def test_step_carries_body_names_and_colours(tmp_path: Path) -> None:
    part, bodies = bodies_of(CASE, SETTINGS)
    write_step(part, tmp_path / "part.step", bodies)
    children = import_step(tmp_path / "part.step").children
    assert [child.label for child in children] == ["Case", "Lid"]
    assert tuple(children[0].color)[:3] == pytest.approx((1, 0, 0))


def test_a_plain_one_body_step_is_not_relabelled(tmp_path: Path) -> None:
    part, bodies = bodies_of(CASE[:2])
    write_step(part, tmp_path / "with.step", bodies)
    write_step(part, tmp_path / "without.step")
    strip = lambda path: [
        line
        for line in path.read_text().splitlines()
        if not line.startswith("FILE_NAME")
    ]
    assert strip(tmp_path / "with.step") == strip(tmp_path / "without.step")


def test_stl_per_body_and_3mf(tmp_path: Path) -> None:
    _, bodies = bodies_of(CASE, SETTINGS)
    written = write_stl_dir(bodies, tmp_path)
    assert sorted(path.name for path in written) == ["Case.stl", "Lid.stl"]
    write_3mf(bodies, tmp_path / "part.3mf")
    mesher = Mesher()
    _ = mesher.read(str(tmp_path / "part.3mf"))
    assert mesher.mesh_count == 2


def test_set_body_validates_and_undo_restores(tmp_path: Path) -> None:
    document = Document(CASE)
    before = document.snapshot()
    _ = document.apply_edits(
        [{"op": "set_body", "id": "lid", "color": "#00ff00", "name": "Lid"}],
        None,
        "test",
    )
    assert document.bodies == {"lid": {"color": "#00ff00", "name": "Lid"}}
    _ = document.apply_edits(
        [{"op": "set_body", "id": "lid", "color": None}], None, "test"
    )
    assert document.bodies == {"lid": {"name": "Lid"}}
    with pytest.raises(DocumentError, match="#rrggbb"):
        _ = document.apply_edits(
            [{"op": "set_body", "id": "lid", "color": "green"}], None, "test"
        )
    with pytest.raises(DocumentError, match="no feature"):
        _ = document.apply_edits(
            [{"op": "set_body", "id": "nope", "name": "x"}], None, "test"
        )
    _ = document.restore(before, "test")
    assert document.bodies == {}
    path = tmp_path / "d.sprocket"
    _ = document.set_visibility("case", False)
    _ = document.save(path)
    assert Document.load(path).bodies == {"case": {"visible": False}}
    with pytest.raises(DocumentError, match="use set_visibility"):
        _ = document.apply_edits(
            [{"op": "set_body", "id": "case", "visible": True}], None, "test"
        )


def test_hiding_a_body_is_undoable_view_state_with_no_log_entry(tmp_path: Path) -> None:
    with (
        running({"features": CASE}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        log_length = len(daemon.session.document.log)
        timeline = client.call("get_timeline")
        _ = client.call("set_visibility", id="lid", visible=False)
        assert client.call("get_bodies")["bodies"][1]["visible"] is False
        _ = client.call("undo")
        assert client.call("get_bodies")["bodies"][1]["visible"] is True
        _ = client.call("redo")
        assert client.call("get_bodies")["bodies"][1]["visible"] is False
        # set_body with visible goes the same way.
        _ = client.call("set_body", id="lid", visible=True)
        assert client.call("get_bodies")["bodies"][1]["visible"] is True
        assert len(daemon.session.document.log) == log_length
        assert client.call("get_timeline")["timeline"] == timeline["timeline"]


def test_cli_build_reports_bodies_and_writes_3mf(tmp_path: Path) -> None:
    path = tmp_path / "two.sprocket"
    _ = path.write_text(json.dumps({"features": CASE, "bodies": SETTINGS}))
    ok, output, _ = build_document(
        path, None, None, tmp_path / "cache", three_mf=tmp_path / "two.3mf"
    )
    assert ok, output
    assert "2 bodies: Case, Lid" in output
    assert (tmp_path / "two.3mf").stat().st_size > 0


def test_daemon_get_bodies_and_set_body(tmp_path: Path) -> None:
    with (
        running({"features": CASE}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        listed = client.call("get_bodies")["bodies"]
        assert [(body["id"], body["faces"]) for body in listed] == [
            ("case", 6),
            ("lid", 3),
        ]
        _ = client.call("set_body", id="lid", color="#123456")
        assert client.call("get_bodies")["bodies"][1]["color"] == "#123456"


def test_an_agent_reads_and_sets_bodies_and_resolves_a_plane_through_mcp(
    tmp_path: Path,
) -> None:
    from test_mcp import drive

    top: JSONObject = {
        "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}],
        "expected_count": 1,
    }

    async def work(session):
        named = await session.call_tool(
            "set_body", {"id": "lid", "name": "Lid", "color": "#00ff00"}
        )
        listed = await session.call_tool("get_bodies", {})
        plane = await session.call_tool(
            "resolve_plane", {"plane": {"feature": "case", "face": top}}
        )
        return named, listed, plane

    with running({"features": CASE}, tmp_path) as daemon:
        named, listed, plane = drive(daemon, work)
    assert not named.is_error, named.content
    assert [body["name"] for body in json.loads(listed.content[0].text)["bodies"]] == [
        "case",
        "Lid",
    ]
    assert json.loads(plane.content[0].text)["plane"]["origin"] == [0, 0, 10]


def test_set_appearances_replaces_face_colours_and_undo_brings_them_back(
    tmp_path: Path,
) -> None:
    top: JSONObject = {
        "color": "#00ff00",
        "select": {
            "select": [{"op": "faces"}, {"op": "at_end", "axis": "Z", "end": "max"}]
        },
    }
    with (
        running({"features": CASE[:2]}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        _ = client.call("set_appearances", appearances=[top])
        assert client.call("get_document")["appearances"] == [top]
        with pytest.raises(Exception, match="appearances"):
            _ = client.call(
                "set_appearances", appearances=[{"color": "green", "select": {}}]
            )
        _ = client.call("undo")
        assert client.call("get_document")["appearances"] == []


def test_get_face_appearance_names_the_entries_on_a_face_and_a_box_that_picks_it_alone(
    tmp_path,
):
    everything: JSONObject = {
        "color": "#00ff00",
        "select": {"select": [{"op": "faces"}]},
    }
    with (
        running({"features": CASE[:2]}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        faces = client.call("describe_geometry")["faces"]
        top = next(item["handle"] for item in faces if item.get("normal") == [0, 0, 1])
        _ = client.call("set_appearances", appearances=[everything])
        info = client.call("get_face_appearance", handle=top)
        assert info["entries"] == [
            {"index": 0, "color": "#00ff00", "material": None, "faces": len(faces)}
        ]
        # Painting with the box colours that face and no other.
        _ = client.call(
            "set_appearances",
            appearances=[everything, {"color": "#ff0000", "select": info["selector"]}],
        )
        again = client.call("get_face_appearance", handle=top)
        assert again["entries"][-1] == {
            "index": 1,
            "color": "#ff0000",
            "material": None,
            "faces": 1,
        }
        with pytest.raises(Exception, match="face handle"):
            _ = client.call("get_face_appearance", handle="e0")
