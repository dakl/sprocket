"""Surface finishes: a preset name in the document, (roughness, metalness)
per face in the mesh. See SPEC.md, "M5, material presets".
"""

import json
import struct

import numpy as np
import pytest
from conftest import running
from test_bodies import CASE

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.materials import PRESETS
from sprocket_engine.mesh import NO_MATERIAL, export_mesh
from sprocket_engine.replay import replay, with_settings
from sprocket_engine.rpc import Client


def read(path):
    raw = path.read_bytes()
    (length,) = struct.unpack("<I", raw[:4])
    header = json.loads(raw[4 : 4 + length].rstrip(b"\0"))
    entry = header["buffers"].get("face_materials")
    values = None
    if entry:
        start = 4 + length + entry["offset"]
        values = np.frombuffer(
            raw[start : start + 4 * entry["length"]], dtype="<f4"
        ).reshape(-1, 2)
    return header, values


def export(tmp_path, settings, appearances=None):
    part, ctx = replay({"features": CASE}, return_context=True)
    path = tmp_path / "part.mesh"
    export_mesh(part, path, appearances, with_settings(ctx.bodies, settings))
    return part, path


def test_no_finish_writes_version_1_with_no_material_buffer(tmp_path):
    _, path = export(tmp_path, {"case": {"color": "#ff0000"}})
    header, values = read(path)
    assert header["version"] == 1 and values is None
    assert all("material" not in body for body in header["bodies"])


def test_a_body_finish_and_a_face_finish_reach_the_mesh_as_numbers(tmp_path):
    lid_top = {
        "color": "#00ff00",
        "material": "glossy",
        "select": {
            "select": [
                {"op": "faces"},
                {"op": "within", "box": [27, -3, 1.9, 33, 3, 2.1]},
            ]
        },
    }
    part, path = export(tmp_path, {"case": {"material": "bare_metal"}}, [lid_top])
    header, values = read(path)
    assert header["version"] == 2
    assert header["bodies"][0]["material"] == "bare_metal"
    assert np.allclose(values[:6], PRESETS["bare_metal"])
    top = next(
        index
        for index, face in enumerate(part.faces())
        if pytest.approx(2) == face.center().Z and pytest.approx(30) == face.center().X
    )
    assert values[top].tolist() == pytest.approx(PRESETS["glossy"])
    others = [index for index in range(6, 9) if index != top]
    assert np.all(values[others] == NO_MATERIAL)


def test_unknown_presets_are_refused(tmp_path):
    with pytest.raises(DocumentError, match="unknown material"):
        Document(CASE).apply_edits(
            [{"op": "set_body", "id": "case", "material": "chrome"}], None, "test"
        )
    path = tmp_path / "bad.sprocket"
    path.write_text(
        json.dumps(
            {
                "features": CASE,
                "appearances": [
                    {
                        "color": "#000000",
                        "material": "x",
                        "select": {"select": [{"op": "faces"}]},
                    }
                ],
            }
        )
    )
    with pytest.raises(DocumentError, match="unknown material"):
        Document.load(path)


def test_set_body_material_is_an_undoable_edit_an_agent_reads_back(tmp_path):
    with (
        running({"features": CASE}, tmp_path) as daemon,
        Client(daemon.socket_path) as client,
    ):
        client.call("set_body", id="case", material="anodised_aluminium")
        assert (
            client.call("get_bodies")["bodies"][0]["material"] == "anodised_aluminium"
        )
        client.call("undo")
        assert client.call("get_bodies")["bodies"][0]["material"] is None
