"""The daemon: one document, several clients, and nothing that can wedge it.

These run against a real socket rather than calling the method functions, so
the framing, the error mapping and the broadcasting are all under test. A unit
test of `_apply_edit` would have passed throughout the session where a fillet
that did not build was reported as a success.
"""

import json
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
    path = tmp_path / "part.json"
    path.write_text(json.dumps({"features": [], "rollback_index": 0}))
    long_socket = tmp_path / ("d" * 120 + ".sock")
    daemon = Daemon(Session(Document.load(path), cache_dir=tmp_path / "cache"), long_socket)
    with pytest.raises(DocumentError, match="shorter --socket"):
        daemon.start_in_background()
