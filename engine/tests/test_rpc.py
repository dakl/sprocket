"""The daemon: one document, several clients, and nothing that can wedge it.

These run against a real socket rather than calling the method functions, so
the framing, the error mapping and the broadcasting are all under test. A unit
test of `_apply_edit` would have passed throughout the session where a fillet
that did not build was reported as a success.
"""

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.rpc import Client, Daemon, RpcError, STALE_REVISION, Session

from conftest import ALL_EDGES, EXTRUDE, SKETCH, running


def insert(feature, **params):
    return {"edits": [{"op": "insert", "feature": feature}], **params}


def test_a_fillet_that_does_not_build_is_reported_as_a_failure(client):
    """The edit is still accepted: a timeline is allowed an unbuildable tip.

    But a bare success is what sent an agent off measuring the part without
    the fillet, so the reply has to name the feature that failed.
    """
    bad = {"id": "f1", "type": "fillet", "base": "e1", "radius": 999, "edges": ALL_EDGES}
    result = client.call("apply_edit", **insert(bad))
    assert result["built"] == {"ok": False, "failed_feature": "f1", "volume": 12000.0}


def test_a_fillet_that_builds_reports_the_new_volume(client):
    good = {"id": "f1", "type": "fillet", "base": "e1", "radius": 3, "edges": ALL_EDGES}
    result = client.call("apply_edit", **insert(good))
    assert result["built"]["ok"] is True
    assert result["built"]["volume"] < 12000.0


def test_preview_returns_no_measurements_when_the_feature_fails(client):
    """A number beside `would_build: false` is a number somebody reads as the
    answer, so a failed preview carries the reason and nothing else."""
    bad = {"id": "try", "type": "fillet", "base": "e1", "radius": 999, "edges": ALL_EDGES}
    result = client.call("preview_feature", feature=bad)
    assert result["would_build"] is False
    assert "volume" not in result
    assert "999" in result["reason"]
    # And the document is untouched.
    assert client.call("get_document")["revision"] == 0


def test_preview_does_not_change_the_document(client):
    good = {"id": "try", "type": "fillet", "base": "e1", "radius": 3, "edges": ALL_EDGES}
    assert client.call("preview_feature", feature=good)["would_build"] is True
    assert [feature["id"] for feature in client.call("get_document")["features"]] == ["s1", "e1"]


def test_a_stale_edit_comes_back_with_the_current_document(client):
    """One round trip, not two: the rejection carries what the client needs to
    rebase, so a losing writer does not have to re-read before retrying."""
    client.call("set_parameter", name="width", value=40)
    with pytest.raises(RpcError) as caught:
        client.call("set_parameter", name="height", value=20, base_revision=0)
    assert caught.value.code == STALE_REVISION
    assert caught.value.data["based_on"] == 0
    assert caught.value.data["current"] == 1
    assert caught.value.data["document"]["parameters"] == {"width": 40}


def test_a_parameter_change_rebuilds_the_part(client):
    client.call("set_parameter", name="thickness", value=25)
    client.call(
        "apply_edit",
        edits=[{"op": "update", "feature": {**EXTRUDE, "distance": "=thickness"}}],
    )
    assert client.call("measure")["volume"] == pytest.approx(40 * 30 * 25)


def test_an_edit_is_saved_to_disk_before_the_reply(client, daemon):
    """A crashed app must lose nothing an agent did, so the file on disk is
    always the revision the reply names."""
    revision = client.call("set_parameter", name="width", value=40)["revision"]
    saved = json.loads(daemon.session.document.path.read_text())
    assert saved["revision"] == revision
    assert saved["parameters"] == {"width": 40}


def test_every_client_hears_about_an_edit(daemon):
    heard = []

    def note(method, params):
        heard.append({"method": method, "params": params})

    with Client(daemon.socket_path, on_notification=note) as listener, Client(daemon.socket_path) as editor:
        listener.call("ping")
        editor.call("set_parameter", name="width", value=40)
        # The listener only reads while it is in a call, so give it one.
        deadline = time.monotonic() + 5
        while not heard and time.monotonic() < deadline:
            listener.call("ping")
    assert heard[0]["method"] == "document_changed"
    assert heard[0]["params"]["revision"] == 1


def test_a_broken_feature_does_not_take_the_daemon_down(client):
    with pytest.raises(RpcError):
        client.call("apply_edit", **insert({"id": "x", "type": "nonsense"}))
    assert client.call("ping")["ok"] is True


def test_an_unknown_method_is_refused_by_name(client):
    with pytest.raises(RpcError, match="explode"):
        client.call("explode")
    assert "render_view" in client.call("list_methods")["methods"]


def test_get_schemas_covers_every_registered_op(client):
    from sprocket_engine.ops import REGISTRY

    assert set(client.call("get_schemas")) == set(REGISTRY)


