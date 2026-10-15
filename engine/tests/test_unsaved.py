"""Saving by hand: the daemon with autosave off, as the app runs it. Whether the
document has unsaved edits, Save, and the recovery file that covers a crash."""

import json

import pytest

from sprocket_engine.document import Document
from sprocket_engine.rpc import Client, RpcError

TALLER = {"op": "update", "feature": {"id": "e1", "type": "extrude", "profile": "s1", "distance": 12}}


@pytest.fixture
def manual(daemon, tmp_path):
    """The daemon as the app starts it: --no-autosave --recovery <file>."""
    daemon.autosave = False
    daemon.recovery_path = tmp_path / "recovery.json"
    return daemon


@pytest.fixture
def listener(daemon):
    seen = []
    with Client(daemon.socket_path, on_notification=lambda method, params: seen.append((method, params))) as connected:
        connected.call("ping")
        connected.seen = seen
        yield connected


def last_change(listener):
    listener.call("ping")
    return [params for name, params in listener.seen if name == "document_changed"][-1]


def test_an_edit_is_unsaved_and_leaves_the_file_alone(manual, client, listener):
    before = manual.session.document.path.read_bytes()
    client.call("apply_edit", edits=[TALLER])
    assert last_change(listener)["unsaved"] is True
    assert client.call("get_file_status")["unsaved"] is True
    assert manual.session.document.path.read_bytes() == before


def test_undo_back_to_the_saved_state_is_not_unsaved(manual, client, listener):
    client.call("apply_edit", edits=[TALLER])
    client.call("undo")
    assert last_change(listener)["unsaved"] is False
    assert not manual.recovery_path.exists()


def test_save_writes_the_file_and_clears_unsaved(manual, client, listener):
    client.call("apply_edit", edits=[TALLER])
    client.call("save")
    assert last_change(listener)["unsaved"] is False
    assert Document.load(manual.session.document.path).features[1]["distance"] == 12
    # Undo after a save is a change from what the file now holds.
    client.call("undo")
    assert last_change(listener)["unsaved"] is True


def test_save_refuses_to_overwrite_a_file_changed_on_disk(manual, client):
    client.call("apply_edit", edits=[TALLER])
    path = manual.session.document.path
    path.write_text(path.read_text() + "\n")
    with pytest.raises(RpcError, match="changed on disk"):
        client.call("save")
    assert client.call("get_file_status")["conflict"] is not None


def test_unsaved_edits_are_copied_to_the_recovery_file_and_deleted_once_saved(manual, client):
    client.call("apply_edit", edits=[TALLER])
    recovered = json.loads(manual.recovery_path.read_text())
    assert recovered["path"] == str(manual.session.document.path)
    assert recovered["snapshot"]["features"][1]["distance"] == 12
    client.call("save")
    assert not manual.recovery_path.exists()


def test_restore_recovery_puts_the_edits_back_unsaved_and_undoable(manual, client, listener, tmp_path):
    client.call("apply_edit", edits=[TALLER])
    kept = tmp_path / "kept.json"
    kept.write_text(manual.recovery_path.read_text())
    client.call("undo")
    assert client.call("get_document")["features"][1]["distance"] != 12

    client.call("restore_recovery", file=str(kept))
    assert client.call("get_document")["features"][1]["distance"] == 12
    assert last_change(listener)["unsaved"] is True
    client.call("undo")
    assert client.call("get_document")["features"][1]["distance"] != 12


def test_autosave_still_saves_every_edit(daemon, client, listener):
    client.call("apply_edit", edits=[TALLER])
    assert last_change(listener)["unsaved"] is False
    assert Document.load(daemon.session.document.path).features[1]["distance"] == 12


def test_document_moved_follows_the_file_without_saving(manual, client, tmp_path):
    client.call("apply_edit", edits=[TALLER])
    old = manual.session.document.path
    new = tmp_path / "renamed.sprocket"
    old.rename(new)  # what NSDocument's Rename does before it tells the engine
    before = new.read_bytes()

    client.call("document_moved", path=str(new))
    status = client.call("get_file_status")
    assert status["path"] == str(new)
    assert status["unsaved"] is True
    assert new.read_bytes() == before

    client.call("save")
    assert Document.load(new).features[1]["distance"] == 12
    assert not old.exists()


def test_document_moved_refuses_a_path_that_does_not_exist(manual, client, tmp_path):
    with pytest.raises(RpcError, match="does not exist"):
        client.call("document_moved", path=str(tmp_path / "missing.sprocket"))
    assert client.call("get_file_status")["path"] == str(manual.session.document.path)


def test_a_file_reloaded_from_disk_is_not_an_unsaved_edit(manual, client, listener):
    path = manual.session.document.path
    rewritten = json.loads(path.read_text())
    rewritten["features"][1]["distance"] = 30
    path.write_text(json.dumps(rewritten))
    assert manual.check_file() == "reloaded"
    assert last_change(listener)["unsaved"] is False
    assert not manual.recovery_path.exists()
    # Undoing the reload puts back what the file no longer holds.
    client.call("undo")
    assert last_change(listener)["unsaved"] is True


@pytest.mark.parametrize("content", [None, "not json", '{"path": null}'])
def test_restore_recovery_refuses_a_missing_or_broken_file(manual, client, tmp_path, content):
    file = tmp_path / "recovery.bad.json"
    if content is not None:
        file.write_text(content)
    revision = client.call("get_document")["revision"]
    with pytest.raises(RpcError, match="cannot read the recovery file"):
        client.call("restore_recovery", file=str(file))
    assert client.call("get_document")["revision"] == revision
