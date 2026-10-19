"""Live updates: who changed the document, and the file changing on disk under
an open daemon (see Daemon.check_file for the rule)."""

import json
import os
import time

import pytest

from conftest import BOX_DOCUMENT, running
from sprocket_engine.rpc import Client, FileWatcher

TALLER = {"op": "update", "feature": {"id": "e1", "type": "extrude", "profile": "s1", "distance": 12}}


@pytest.fixture
def listener(daemon):
    seen = []
    with Client(daemon.socket_path, on_notification=lambda method, params: seen.append((method, params))) as connected:
        connected.call("ping")
        connected.seen = seen
        yield connected


def heard(listener, method):
    listener.call("ping")
    return [params for name, params in listener.seen if name == method]


def write_atomically(path, document):
    """What `sprocket build`, git and most editors do: a new file renamed over the old."""
    temporary = path.with_name(path.name + ".other")
    temporary.write_text(json.dumps(document))
    os.replace(temporary, path)


def on_disk(daemon):
    return json.loads(daemon.session.document.path.read_text())


def test_document_changed_says_who_made_the_change(daemon, client, listener):
    client.call("apply_edit", edits=[TALLER], author="agent:test")
    (change,) = heard(listener, "document_changed")
    assert change["author"] == "agent:test"
    assert change["changes"] == [{"action": "update", "feature_id": "e1"}]


def test_a_view_change_has_no_author(daemon, client, listener):
    client.call("set_visibility", id="e1", visible=False)
    assert heard(listener, "document_changed")[-1]["author"] is None


def test_the_engines_own_save_is_not_a_change_on_disk(daemon, client):
    client.call("apply_edit", edits=[TALLER], author="agent:test")
    assert daemon.check_file() is None
    assert client.call("get_file_status")["changed_on_disk"] is False


def test_a_file_replaced_on_disk_is_reloaded_as_an_undoable_step(daemon, client, listener):
    revision = client.call("get_document")["revision"]
    replacement = {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], TALLER["feature"]]}
    write_atomically(daemon.session.document.path, replacement)

    assert daemon.check_file() == "reloaded"
    document = client.call("get_document")
    assert document["revision"] == revision + 1
    assert document["features"][1]["distance"] == 12
    change = heard(listener, "document_changed")[-1]
    assert change["author"] == "file"
    assert change["opened"] is False  # the same document: the camera stays
    # Reloading does not write the file, so a git checkout stays clean.
    assert on_disk(daemon) == replacement
    assert daemon.check_file() is None

    client.call("undo")
    assert client.call("get_document")["features"][1]["distance"] == 10
    assert on_disk(daemon)["features"][1]["distance"] == 10


def test_a_half_written_file_is_left_alone_until_it_reads(daemon, client):
    path = daemon.session.document.path
    path.write_text('{"features": [')
    assert daemon.check_file() == "unreadable"
    assert client.call("get_document")["features"][1]["distance"] == 10
    path.write_text(json.dumps({**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], TALLER["feature"]]}))
    assert daemon.check_file() == "reloaded"


def test_unsaved_edits_and_a_changed_file_are_a_conflict_not_a_reload(daemon, client, listener):
    daemon.autosave = False
    client.call("apply_edit", edits=[TALLER], author="viewer")
    theirs = {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], {**TALLER["feature"], "distance": 20}]}
    write_atomically(daemon.session.document.path, theirs)

    assert daemon.check_file() == "conflict"
    assert client.call("get_document")["features"][1]["distance"] == 12  # the human's edit is still there
    (conflict,) = heard(listener, "document_conflict")
    assert "changed on disk" in conflict["message"]
    assert daemon.check_file() == "conflict"
    assert len(heard(listener, "document_conflict")) == 1  # said once, not every poll

    client.call("resolve_conflict", keep="mine")
    assert on_disk(daemon)["features"][1]["distance"] == 12
    assert client.call("get_file_status")["conflict"] is None


def test_keeping_the_file_in_a_conflict_still_lets_undo_bring_the_edit_back(daemon, client):
    daemon.autosave = False
    client.call("apply_edit", edits=[TALLER], author="viewer")
    theirs = {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], {**TALLER["feature"], "distance": 20}]}
    write_atomically(daemon.session.document.path, theirs)
    daemon.check_file()

    client.call("resolve_conflict", keep="file")
    assert client.call("get_document")["features"][1]["distance"] == 20
    client.call("undo")
    assert client.call("get_document")["features"][1]["distance"] == 12


def test_an_edit_that_lands_before_the_watcher_looks_does_not_overwrite_the_file(daemon, client, listener):
    """The case that lost a regenerated file: the app saved its next edit over it."""
    theirs = {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], {**TALLER["feature"], "distance": 20}]}
    write_atomically(daemon.session.document.path, theirs)
    client.call("set_rollback", index=1)
    assert on_disk(daemon) == theirs
    assert heard(listener, "document_conflict")
    assert client.call("get_file_status")["unsaved"] is True