def test_an_empty_document_says_so_rather_than_failing_obscurely(tmp_path: Path):
    with running({"features": [], "rollback_index": 0}, tmp_path) as daemon:
        with Client(daemon.socket_path) as connected:
            with pytest.raises(RpcError, match="builds to nothing"):
                connected.call("measure")


def test_a_broken_feature_is_named_rather_than_called_no_geometry(tmp_path: Path):
    """An invalid feature and an empty document both replay to nothing.

    Saying only "no geometry" sent an agent looking at its rollback index when
    what it had written was a feature the schema rejects.
    """
    # A zero-length extrude: the schema takes it, OCCT does not. A document of
    # one bad sketch would not do, because the replay trims back to the last
    # feature that made a solid and so never runs the sketch at all.
    broken = {"features": [SKETCH, {**EXTRUDE, "distance": 0}], "rollback_index": 2}
    with running(broken, tmp_path) as daemon:
        with Client(daemon.socket_path) as connected:
            with pytest.raises(RpcError, match="'e1' does not build"):
                connected.call("describe_geometry")


def test_shutdown_removes_the_socket_file(daemon):
    """A leftover socket file makes the next start fail against nothing."""
    assert daemon.socket_path.exists()
    daemon.shutdown()
    assert not daemon.socket_path.exists()


def test_a_socket_path_the_kernel_cannot_bind_says_which_limit_it_broke(tmp_path: Path):
    """Bind's own message is "AF_UNIX path too long", with no path in it."""
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps({"features": [], "rollback_index": 0}))
    long_socket = tmp_path / ("d" * 120 + ".sock")
    daemon = Daemon(Session(Document.load(path), cache_dir=tmp_path / "cache"), long_socket)
    with pytest.raises(DocumentError, match="shorter --socket"):
        daemon.start_in_background()


def distance_of_e1(client) -> float:
    return next(f for f in client.call("get_document")["features"] if f["id"] == "e1")["distance"]


def set_distance(client, distance: float) -> None:
    client.call("apply_edit", edits=[{"op": "update", "id": "e1", "feature": {**EXTRUDE, "distance": distance}}])


def test_undo_and_redo_step_through_edits(client):
    set_distance(client, 25)
    set_distance(client, 30)

    assert client.call("undo")["changed"] is True
    assert distance_of_e1(client) == 25
    client.call("undo")
    assert distance_of_e1(client) == 10
    assert client.call("undo")["changed"] is False

    client.call("redo")
    assert distance_of_e1(client) == 25


def test_a_new_edit_clears_redo(client):
    set_distance(client, 25)
    client.call("undo")
    set_distance(client, 40)
    assert client.call("redo")["changed"] is False
    assert distance_of_e1(client) == 40


def test_moving_the_rollback_bar_is_not_undoable(client):
    """Cmd+Z after scrubbing the timeline must undo the last edit, not the scrub."""
    set_distance(client, 25)
    client.call("set_rollback", index=1)
    client.call("undo")
    assert distance_of_e1(client) == 10


def test_a_daemon_with_a_viewer_dir_writes_the_files_the_app_reads(tmp_path: Path):
    """The Swift app opens the viewer from disk. After every change the
    daemon rewrites the mesh and the timeline beside it, before it says so."""
    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps({"features": [SKETCH, EXTRUDE], "rollback_index": 2}))
    socket_dir = Path(tempfile.mkdtemp())
    daemon = Daemon(
        Session(Document.load(path), cache_dir=tmp_path / "cache"), socket_dir / "d.sock", viewer_dir=viewer_dir
    )
    daemon.publish_viewer_files()
    daemon.start_in_background()
    try:
        assert (viewer_dir / "part.mesh").exists()
        assert "extrude" in json.loads((viewer_dir / "features.json").read_text())[1]["type"]
        assert "sketch" in json.loads((viewer_dir / "schemas.json").read_text())

        with Client(daemon.socket_path) as client:
            client.call("set_rollback", index=0)
        # An empty document deletes the mesh, which the viewer reads as nothing to show.
        assert not (viewer_dir / "part.mesh").exists()
        assert [entry["active"] for entry in json.loads((viewer_dir / "features.json").read_text())] == [False, False]
    finally:
        daemon.shutdown()
        shutil.rmtree(socket_dir, ignore_errors=True)


