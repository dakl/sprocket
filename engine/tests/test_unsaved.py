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
