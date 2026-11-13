"""`sprocket web` against a real HTTP server and a real document.

The gesture rules are in viewer/edits.js and tested there. These cover what the
server owns: the files it serves, the rpc route and who may call it.
"""

import json
import socket
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

SKETCH = {
    "id": "s1",
    "type": "sketch",
    "plane": "XY",
    "rect": {"width": 40, "height": 30},
}
EXTRUDE = {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10}


@pytest.fixture
def server(tmp_path: Path):
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps({"features": [SKETCH, EXTRUDE], "rollback_index": 2}))
    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    (viewer_dir / "index.html").write_text("<title>viewer</title>")

    socket_dir = Path(tempfile.mkdtemp())
    daemon = Daemon(
        Session(Document.load(path), cache_dir=tmp_path / "cache"),
        socket_dir / "d.sock",
    )
    http = make_server(WebApp(daemon, viewer_dir), 0)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{http.server_address[1]}"
    yield base, daemon
    http.shutdown()
    http.server_close()


def get(base: str, route: str) -> bytes:
    with urllib.request.urlopen(base + route) as response:
        return response.read()


def post(base: str, route: str, body: dict, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + route,
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers or {},
    )
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


def rpc(base: str, method: str, **params) -> dict:
    reply = post(
        base, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    return reply["result"]


def test_an_empty_document_has_no_mesh_and_the_viewer_sees_a_404(server):
    base, _ = server
    rpc(
        base,
        "apply_edit",
        edits=[{"op": "delete", "id": "e1"}, {"op": "delete", "id": "s1"}],
    )
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
    assert {"sketch", "extrude", "fillet"} <= set(
        json.loads(get(base, "/schemas.json"))
    )


def test_rpc_route_takes_the_daemons_methods(server):
    base, _ = server
    reply = post(base, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert reply["result"]["ok"] is True


def test_an_edit_through_rpc_changes_the_served_timeline_and_mesh(server):
    base, _ = server
    feature = {
        "id": "f3",
        "type": "extrude_face",
        "base": "e1",
        "distance": 5,
        "face": {
            "from": "part",
            "select": [
                {"op": "faces"},
                {"op": "nearest_to", "point": [0, 0, 10], "normal": [0, 0, 1]},
            ],
        },
    }
    result = rpc(base, "apply_edit", edits=[{"op": "insert", "feature": feature}])
    assert result["built"]["ok"] is True
    assert feature_ids(base) == ["s1", "e1", "f3"]
    assert mesh_header(base)["counts"]["faces"] == 6


def test_a_page_on_another_origin_cannot_edit_the_document(server):
    """Any web page in the user's browser can reach 127.0.0.1. A simple POST
    needs no permission from the server, so the server has to check.
    """
    base, _ = server
    body = {"jsonrpc": "2.0", "id": 1, "method": "set_rollback", "params": {"index": 0}}
    refused = post(base, "/rpc", body, headers={"Origin": "https://evil.example"})
    assert refused["status"] == 403
    rebound = post(base, "/rpc", body, headers={"Host": "evil.example"})
    assert rebound["status"] == 403
    assert json.loads(get(base, "/features.json"))[1]["active"] is True
    # The viewer's own origin still works.
    port = base.rsplit(":", 1)[1]
    allowed = post(base, "/rpc", body, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert "result" in allowed


def test_opening_another_document_at_the_same_revision_serves_its_mesh(
    server, tmp_path
):
    """Both documents are at revision 0. A mesh cached by revision alone would
    keep serving the box.
    """
    base, _ = server
    assert mesh_header(base)["counts"]["faces"] == 6
    cylinder = tmp_path / "cylinder.sprocket"
    cylinder.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "id": "s1",
                        "type": "sketch",
                        "plane": "XY",
                        "circle": {"radius": 5},
                    },
                    {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10},
                ]
            }
        )
    )
    post(
        base,
        "/rpc",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "open_document",
            "params": {"path": str(cylinder)},
        },
    )
    assert mesh_header(base)["counts"]["faces"] == 3
    assert feature_ids(base) == ["s1", "e1"]


def test_a_burst_of_connections_waits_in_the_queue_instead_of_being_reset(
    tmp_path: Path,
):
    """A page load opens several connections at once.

    The stdlib's listen backlog of 5 made the kernel reset the extras while the
    server was busy, so a viewer <script src> failed with ERR_CONNECTION_RESET
    and the page never started. Here 20 clients connect before the server
    accepts any.
    """
    (tmp_path / "index.html").write_text("<title>viewer</title>")
    http = make_server(WebApp(None, tmp_path), 0)
    clients = []
    try:
        port = http.server_address[1]
        clients = [
            socket.create_connection(("127.0.0.1", port), timeout=1) for _ in range(20)
        ]
        threading.Thread(target=http.serve_forever, daemon=True).start()
        for client in clients:
            client.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
            assert b" 200 " in client.recv(64)
    finally:
        for client in clients:
            client.close()
        http.shutdown()
        http.server_close()


def get_with_headers(base: str, route: str, headers: dict | None = None):
    request = urllib.request.Request(base + route, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_export_route_hands_the_part_over_as_a_download(server):
    """A 40 x 30 x 10 box: a box is 12 triangles and 8 corners, whatever the writer does."""
    import io
    import re
    import struct
    import zipfile

    base, _ = server
    status, headers, stl = get_with_headers(base, "/export?format=stl")
    assert status == 200
    assert headers["Content-Disposition"] == 'attachment; filename="part.stl"'
    if stl[:5] == b"solid" and b"facet" in stl[:300]:
        assert stl.count(b"facet normal") == 12
    else:
        assert struct.unpack("<I", stl[80:84])[0] == 12

    _, _, step = get_with_headers(base, "/export?format=step")
    assert step.startswith(b"ISO-10303-21")
    assert b"MANIFOLD_SOLID_BREP" in step

    _, headers, threemf = get_with_headers(base, "/export?format=3mf")
    assert headers["Content-Disposition"] == 'attachment; filename="part.3mf"'
    with zipfile.ZipFile(io.BytesIO(threemf)) as archive:
        model = archive.read("3D/3dmodel.model").decode()
    assert len(re.findall(r"<vertex ", model)) == 8


def test_the_export_route_refuses_what_it_cannot_serve(server):
    base, _ = server
    assert get_with_headers(base, "/export?format=obj")[0] == 400
    assert get_with_headers(base, "/export")[0] == 400
    assert (
        get_with_headers(
            base, "/export?format=stl", {"Origin": "https://evil.example"}
        )[0]
        == 403
    )
    rpc(
        base,
        "apply_edit",
        edits=[{"op": "delete", "id": "e1"}, {"op": "delete", "id": "s1"}],
    )
    status, _, body = get_with_headers(base, "/export?format=stl")
    assert status == 409
    assert "no geometry" in json.loads(body)["error"]