def test_a_mesh_that_cannot_be_written_reaches_the_viewer_as_text(tmp_path: Path, monkeypatch):
    """The part builds, but the export refuses it. The daemon used to log that
    to stderr and leave the previous document's mesh on screen, so opening the
    file looked like nothing happened. Now the mesh is deleted and the
    notification carries the reason."""
    import sprocket_engine.viewer_files as viewer_files

    def refuse(shape, appearances=None):
        raise ValueError("tessellation disagrees with the B-rep")

    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    (viewer_dir / "part.mesh").write_bytes(b"the previous document")
    path = tmp_path / "part.sprocket"
    path.write_text(json.dumps({"features": [SKETCH, EXTRUDE], "rollback_index": 2}))
    socket_dir = Path(tempfile.mkdtemp())
    daemon = Daemon(
        Session(Document.load(path), cache_dir=tmp_path / "cache"), socket_dir / "d.sock", viewer_dir=viewer_dir
    )
    monkeypatch.setattr(viewer_files, "mesh_bytes", refuse)
    daemon.start_in_background()
    heard = []
    try:
        with Client(daemon.socket_path, on_notification=lambda method, params: heard.append(params)) as client:
            client.call("set_rollback", index=1)
            client.call("ping")  # reading a reply is what delivers the queued notification
        assert heard[-1]["viewer_error"] == "ValueError: tessellation disagrees with the B-rep"
        assert not (viewer_dir / "part.mesh").exists(), "the old mesh would show the wrong part"
        assert len(json.loads((viewer_dir / "features.json").read_text())) == 2, "the timeline is still written"
    finally:
        daemon.shutdown()
        shutil.rmtree(socket_dir, ignore_errors=True)


# ---- opening, creating and saving documents ----------------------------------


def write_document(path: Path, features: list[dict]) -> Path:
    path.write_text(json.dumps({"features": features, "rollback_index": len(features)}))
    return path


def test_opening_a_document_replaces_the_open_one_and_keeps_its_file_untouched(client, tmp_path):
    other = write_document(tmp_path / "other.sprocket", [SKETCH])
    before = other.read_text()
    client.call("set_parameter", name="width", value=40)  # an undoable edit on the first document

    opened = client.call("open_document", path=str(other))

    assert opened["path"] == str(other)
    assert [feature["id"] for feature in client.call("get_document")["features"]] == ["s1"]
    assert client.call("get_document")["path"] == str(other)
    assert other.read_text() == before, "opening a file must not rewrite it"
    assert client.call("undo")["changed"] is False, "the undo history belonged to the first document"


def test_a_file_that_will_not_open_leaves_the_open_document_alone(client, tmp_path):
    broken = tmp_path / "broken.sprocket"
    broken.write_text("{ not json")
    with pytest.raises(RpcError, match="not valid JSON"):
        client.call("open_document", path=str(broken))
    assert [feature["id"] for feature in client.call("get_document")["features"]] == ["s1", "e1"]


@pytest.mark.parametrize("path", ["part.sprocket", "/tmp/part.txt", ""])
def test_file_methods_want_an_absolute_sprocket_path(client, path):
    with pytest.raises(RpcError):
        client.call("open_document", path=path)
    with pytest.raises(RpcError):
        client.call("save_as", path=path)


def test_opening_announces_a_replacement_not_an_edit(daemon, tmp_path):
    other = write_document(tmp_path / "other.sprocket", [SKETCH])
    heard = []
    with Client(daemon.socket_path, on_notification=lambda method, params: heard.append(params)) as watcher:
        watcher.call("ping")
        with Client(daemon.socket_path) as other_client:
            other_client.call("open_document", path=str(other))
        watcher.call("ping")  # reading a reply is what delivers the queued notification
    assert heard[-1]["opened"] is True
    assert heard[-1]["path"] == str(other)


def test_a_new_document_is_empty_saved_and_never_overwrites(client, tmp_path):
    fresh = tmp_path / "fresh.sprocket"
    made = client.call("new_document", path=str(fresh))
    assert made["path"] == str(fresh)
    assert fresh.exists()
    assert client.call("get_document")["features"] == []

    with pytest.raises(RpcError, match="already exists"):
        client.call("new_document", path=str(fresh))


def test_save_as_moves_autosave_to_the_new_file(client, tmp_path):
    original = tmp_path / "part.sprocket"
    copy = tmp_path / "copy.sprocket"
    client.call("save_as", path=str(copy))
    assert Document.load(copy).features == Document.load(original).features

    client.call("set_parameter", name="width", value=55)
    assert Document.load(copy).parameters == {"width": 55}
    assert Document.load(original).parameters == {}, "the first file is where it was left"


def test_an_agent_cannot_swap_the_users_document():
    """Opening and saving are the user's, through the app's menus. The agent's
    tools edit the open document and nothing else."""
    from sprocket_engine.mcp_server import document_tools, op_tools

    names = {tool.name for tool in document_tools() + op_tools()}
    assert not names & {"open_document", "new_document", "save_as"}


def test_a_daemon_export_to_a_missing_directory_says_why(client, tmp_path):
    target = tmp_path / "nowhere" / "part.step"
    with pytest.raises(RpcError, match="does not exist"):
        client.call("export", step=str(target))