def test_the_selection_survives_a_reload_when_its_face_is_still_there(daemon, client):
    faces = client.call("describe_geometry")["faces"]
    bottom = next(item for item in faces if item["centre"][2] == pytest.approx(0))
    client.call("set_selection", handles=[bottom["handle"]], revision=client.call("get_document")["revision"])
    write_atomically(daemon.session.document.path, {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], TALLER["feature"]]})
    daemon.check_file()
    selection = client.call("get_selection")
    assert selection["stale"] is False
    (handle,) = selection["handles"]
    assert selection["descriptions"][0]["centre"] == bottom["centre"]


def test_the_watcher_reloads_a_rewritten_file_within_a_second(tmp_path):
    with running(BOX_DOCUMENT, tmp_path) as daemon, Client(daemon.socket_path) as client:
        daemon.start_watching(interval=0.05)
        revision = client.call("get_document")["revision"]
        write_atomically(daemon.session.document.path, {**BOX_DOCUMENT, "features": [BOX_DOCUMENT["features"][0], TALLER["feature"]]})
        deadline = time.monotonic() + 1.0
        while client.call("get_document")["revision"] == revision and time.monotonic() < deadline:
            time.sleep(0.02)
        assert client.call("get_document")["features"][1]["distance"] == 12
        # Its own saves from here on are not changes.
        client.call("set_rollback", index=1)
        time.sleep(0.3)
        assert client.call("get_document")["rollback_index"] == 1


# --- a referenced file changing on disk -------------------------------------------

BOARD = {"features": [
    {"id": "s", "type": "sketch", "plane": "XY", "rect": {"width": 10, "height": 10}},
    {"id": "b", "type": "extrude", "profile": "s", "distance": 2},
]}


def taller_board(height):
    return {"features": [BOARD["features"][0], {**BOARD["features"][1], "distance": height}]}


def case_document(board_path):
    tray_s = {"id": "ts", "type": "sketch", "plane": {"name": "XY", "offset": -3}, "rect": {"width": 20, "height": 20}}
    tray = {"id": "tray", "type": "extrude", "profile": "ts", "distance": 4}
    return {"features": [{"id": "board", "type": "reference", "file": str(board_path)}, tray_s, tray]}


def board_height(client):
    bodies = client.call("get_bodies")["bodies"]
    return next(body for body in bodies if body["reference"])["volume"] / 100


def test_a_referenced_file_rewritten_on_disk_rebuilds_the_document_that_uses_it(tmp_path):
    board = tmp_path / "board.sprocket"
    board.write_text(json.dumps(BOARD))
    seen = []
    with running(case_document(board), tmp_path) as daemon, Client(daemon.socket_path, on_notification=lambda method, params: seen.append((method, params))) as client:
        daemon.start_watching(interval=0.05)
        assert board_height(client) == pytest.approx(2)
        revision = client.call("get_document")["revision"]
        saved = daemon.session.document.path.read_bytes()
        write_atomically(board, taller_board(5))
        deadline = time.monotonic() + 2
        while board_height(client) != pytest.approx(5) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert board_height(client) == pytest.approx(5)
        # The case's own file and revision are untouched: nothing in it changed.
        assert client.call("get_document")["revision"] == revision
        assert daemon.session.document.path.read_bytes() == saved
        change = [params for method, params in seen if method == "document_changed"][-1]
        assert change["refresh"] is True
        assert change["changes"] == [{"action": "reference_changed", "feature_id": "board", "file": "board.sprocket"}]
        assert daemon.check_references() is None  # already up to date


def test_a_file_that_changed_before_the_watcher_first_saw_it_is_still_checked(tmp_path):
    # A reference added, then its file rewritten before the watcher's next
    # poll: the watcher's first stat is of the new file, so it must not be
    # taken as the starting point and dropped.
    board = tmp_path / "board.sprocket"
    board.write_text(json.dumps(BOARD))
    watched, checked = [], []
    watcher = FileWatcher(lambda: list(watched), checked.append, interval=0.02)
    watcher.start()
    try:
        time.sleep(0.1)
        write_atomically(board, taller_board(5))
        watched.append(board)
        deadline = time.monotonic() + 1
        while not checked and time.monotonic() < deadline:
            time.sleep(0.02)
        assert checked == [board]
    finally:
        watcher.stop()


def test_a_change_two_references_deep_reaches_the_top_document(tmp_path):
    board = tmp_path / "board.sprocket"
    board.write_text(json.dumps(BOARD))
    middle = tmp_path / "middle.sprocket"
    # A reference body is never part of what a document builds, so the middle
    # one joins the board to a 1 mm cube of its own to pass it on.
    middle.write_text(json.dumps({"features": [
        {"id": "inner", "type": "reference", "file": "board.sprocket"},
        {"id": "cs", "type": "sketch", "plane": "XY", "rect": {"width": 1, "height": 1, "center": [30, 0]}},
        {"id": "cube", "type": "extrude", "profile": "cs", "distance": 1},
        {"id": "joined", "type": "boolean", "operation": "union", "base": "cube", "tool": "inner"},
    ]}))
    with running(case_document(middle), tmp_path) as daemon, Client(daemon.socket_path) as client:
        daemon.start_watching(interval=0.05)
        assert board in daemon.watched_files()
        assert board_height(client) == pytest.approx(2.01)
        write_atomically(board, taller_board(7))
        assert daemon.check_references() == "rebuilt"
        assert board_height(client) == pytest.approx(7.01)
