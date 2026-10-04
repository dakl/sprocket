"""Several bodies in one document: see SPEC.md, "M5, bodies"."""

import json
import math
import struct

import numpy as np
import pytest
from build123d import Mesher, import_step

from conftest import running
from sprocket_engine.cli import _build_document
from sprocket_engine.document import Document, DocumentError
from sprocket_engine.export import write_3mf, write_step, write_stl_dir
from sprocket_engine.mesh import NO_COLOUR, export_mesh
from sprocket_engine.replay import replay, with_settings
from sprocket_engine.rpc import Client

CASE = [
    {"id": "cs", "type": "sketch", "plane": "XY", "rect": {"width": 20, "height": 20}},
    {"id": "case", "type": "extrude", "profile": "cs", "distance": 10},
    {"id": "ls", "type": "sketch", "plane": "XY", "circle": {"radius": 3, "center": [30, 0]}},
    {"id": "lid", "type": "extrude", "profile": "ls", "distance": 2},
]
SETTINGS = {"case": {"name": "Case", "color": "#ff0000"}, "lid": {"name": "Lid", "visible": False}}


def bodies_of(features, settings=None):
    part, ctx = replay({"features": features}, return_context=True)
    return part, with_settings(ctx.bodies, settings or {})


def test_two_unjoined_extrudes_are_two_bodies_in_one_compound():
    part, bodies = bodies_of(CASE)
    assert [body["id"] for body in bodies] == ["case", "lid"]
    assert part.volume == pytest.approx(4000 + math.pi * 9 * 2)
    # Face order runs body by body, which the mesh and picking rely on.
    expected = bodies[0]["shape"].faces() + bodies[1]["shape"].faces()
    assert all(a.is_same(b) for a, b in zip(part.faces(), expected, strict=True))


def test_a_boolean_ends_its_tools_body():
    joined = CASE + [{"id": "u", "type": "boolean", "operation": "union", "base": "case", "tool": "lid"}]
    _, bodies = bodies_of(joined)
    assert [(body["id"], body["tip"]) for body in bodies] == [("case", "u")]


def test_a_feature_on_an_older_chain_member_replaces_the_body_as_before():
    fillet = {"id": "r", "type": "fillet", "base": "case", "radius": 1, "edges": {"select": [{"op": "edges"}, {"op": "filter_by", "axis": "Z"}]}}
    chamfer = {"id": "c", "type": "chamfer", "base": "case", "length": 1, "edges": {"select": [{"op": "edges"}, {"op": "filter_by", "axis": "Z"}]}}
    _, bodies = bodies_of(CASE[:2] + [fillet, chamfer])
    assert [(body["id"], body["tip"]) for body in bodies] == [("case", "c")]


def read_mesh(path):
    raw = path.read_bytes()
    (length,) = struct.unpack("<I", raw[:4])
    header = json.loads(raw[4:4 + length].rstrip(b"\0"))
    entry = header["buffers"].get("face_colors")
    colours = None
    if entry:
        start = 4 + length + entry["offset"]
        colours = np.frombuffer(raw[start:start + 4 * entry["length"]], dtype="<u4")
    return header, colours


def test_the_mesh_lists_bodies_in_face_order_with_colours(tmp_path):
    part, bodies = bodies_of(CASE, SETTINGS)
    lid_top = {"color": "#00ff00", "select": {"select": [{"op": "faces"}, {"op": "within", "box": [27, -3, 1.9, 33, 3, 2.1]}]}}
    header, colours = read_mesh_after(export_mesh, part, bodies, [lid_top], tmp_path)
    assert [(body["name"], body["faces"], body["visible"]) for body in header["bodies"]] == [("Case", 6, True), ("Lid", 3, False)]
    assert colours[:6].tolist() == [0xFF0000] * 6
    # The appearance indexes the part's faces, so it must land on the lid's top.
    top = next(index for index, face in enumerate(part.faces()) if face.center().Z == pytest.approx(2) and face.center().X == pytest.approx(30))
    assert colours[top] == 0x00FF00
    assert sorted(colours[6:].tolist()) == [0x00FF00, NO_COLOUR, NO_COLOUR]


def read_mesh_after(export, part, bodies, appearances, tmp_path):
    path = tmp_path / "part.mesh"
    export(part, path, appearances, bodies)
    return read_mesh(path)


def test_step_carries_body_names_and_colours(tmp_path):
    part, bodies = bodies_of(CASE, SETTINGS)
    write_step(part, tmp_path / "part.step", bodies)
    children = import_step(tmp_path / "part.step").children
    assert [child.label for child in children] == ["Case", "Lid"]
    assert tuple(children[0].color)[:3] == pytest.approx((1, 0, 0))


def test_a_plain_one_body_step_is_not_relabelled(tmp_path):
    part, bodies = bodies_of(CASE[:2])
    write_step(part, tmp_path / "with.step", bodies)
    write_step(part, tmp_path / "without.step")
    strip = lambda path: [line for line in path.read_text().splitlines() if not line.startswith("FILE_NAME")]
    assert strip(tmp_path / "with.step") == strip(tmp_path / "without.step")


def test_stl_per_body_and_3mf(tmp_path):
    _, bodies = bodies_of(CASE, SETTINGS)
    written = write_stl_dir(bodies, tmp_path)
    assert sorted(path.name for path in written) == ["Case.stl", "Lid.stl"]
    write_3mf(bodies, tmp_path / "part.3mf")
    mesher = Mesher()
    mesher.read(str(tmp_path / "part.3mf"))
    assert mesher.mesh_count == 2


def test_set_body_validates_and_undo_restores(tmp_path):
    document = Document(CASE)
    before = document.snapshot()
    document.apply_edits([{"op": "set_body", "id": "lid", "color": "#00ff00", "name": "Lid"}], None, "test")
    assert document.bodies == {"lid": {"color": "#00ff00", "name": "Lid"}}
    document.apply_edits([{"op": "set_body", "id": "lid", "color": None}], None, "test")
    assert document.bodies == {"lid": {"name": "Lid"}}
    with pytest.raises(DocumentError, match="#rrggbb"):
        document.apply_edits([{"op": "set_body", "id": "lid", "color": "green"}], None, "test")
    with pytest.raises(DocumentError, match="no feature"):
        document.apply_edits([{"op": "set_body", "id": "nope", "name": "x"}], None, "test")
    document.restore(before, "test")
    assert document.bodies == {}
    path = tmp_path / "d.sprocket"
    document.apply_edits([{"op": "set_body", "id": "case", "visible": False}], None, "test")
    document.save(path)
    assert Document.load(path).bodies == {"case": {"visible": False}}


def test_cli_build_reports_bodies_and_writes_3mf(tmp_path):
    path = tmp_path / "two.sprocket"
    path.write_text(json.dumps({"features": CASE, "bodies": SETTINGS}))
    ok, output, _ = _build_document(path, None, None, tmp_path / "cache", three_mf=tmp_path / "two.3mf")
    assert ok, output
    assert "2 bodies: Case, Lid" in output
    assert (tmp_path / "two.3mf").stat().st_size > 0


def test_daemon_get_bodies_and_set_body(tmp_path):
    with running({"features": CASE}, tmp_path) as daemon, Client(daemon.socket_path) as client:
        listed = client.call("get_bodies")["bodies"]
        assert [(body["id"], body["faces"]) for body in listed] == [("case", 6), ("lid", 3)]
        client.call("set_body", id="lid", color="#123456")
        assert client.call("get_bodies")["bodies"][1]["color"] == "#123456"
