"""`sprocket web` against a real HTTP server and a real document.

The viewer's gestures are checked through `WebApp.command` and the routes, not
through `edits_for` alone: a rule that produces edits the document then refuses
is the failure that matters, and only the full path shows it.
"""

import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sprocket_engine.document import Document
from sprocket_engine.mesh import FORMAT
from sprocket_engine.rpc import Daemon, Session
from sprocket_engine.web import WebApp, make_server

SKETCH = {"id": "s1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 30}}
EXTRUDE = {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10}
TOP_FACE = {"point": [0, 0, 10], "normal": [0, 0, 1], "distance": 5}


@pytest.fixture
def server(tmp_path: Path):
    path = tmp_path / "part.json"
    path.write_text(json.dumps({"features": [SKETCH, EXTRUDE], "rollback_index": 2}))
    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    (viewer_dir / "index.html").write_text("<title>viewer</title>")

    socket_dir = Path(tempfile.mkdtemp())
    daemon = Daemon(Session(Document.load(path), cache_dir=tmp_path / "cache"), socket_dir / "d.sock")
    http = make_server(WebApp(daemon, viewer_dir), 0)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{http.server_address[1]}"
    yield base, daemon
    http.shutdown()
    http.server_close()


def get(base: str, route: str) -> bytes:
    with urllib.request.urlopen(base + route) as response:
        return response.read()


def post(base: str, route: str, body: dict) -> dict:
    request = urllib.request.Request(base + route, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return {"status": error.code, **json.loads(error.read())}


def mesh_header(base: str) -> dict:
    mesh = get(base, "/part.mesh")
    header_length = int.from_bytes(mesh[:4], "little")
    return json.loads(mesh[4 : 4 + header_length].rstrip(b"\0"))


def feature_ids(base: str) -> list[str]:
    return [entry["id"] for entry in json.loads(get(base, "/features.json"))]


def test_serves_the_viewer_files(server):
    base, _ = server
    assert b"<title>viewer</title>" in get(base, "/index.html")


def test_the_mesh_route_serves_the_built_part(server):
    base, _ = server
    header = mesh_header(base)
    assert header["format"] == FORMAT
    assert header["counts"]["faces"] == 6


def test_an_empty_document_has_no_mesh_and_the_viewer_sees_a_404(server):
    base, _ = server
    post(base, "/command", {"type": "new_document"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(base, "/part.mesh")
    assert caught.value.code == 404


def test_the_timeline_carries_each_full_feature_for_the_parameter_form(server):
    base, _ = server
    entries = json.loads(get(base, "/features.json"))
    assert entries[1]["feature"] == EXTRUDE
    assert [entry["active"] for entry in entries] == [True, True]


def test_the_schemas_route_lists_every_op(server):
    base, _ = server
    assert {"sketch", "extrude", "fillet"} <= set(json.loads(get(base, "/schemas.json")))


def test_extrude_face_appends_a_feature_that_builds(server):
    base, _ = server
    result = post(base, "/command", {"type": "extrude_face", **TOP_FACE})
    assert result["failed_feature"] is None
    assert feature_ids(base) == ["s1", "e1", "f3"]
    assert mesh_header(base)["counts"]["faces"] == 6


def test_building_after_a_rollback_discards_the_rolled_back_tail(server):
    base, _ = server
    post(base, "/command", {"type": "set_rollback", "index": 1})
    post(base, "/command", {"type": "add_sketch", "plane": "XY", "polygon": [[0, 0], [5, 0], [5, 5]]})
    # e1 sat past the bar, so it is gone and the new sketch takes its place.
    assert feature_ids(base) == ["s1", "f2"]


def test_extrude_sketch_fuses_into_the_solid_at_the_tip(server):
    base, _ = server
    post(base, "/command", {"type": "add_sketch", "plane": "XY", "polygon": [[0, 0], [5, 0], [5, 5]]})
    post(base, "/command", {"type": "extrude_sketch", "profile": "f3", "distance": 4})
    extrude = json.loads(get(base, "/features.json"))[-1]["feature"]
    assert extrude["type"] == "extrude"
    assert extrude["base"] == "e1"


def test_update_feature_edits_a_parameter_and_undo_puts_it_back(server):
    base, _ = server
    result = post(base, "/command", {"type": "update_feature", "id": "e1", "feature": {**EXTRUDE, "distance": 25}})
    assert result["revision"] == 1
    assert json.loads(get(base, "/features.json"))[1]["feature"]["distance"] == 25

    post(base, "/command", {"type": "undo"})
    assert json.loads(get(base, "/features.json"))[1]["feature"]["distance"] == 10
    post(base, "/command", {"type": "redo"})
    assert json.loads(get(base, "/features.json"))[1]["feature"]["distance"] == 25


def test_a_new_edit_clears_redo(server):
    base, _ = server
    post(base, "/command", {"type": "extrude_face", **TOP_FACE})
    post(base, "/command", {"type": "undo"})
    post(base, "/command", {"type": "extrude_face", **TOP_FACE, "distance": 2})
    post(base, "/command", {"type": "redo"})
    assert feature_ids(base) == ["s1", "e1", "f3"]


def test_rolling_back_is_not_undoable(server):
    """Cmd+Z after scrubbing the timeline must undo the last edit, not the scrub."""
    base, _ = server
    post(base, "/command", {"type": "update_feature", "id": "e1", "feature": {**EXTRUDE, "distance": 25}})
    post(base, "/command", {"type": "set_rollback", "index": 1})
    post(base, "/command", {"type": "undo"})
    entries = json.loads(get(base, "/features.json"))
    assert entries[1]["feature"]["distance"] == 10


def test_a_bad_command_is_a_400_with_the_reason(server):
    base, _ = server
    result = post(base, "/command", {"type": "update_feature", "id": "e1", "feature": {**EXTRUDE, "distance": 0}})
    assert result["status"] == 400
    assert "e1" in result["error"]
    # And the document is untouched.
    assert json.loads(get(base, "/features.json"))[1]["feature"]["distance"] == 10


def test_rpc_route_takes_the_daemons_methods(server):
    base, _ = server
    reply = post(base, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert reply["result"]["ok"] is True
