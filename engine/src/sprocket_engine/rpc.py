"""The engine daemon: JSON-RPC 2.0 over a Unix socket.

M0 ran the engine as a stdio child of the Swift app, one request per line.
That works for exactly one client. M1 needs four at once: the app, the viewer,
an MCP server driving the document for an agent, and the test harness. A
socket lets all four attach to the same live document, so what the agent edits
is what the user sees, and it does not need the app running to work.

Framing is one JSON object per line, not Content-Length headers. A line is
trivially readable from Swift, from a shell with `nc`, and from a test, and
JSON itself has no unescaped newlines to break it.

Everything runs under one lock. OCCT is not thread-safe, so a second replay
running beside the first is a crash rather than a race, and the document's
revision counter would be wrong besides. Threads exist here to let a slow
client's socket drain without blocking the others, not for parallel geometry.

Concurrency is optimistic. Every mutating call carries the revision it was
based on. A stale one is rejected with the current revision and the current
document attached, so a caller can rebase without a second round trip. There
are no locks a client can hold, because an agent that dies holding one would
freeze the user's app.
"""

import json
import os
import re
import socket
import socketserver
import threading
import traceback
from pathlib import Path

from .document import Document, DocumentError, StaleRevision, reference_digest, validate_feature
from .geometry import _resolve_handle, describe, measure
from .ops import REGISTRY
from .ops.sketch import sketch_profiles
from .render import render_png
from .replay import build_shape, last_solid_index, replay, with_settings
from .selectors import SelectorError, resolve_selector
from .synthesis import synthesize
from .viewer_files import write_viewer_files

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application errors, in JSON-RPC's reserved-for-the-server range.
DOCUMENT_ERROR = -32000
STALE_REVISION = -32001
BUILD_ERROR = -32002


class Session:
    """The live document, its built shape, and the policy for rebuilding it.

    The shape is memoised against the revision rather than rebuilt per call.
    `describe_geometry` then `measure` then `render_view` is the normal agent
    loop, and replaying a fillet three times to answer three questions about
    one unchanged document is the cost this avoids.
    """

    def __init__(self, document: Document, cache_dir: Path | None = None):
        self.document = document
        self._chosen_cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_dir = self._chosen_cache_dir or self._default_cache_dir(document)
        self._shape = None
        self._failed_feature = None
        self._context = None
        self._built_revision = None
        # Counts the documents this session has held. A revision number is
        # only unique within one document.
        self.generation = 0
        # Counts rebuilds caused by a referenced file changing, which leave
        # the revision as it was. A cache keyed on the revision needs it too.
        self.reference_rebuilds = 0

    def use(self, document: Document) -> None:
        """Switches to another document. A cache directory chosen at start-up
        stays, and otherwise the cache follows the document's folder."""
        self.document = document
        self.generation += 1
        self.cache_dir = self._chosen_cache_dir or self._default_cache_dir(document)
        self.invalidate()

    def moved(self) -> None:
        """The document was saved under a new path, so the default cache moves with it."""
        self.cache_dir = self._chosen_cache_dir or self._default_cache_dir(self.document)

    @staticmethod
    def _default_cache_dir(document: Document) -> Path:
        return (document.path.parent / "cache") if document.path else Path.cwd() / "cache"

    def invalidate(self) -> None:
        self._built_revision = None

    def shape(self):
        """Returns (shape, failed_feature_id). The shape is None for an empty
        document, which is a valid state and not an error."""
        if self._built_revision != self.document.revision:
            replayable = self.document.for_replay()
            self._shape, self._failed_feature, self._context = build_shape(
                replayable["features"], replayable["rollback_index"], self.cache_dir
            )
            self._built_revision = self.document.revision
        return self._shape, self._failed_feature

    def context(self):
        """The replay context of the current build: every feature's result."""
        self.shape()
        return self._context

    def bodies(self) -> list[dict]:
        """The built bodies with the document's settings for each."""
        context = self.context()
        return with_settings(context.bodies, self.document.bodies) if context else []

    def require_shape(self):
        """The built shape, or a readable reason there isn't one.

        Naming the broken feature matters more here than anywhere else: an
        invalid feature and an empty document both replay to None, and a bare
        "no geometry" sent an agent looking at its rollback index when what it
        had actually written was a feature the schema rejects.
        """
        shape, failed = self.shape()
        if shape is None and failed:
            raise DocumentError(
                f"feature {failed!r} does not build, and nothing before it builds a solid"
            )
        if shape is None:
            raise DocumentError("this document builds to nothing, so there is no geometry yet")
        return shape


def empty_selection() -> dict:
    return {"handles": [], "sketches": [], "revision": None}


class Daemon:
    """Owns the socket, the session lock and the connected clients."""

    def __init__(
        self, session: Session, socket_path: Path, autosave: bool = True, viewer_dir: Path | None = None
    ):
        self.session = session
        # When set, the viewer's files are rewritten after every change (see
        # viewer_files.py). The Swift app reads them off disk.
        self.viewer_dir = Path(viewer_dir) if viewer_dir else None
        self.socket_path = Path(socket_path)
        self.autosave = autosave
        self.lock = threading.RLock()
        # Snapshots of the editable state, one per undoable edit. Shared by
        # every client, so an undo undoes the newest edit whoever made it.
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        # What the person has picked in the viewer. See `_set_selection`.
        self.selection: dict = empty_selection()
        # A geometric key per selected handle, so a rebuild can find the same
        # face or edge again under its new number. See `_carry_selection`.
        self.selection_keys: dict[str, tuple] = {}
        # The engine holds edits the file does not: autosave is off, or a save
        # was held back because the file changed under it.
        self.unsaved = False
        # The file changed on disk while there were unsaved edits. Nothing is
        # saved or reloaded until a client calls `resolve_conflict`.
        self.conflict: dict | None = None
        self._watcher: "FileWatcher | None" = None
        # Each reference feature's digest at the last build (see
        # document.reference_digest), and the files those digests read.
        self.reference_digests: dict[str, str | None] = {}
        self.watched_references: list[Path] = []
        self._clients: set = set()
        self._clients_lock = threading.Lock()
        self._server = None

    # ---- client registry ---------------------------------------------------

    def add_client(self, handler) -> None:
        with self._clients_lock:
            self._clients.add(handler)

    def remove_client(self, handler) -> None:
        with self._clients_lock:
            self._clients.discard(handler)

    def broadcast(self, method: str, params: dict) -> None:
        """Tells every connected client that the document moved.

        A notification, so it carries no id and nobody replies to it. The app's
        viewer and an agent's MCP server are looking at the same document, and
        without this the user watches a stale part while an agent edits it.

        A client whose socket has already gone is dropped rather than raised
        on: one dead viewer must not stop the others being told.
        """
        message = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.send_line(message)
            except OSError:
                self.remove_client(client)

    def publish_viewer_files(self) -> str | None:
        """Rewrites the viewer's files. Returns why the part could not be drawn,
        or None, so a change notification can carry it to the viewer."""
        if self.viewer_dir is None:
            return None
        try:
            error = write_viewer_files(self.session, self.viewer_dir)
        except Exception as exc:
            # A document that will not build must not stop the edit that
            # caused it. The viewer keeps the files from the last good build.
            error = f"{type(exc).__name__}: {exc}"
        if error:
            print(f"viewer files not written: {error}", flush=True)
        return error

    def announce_change(self, opened: bool = False, author: str | None = None, changes: list | None = None) -> None:
        """`opened` says the document was replaced by another, not edited. A
        viewer then drops its selection and refits the camera, and cannot
        trust that an unchanged revision number means an unchanged part.

        `author` and `changes` come off the log entries of this revision, so a
        viewer can say who moved the part: `viewer`, `agent:...`, or `file`
        for a reload from disk. A view change has no entry, so no author."""
        viewer_error = self.publish_viewer_files()
        document = self.session.document
        self._note_references()
        entries = []
        for entry in reversed(document.log):
            if entry.get("revision") != document.revision:
                break
            entries.insert(0, entry)
        self.broadcast(
            "document_changed",
            {
                "revision": document.revision,
                "timeline": document.timeline(),
                "rollback_index": document.rollback_index,
                "path": str(document.path) if document.path else None,
                "opened": opened,
                "viewer_error": viewer_error,
                "author": author or (entries[-1].get("author") if entries else None),
                # A body's id is the id of the feature that starts it.
                "changes": changes if changes is not None else [
                    {"action": entry.get("action"), "feature_id": entry.get("feature_id") or entry.get("body")} for entry in entries
                ],
                # The part changed although the revision did not: a viewer
                # must reload instead of skipping a revision it already has.
                "refresh": changes is not None,
                "conflict": self.conflict,
            },
        )

    # ---- the file on disk --------------------------------------------------
    #
    # Rule: the engine saves every edit as it happens, so the file normally
    # holds exactly what the engine last wrote (`Document.saved_sha256`).
    # Different bytes on disk mean someone else wrote it. With no unsaved edits
    # the engine reloads it, as an undoable step. With unsaved edits it is a
    # conflict: nothing is overwritten or reloaded until the person picks one.

    def save_document(self, force: bool = False) -> bool:
        """Saves unless the file changed under the engine. Returns whether it saved."""
        document = self.session.document
        on_disk = document.file_sha256()
        if not force and on_disk is not None and document.saved_sha256 is not None and on_disk != document.saved_sha256:
            self.unsaved = True
            self._enter_conflict(on_disk)
            return False
        document.save()
        self.unsaved = False
        self.conflict = None
        return True

    def _enter_conflict(self, on_disk: str) -> None:
        if self.conflict and self.conflict["file_sha256"] == on_disk:
            return
        path = str(self.session.document.path)
        self.conflict = {
            "path": path,
            "file_sha256": on_disk,
            "message": f"{Path(path).name} changed on disk, and this window has edits the file does not contain.",
        }
        self.broadcast("document_conflict", self.conflict)

    def check_file(self) -> str | None:
        """Compares the file with the engine's last write and acts on it.
        Returns what it did: None, "reloaded", "conflict" or "unreadable"."""
        with self.lock:
            document = self.session.document
            on_disk = document.file_sha256()
            if on_disk is None or on_disk == document.saved_sha256:
                return None
            if self.unsaved:
                self._enter_conflict(on_disk)
                return "conflict"
            try:
                self.reload_from_disk()
            except DocumentError as exc:
                # Half written by an editor that does not write atomically, or
                # simply broken. The next change to the file is looked at again.
                print(f"not reloading {document.path}: {exc}", flush=True)
                return "unreadable"
            return "reloaded"

    def _note_references(self) -> None:
        document = self.session.document
        self.reference_digests = {feature_id: reference_digest(path) for feature_id, path in document.reference_sources()}
        self.watched_references = document.referenced_files()

    def watched_files(self) -> list[Path]:
        document = self.session.document
        return ([document.path] if document.path else []) + list(self.watched_references)

    def on_file_change(self, path: Path) -> str | None:
        if path == self.session.document.path:
            return self.check_file()
        return self.check_references()

    def check_references(self) -> str | None:
        """Rebuilds when a file a reference reads has changed: the board under
        an open case, regenerated by its script. Returns "rebuilt" or None.
        The document itself does not change, so nothing is saved or logged."""
        with self.lock:
            document = self.session.document
            changed = [
                (feature_id, path)
                for feature_id, path in document.reference_sources()
                if reference_digest(path) != self.reference_digests.get(feature_id)
            ]
            if not changed:
                return None
            self.session.invalidate()
            self.session.reference_rebuilds += 1
            self.announce_change(
                author="file",
                changes=[{"action": "reference_changed", "feature_id": feature_id, "file": path.name} for feature_id, path in changed],
            )
            return "rebuilt"

    def reload_from_disk(self) -> dict:
        """Takes the file's content as a new revision. The engine's previous
        state goes on the undo stack, so one undo brings it back."""
        document = self.session.document
        fresh = Document.load(document.path)
        self.undo_stack.append(document.snapshot())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        revision = document.restore(fresh.snapshot(), "file", action="reload")
        document.saved_sha256 = fresh.saved_sha256
        self.unsaved = False
        self.conflict = None
        return _commit(self, revision, save=False)

    def start_watching(self, interval: float = 0.25) -> None:
        if self._watcher is None:
            self._note_references()
            self._watcher = FileWatcher(self.watched_files, self.on_file_change, interval)
            self._watcher.start()

    # ---- running -----------------------------------------------------------

    def serve_forever(self) -> None:
        self._server = _Server(self)
        try:
            self._server.serve_forever()
        finally:
            self.shutdown()

    def start_in_background(self) -> None:
        """Starts the server on its own thread and returns once it is listening.

        The socket is bound before this returns, so a test or the Swift app can
        connect on the next line without polling for the file to appear.
        """
        self._server = _Server(self)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def shutdown(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        # A Unix socket is a file. Leaving it behind makes the next start fail
        # with "address already in use" against a socket nothing is listening on.
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    # The kernel's `sun_path` is 104 bytes on macOS and 108 on Linux, so the
    # tighter of the two is what to check against.
    MAX_SOCKET_PATH = 103

    def __init__(self, daemon: Daemon):
        self.daemon_ref = daemon
        path = daemon.socket_path
        # The socket defaults to sitting beside the document, and a document a
        # few folders deep is already past the limit. Bind reports that as a
        # bare "AF_UNIX path too long", naming neither the path nor the way out.
        length = len(str(path).encode())
        if length > self.MAX_SOCKET_PATH:
            raise DocumentError(
                f"the socket path is {length} bytes, over the {self.MAX_SOCKET_PATH} "
                f"a Unix socket allows: pass a shorter --socket ({path})"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bind fails outright on an existing path, and a leftover socket from a
        # killed daemon is the common case. Removing a live one is prevented by
        # the connect test rather than by the file's presence.
        if path.exists():
            if _is_live_socket(path):
                raise OSError(f"a daemon is already listening on {path}")
            path.unlink()
        super().__init__(str(path), _Handler)
        # Only this user's processes may drive the document.
        os.chmod(path, 0o600)


def _is_live_socket(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self._write_lock = threading.Lock()
        self.server.daemon_ref.add_client(self)

    def finish(self):
        self.server.daemon_ref.remove_client(self)
        super().finish()

    def send_line(self, message: str) -> None:
        # A broadcast comes from whichever thread made the edit, so two writes
        # can interleave mid-line and hand the client invalid JSON.
        with self._write_lock:
            self.wfile.write(message.encode() + b"\n")
            self.wfile.flush()

    def handle(self):
        daemon = self.server.daemon_ref
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            response = _handle_line(daemon, line)
            if response is not None:
                try:
                    self.send_line(json.dumps(response))
                except OSError:
                    return


def _handle_line(daemon: Daemon, line: bytes):
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, PARSE_ERROR, f"not valid JSON: {exc}")
    if not isinstance(request, dict):
        return _error(None, INVALID_REQUEST, "a request must be a JSON object")

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "'params' must be a JSON object")

    handler = METHODS.get(method)
    if handler is None:
        return _error(request_id, METHOD_NOT_FOUND, f"no method {method!r} (try list_methods)")

    try:
        with daemon.lock:
            result = handler(daemon, params)
    except StaleRevision as exc:
        return _error(
            request_id,
            STALE_REVISION,
            str(exc),
            data={"based_on": exc.based_on, "current": exc.current, "document": _document_state(daemon)},
        )
    except DocumentError as exc:
        return _error(request_id, DOCUMENT_ERROR, str(exc))
    except (TypeError, ValueError, KeyError) as exc:
        return _error(request_id, INVALID_PARAMS, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        # A geometry kernel failure must not take the daemon down with it: the
        # user still has a document open, and the next edit may well build.
        return _error(
            request_id,
            BUILD_ERROR,
            f"{type(exc).__name__}: {exc}",
            data={"traceback": traceback.format_exc(limit=6)},
        )

    if request_id is None:  # a notification, no reply expected
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


# ---- methods ---------------------------------------------------------------


def _document_state(daemon: Daemon) -> dict:
    document = daemon.session.document
    return {
        "path": str(document.path) if document.path else None,
        "revision": document.revision,
        "features": document.features,
        "rollback_index": document.rollback_index,
        "parameters": document.parameters,
        "appearances": document.appearances,
        "bodies": document.bodies,
    }


def _commit(daemon: Daemon, revision: int, save: bool = True, opened: bool = False) -> dict:
    """What every mutating method returns, and the side effects that go with it.

    It rebuilds before replying, and says which feature failed if one did. An
    edit that leaves the tip unbuildable is accepted on purpose, because
    editing an early sketch can break a later op and the user still needs to
    see the part up to that point. But an agent that got a bare success back
    had no way to tell a fillet that worked from one the kernel refused, and
    the next thing it measured was the part without it.

    Saving here rather than on a timer means a crashed app loses nothing an
    agent did, and the file on disk is always the revision the reply names.
    """
    session = daemon.session
    session.invalidate()
    if save and session.document.path:
        if daemon.autosave and daemon.conflict is None:
            daemon.save_document()
        else:
            daemon.unsaved = True
    _carry_selection(daemon)
    daemon.announce_change(opened=opened)

    try:
        shape, failed = session.shape()
    except Exception as exc:
        return {
            "revision": revision,
            "timeline": session.document.timeline(),
            "built": {"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
        }
    return {
        "revision": revision,
        "timeline": session.document.timeline(),
        "built": {
            "ok": failed is None,
            "failed_feature": failed,
            "volume": round(shape.volume, 9) if shape is not None else None,
        },
    }


def _get_document(daemon: Daemon, params: dict) -> dict:
    return _document_state(daemon)


def _file_path(params: dict) -> Path:
    """The document path a file method was given. Absolute, because the daemon's
    working directory is not something a caller can see, and `.sprocket`, because
    these methods write files and should not be a way to write any other kind."""
    raw = params.get("path")
    if not isinstance(raw, str) or not raw:
        raise DocumentError("this needs a 'path'")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DocumentError(f"{raw!r} is not an absolute path")
    if path.suffix != ".sprocket":
        raise DocumentError(f"{path.name!r} is not a .sprocket file")
    return path


def _switch_to(daemon: Daemon, document: Document) -> dict:
    """Makes `document` the open one. The undo history belonged to the old one,
    so it goes, and nothing is written: opening a file must not touch it."""
    daemon.session.use(document)
    daemon.undo_stack.clear()
    daemon.redo_stack.clear()
    daemon.unsaved = False
    daemon.conflict = None
    # A selection names faces and sketches of the old document. It is cleared
    # first so `document_changed`, which clients act on, stays the last word.
    daemon.selection = empty_selection()
    daemon.selection_keys = {}
    daemon.broadcast("selection_changed", _selection_view(daemon))
    return {"path": str(document.path), **_commit(daemon, document.revision, save=False, opened=True)}


def _open_document(daemon: Daemon, params: dict) -> dict:
    """Replaces the open document with the one at `path`. A file that will not
    load is refused and the open document is left as it was."""
    return _switch_to(daemon, Document.load(_file_path(params)))


def _new_document(daemon: Daemon, params: dict) -> dict:
    """An empty document saved at `path`. An existing file is refused rather
    than overwritten, because the caller picked a name, not a replacement."""
    path = _file_path(params)
    if path.exists():
        raise DocumentError(f"{path} already exists")
    document = Document(path=path)
    document.save()
    return _switch_to(daemon, document)


def _save_as(daemon: Daemon, params: dict) -> dict:
    """Writes the open document to `path`. Edits are saved there from now on."""
    document = daemon.session.document
    path = document.save(_file_path(params))
    daemon.unsaved = False
    daemon.conflict = None
    daemon.session.moved()
    daemon.announce_change()
    return {"path": str(path), "revision": document.revision}


def _get_timeline(daemon: Daemon, params: dict) -> dict:
    document = daemon.session.document
    _, failed = daemon.session.shape() if params.get("build", False) else (None, None)
    return {
        "revision": document.revision,
        "rollback_index": document.rollback_index,
        "timeline": document.timeline(),
        "failed_feature": failed,
    }


UNDO_LIMIT = 100


def _edit(daemon: Daemon, edits, params: dict) -> dict:
    """Applies edits, and makes them undoable unless they only move the rollback bar.

    Scrubbing the timeline is navigation. Undoing it would leave the user
    wondering what Cmd+Z just did to the part.
    """
    document = daemon.session.document
    before = document.snapshot()
    revision = document.apply_edits(
        edits, params.get("base_revision"), params.get("author", "agent:unknown")
    )
    if any(isinstance(edit, dict) and edit.get("op") != "set_rollback" for edit in edits):
        daemon.undo_stack.append(before)
        del daemon.undo_stack[:-UNDO_LIMIT]
        daemon.redo_stack.clear()
    result = _commit(daemon, revision)
    # Fields whose "=param" expression a new number replaced, for the viewer to say so.
    unlinked = [item for entry in document.log[-len(edits):] for item in entry.get("unlinked", [])]
    if unlinked:
        result["unlinked"] = unlinked
    return result


def _apply_edit(daemon: Daemon, params: dict) -> dict:
    edits = params.get("edits")
    if edits is None and "edit" in params:
        edits = [params["edit"]]
    return _edit(daemon, edits, params)


def _set_rollback(daemon: Daemon, params: dict) -> dict:
    return _edit(daemon, [{"op": "set_rollback", "index": params.get("index")}], params)


def _set_parameter(daemon: Daemon, params: dict) -> dict:
    return _edit(
        daemon, [{"op": "set_parameter", "name": params.get("name"), "value": params.get("value")}], params
    )


def _set_visibility(daemon: Daemon, params: dict) -> dict:
    """Shows or hides a body. Undoable, but not an edit: no log entry, no
    timeline row. Hidden bodies still export."""
    document = daemon.session.document
    before = document.snapshot()
    before["view_only"] = True
    if params.get("base_revision") is not None and params["base_revision"] != document.revision:
        raise StaleRevision(params["base_revision"], document.revision)
    revision = document.set_visibility(params.get("id"), params.get("visible"))
    daemon.undo_stack.append(before)
    del daemon.undo_stack[:-UNDO_LIMIT]
    daemon.redo_stack.clear()
    return _commit(daemon, revision)


def _set_body(daemon: Daemon, params: dict) -> dict:
    """Name, colour and material are a logged edit. `visible`, when given, goes through
    `set_visibility` first, so it stays view state."""
    result = None
    if "visible" in params:
        result = _set_visibility(daemon, {"id": params.get("id"), "visible": params["visible"], "base_revision": params.get("base_revision")})
    changes = {name: params[name] for name in ("name", "color", "material") if name in params}
    if changes:
        based = {} if result is None else {"base_revision": None}
        result = _edit(daemon, [{"op": "set_body", "id": params.get("id"), **changes}], {**params, **based})
    if result is None:
        raise DocumentError("set_body needs name, color, material or visible")
    return result


def _set_appearances(daemon: Daemon, params: dict) -> dict:
    return _edit(daemon, [{"op": "set_appearances", "appearances": params.get("appearances")}], params)


def _get_face_appearance(daemon: Daemon, params: dict) -> dict:
    """What colours one face, and a selector that picks out just that face.

    `entries` are the appearances that reach the face, with how many faces
    each one colours, so a client can tell an entry made for this face from a
    wider one. `selector` is a `within` box around the face's centre, checked
    against the current build to match this face alone. Appearances resolve
    without a replay context, so a box is the selector that works there, and
    it survives any rebuild that leaves the face where it is."""
    handle = params.get("handle")
    if not isinstance(handle, str) or not handle.startswith("f"):
        raise DocumentError("'handle' must be a face handle like 'f3'")
    shape = daemon.session.require_shape()
    try:
        face = _resolve_handle(shape, handle)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    entries = []
    for index, appearance in enumerate(daemon.session.document.appearances):
        try:
            picked = resolve_selector(shape, appearance["select"])
        except SelectorError:
            continue
        if any(item.is_same(face) for item in picked):
            entries.append({"index": index, "color": appearance["color"], "material": appearance.get("material"), "faces": len(picked)})
    centre = list(face.center())
    selector = None
    for half in (0.05, 0.001):
        box = [round(value - half, 6) for value in centre] + [round(value + half, 6) for value in centre]
        candidate = {"select": [{"op": "faces"}, {"op": "within", "box": box}]}
        picked = resolve_selector(shape, candidate)
        if len(picked) == 1 and picked[0].is_same(face):
            selector = candidate
            break
    if selector is None:
        raise DocumentError(f"no box around {handle}'s centre picks it out alone: another face shares that centre")
    return {"handle": handle, "centre": [round(value, 6) for value in centre], "entries": entries, "selector": selector}


def _get_bodies(daemon: Daemon, params: dict) -> dict:
    """The built bodies in order, with their settings, volume and face count."""
    return {
        "bodies": [
            {
                "id": body["id"],
                "name": body["name"],
                "color": body["color"],
                "material": body["material"],
                "visible": body["visible"],
                "reference": body["reference"],
                "tip": body["tip"],
                "volume": round(body["shape"].volume, 6),
                "faces": len(body["shape"].faces()),
            }
            for body in daemon.session.bodies()
        ]
    }


def _check_clearance(daemon: Daemon, params: dict) -> dict:
    """The gap between each part body and each reference body: the smallest
    distance, and the volume they share. A distance of 0 with no shared volume
    means they touch."""
    from build123d import ShapeList

    bodies = daemon.session.bodies()
    parts = [body for body in bodies if not body["reference"]]
    references = [body for body in bodies if body["reference"]]
    if not references:
        raise DocumentError("the document has no reference body to check against")
    pairs = []
    for part in parts:
        for reference in references:
            shared = part["shape"].intersect(reference["shape"])
            pieces = list(shared) if isinstance(shared, ShapeList) else [shared] if shared is not None else []
            overlap = sum(piece.volume for piece in pieces)
            pairs.append({
                "body": part["id"],
                "reference": reference["id"],
                "distance": round(part["shape"].distance_to(reference["shape"]), 6),
                "overlap_volume": round(overlap, 6),
            })
    return {"pairs": pairs}


SKETCH_EDIT_FIELDS = {
    "add_constraint": ("constraint",),
    "update_constraint": ("constraint_id", "changes"),
    "remove_constraint": ("constraint_id",),
    "add_entity": ("entity",),
    "update_entity": ("entity_id", "changes"),
    "remove_entity": ("entity_id",),
}


def _sketch_method(action: str):
    """A daemon method that does one `edit_sketch` action on the sketch named by `feature`.

    The reply carries the solved sketch, so a caller sees where the solver put
    everything without a second request.
    """

    def method(daemon: Daemon, params: dict) -> dict:
        edit = {"op": "edit_sketch", "action": action, "id": params.get("feature")}
        edit.update({field: params[field] for field in SKETCH_EDIT_FIELDS[action] if field in params})
        result = _edit(daemon, [edit], params)
        document = daemon.session.document
        entry = document.log[-1]
        sketch = document.features[entry["index"]]
        detail = {key: entry[key] for key in ("constraint_id", "entity_id", "dof", "redundant") if key in entry}
        return {**result, **detail, "sketch": {"entities": sketch["entities"], "constraints": sketch["constraints"]}}

    return method


def _step_history(daemon: Daemon, take_from: list, put_on: list, params: dict) -> dict:
    document = daemon.session.document
    if not take_from:
        return {"revision": document.revision, "changed": False}
    snapshot = take_from.pop()
    # A view change (set_visibility) undoes and redoes without a log entry.
    view_only = snapshot.get("view_only", False)
    current = document.snapshot()
    if view_only:
        current["view_only"] = True
    put_on.append(current)
    revision = document.restore(snapshot, params.get("author", "agent:unknown"), log=not view_only)
    return {**_commit(daemon, revision), "changed": True}


def _undo(daemon: Daemon, params: dict) -> dict:
    return _step_history(daemon, daemon.undo_stack, daemon.redo_stack, params)


def _redo(daemon: Daemon, params: dict) -> dict:
    return _step_history(daemon, daemon.redo_stack, daemon.undo_stack, params)


def _preview_feature(daemon: Daemon, params: dict) -> dict:
    """Builds a feature against a copy and throws the copy away.

    This is what lets an agent check a guess before it touches the user's
    document. The alternative it would otherwise reach for is apply-then-undo,
    which puts a wrong part on the user's screen and two entries in the log.

    Unlike every other build path this one does not tolerate a broken tip. A
    preview whose feature failed would otherwise come back carrying the volume
    of the part without it, and a number beside `would_build: false` is a
    number somebody reads as the answer. On failure it returns the reason and
    no measurements at all.
    """
    document = daemon.session.document
    trial = Document(
        document.features, document.rollback_index, document.parameters, document.revision, []
    )
    index = params.get("index", trial.rollback_index)
    trial.apply_edits(
        [
            {"op": "insert", "feature": params.get("feature"), "index": index},
            {"op": "set_rollback", "index": index + 1},
        ],
        None,
        "preview",
    )

    try:
        shape = replay(trial.for_replay(), cache_dir=daemon.session.cache_dir)
    except Exception as exc:
        return {
            "would_build": False,
            "failed_feature": getattr(exc, "feature_id", None),
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if shape is None:
        return {"would_build": False, "reason": "the previewed feature built nothing"}
    return {
        "would_build": True,
        "volume": round(shape.volume, 9),
        "area": round(shape.area, 9),
        "counts": {
            "faces": len(shape.faces()),
            "edges": len(shape.edges()),
            "solids": len(shape.solids()),
        },
    }


def _get_profiles(daemon: Daemon, params: dict) -> dict:
    """The regions of one sketch that an extrude can pick, each named by the
    entity ids that bound it. An agent reads this to write `profiles`."""
    feature_id = params.get("feature")
    document = daemon.session.document
    feature = document.for_replay()["features"][document.index_of(feature_id)]
    if feature.get("type") != "sketch":
        raise DocumentError(f"feature {feature_id!r} is a {feature.get('type')}, not a sketch")
    return {"feature": feature_id, "profiles": sketch_profiles(feature)}


def _resolve_plane(daemon: Daemon, params: dict) -> dict:
    """Where a sketch plane reference lands on the current build, as an explicit
    origin, normal and x_dir. An agent checks a face plane with this before it
    draws on it."""
    from .ops.sketch import plane_json, resolve_plane

    plane = params.get("plane")
    if plane is None:
        raise DocumentError("resolve_plane needs 'plane'")
    try:
        return {"plane": plane_json(resolve_plane(plane, daemon.session.context()))}
    except (KeyError, ValueError, SelectorError) as exc:
        raise DocumentError(f"plane does not resolve: {exc}") from None


def _describe_geometry(daemon: Daemon, params: dict) -> dict:
    return {**describe(daemon.session.require_shape()), "selection": _selection_view(daemon)}


# ---- selection -------------------------------------------------------------
#
# The person's selection lives here, not in the viewer, so an agent can read it:
# "fillet these" has nothing to act on otherwise. The viewer sends it on every
# pick. It holds handles from describe_geometry (`f2`, `e7`) and sketch ids, and
# the revision the handles were taken at, because a rebuild renumbers faces and
# edges.

_HANDLE = re.compile(r"[fe]\d+")


def _handles(params: dict) -> list[str]:
    handles = params.get("handles", [])
    if not isinstance(handles, list) or not all(isinstance(item, str) and _HANDLE.fullmatch(item) for item in handles):
        raise DocumentError("'handles' must be a list like ['f2', 'e7']")
    return handles


def _sketch_ids(daemon: Daemon, params: dict) -> list[str]:
    ids = params.get("sketches", [])
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise DocumentError("'sketches' must be a list of sketch feature ids")
    sketches = {feature["id"] for feature in daemon.session.document.features if feature.get("type") == "sketch"}
    return [item for item in ids if item in sketches]


def _selection_view(daemon: Daemon) -> dict:
    """The selection with `describe_geometry`'s entry for each handle: the sentence,
    and the centre, length or area an agent builds a selector from.

    `stale` is true when the part was rebuilt since the handles were taken.
    The sentences are left out then, because `f2` may be a different face now.
    """
    document = daemon.session.document
    selection = daemon.selection
    view = {**selection, "stale": bool(selection["handles"]) and selection["revision"] != document.revision}
    if selection["handles"] and not view["stale"]:
        shape, _ = daemon.session.shape()
        if shape is not None:
            described = describe(shape)
            items = {item["handle"]: item for item in described["faces"] + described["edges"]}
            view["descriptions"] = [items.get(handle, {"handle": handle, "description": None}) for handle in selection["handles"]]
    return view


def _set_selection(daemon: Daemon, params: dict) -> dict:
    """What the viewer calls on every pick. `revision` is the revision the
    handles were picked at, and defaults to the current one."""
    revision = params.get("revision", daemon.session.document.revision)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise DocumentError("'revision' must be an integer")
    daemon.selection = {"handles": _handles(params), "sketches": _sketch_ids(daemon, params), "revision": revision}
    daemon.selection_keys = _selection_keys(daemon) if revision == daemon.session.document.revision else {}
    view = _selection_view(daemon)
    daemon.broadcast("selection_changed", view)
    return view


def _get_selection(daemon: Daemon, params: dict) -> dict:
    return _selection_view(daemon)


def _geometry_key(item) -> tuple:
    """Where a face or edge is and how big: what stays the same when a rebuild
    renumbers it but leaves it in place. Rounded to 0.1 µm."""
    centre = tuple(round(value, 4) for value in item.center())
    size = item.area if hasattr(item, "area") and item.area else item.length
    return (*centre, round(size, 4))


def _selection_keys(daemon: Daemon) -> dict[str, tuple]:
    handles = daemon.selection["handles"]
    if not handles:
        return {}
    shape, _ = daemon.session.shape()
    if shape is None:
        return {}
    keys = {}
    for handle in handles:
        try:
            keys[handle] = _geometry_key(_resolve_handle(shape, handle))
        except ValueError:
            pass
    return keys


def _carry_selection(daemon: Daemon) -> None:
    """Keeps the person's selection across a rebuild: each picked face or edge
    is looked up again by position and size, and keeps its place in the
    selection under its new handle. One that is gone, or no longer unique,
    drops out. Runs before `document_changed` goes out, so a viewer that asks
    for the selection after it reloads gets the carried one."""
    selection = daemon.selection
    revision = daemon.session.document.revision
    if not selection["handles"] or selection["revision"] == revision:
        return
    keys = daemon.selection_keys
    shape, _ = daemon.session.shape() if keys else (None, None)
    found: dict[tuple, list[str]] = {}
    if shape is not None:
        for kind, items in (("f", shape.faces()), ("e", shape.edges())):
            wanted = {key for handle, key in keys.items() if handle[0] == kind}
            for index, item in enumerate(items if wanted else []):
                key = _geometry_key(item)
                if key in wanted:
                    found.setdefault((kind, key), []).append(f"{kind}{index}")
    carried_keys = {}
    for handle in selection["handles"]:
        matches = found.get((handle[0], keys.get(handle)), [])
        if len(matches) == 1:
            carried_keys[matches[0]] = keys[handle]
    feature_ids = {feature.get("id") for feature in daemon.session.document.features}
    sketches = [item for item in selection["sketches"] if item in feature_ids]
    daemon.selection = {"handles": list(carried_keys), "sketches": sketches, "revision": revision}
    daemon.selection_keys = carried_keys
    daemon.broadcast("selection_changed", _selection_view(daemon))


def _resolve_conflict(daemon: Daemon, params: dict) -> dict:
    """Ends a conflict between the engine's edits and a file changed on disk.
    `keep: "file"` reloads the file, and the engine's state goes on the undo
    stack. `keep: "mine"` writes the engine's state over the file."""
    keep = params.get("keep")
    if keep not in ("file", "mine"):
        raise DocumentError("'keep' must be \"file\" or \"mine\"")
    if daemon.conflict is None and not daemon.unsaved:
        return {"revision": daemon.session.document.revision, "changed": False}
    if keep == "file":
        result = daemon.reload_from_disk()
    else:
        daemon.save_document(force=True)
        daemon.announce_change()
        result = {"revision": daemon.session.document.revision}
    daemon.broadcast("document_conflict", {"path": str(daemon.session.document.path), "resolved": keep})
    return {**result, "changed": True}


def _get_file_status(daemon: Daemon, params: dict) -> dict:
    document = daemon.session.document
    return {
        "path": str(document.path) if document.path else None,
        "revision": document.revision,
        "unsaved": daemon.unsaved,
        "conflict": daemon.conflict,
        "changed_on_disk": document.path is not None and document.file_sha256() not in (None, document.saved_sha256),
    }


class FileWatcher:
    """Polls some files' stat and calls `on_change(path)` once one has changed
    and then held still for one interval, so a writer that is not atomic is
    read after it finishes. Polling, not FSEvents: stdlib only, it survives an
    atomic rename (a new inode), and a few stat calls a second cost nothing.
    `paths_of()` is asked every interval, so the set can change: Save As,
    Open, or a reference added."""

    def __init__(self, paths_of, on_change, interval: float = 0.25):
        self.paths_of = paths_of
        self.on_change = on_change
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _stat(path):
        try:
            info = os.stat(path)
            return (info.st_ino, info.st_size, info.st_mtime_ns)
        except (OSError, TypeError):
            return None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        seen = {path: self._stat(path) for path in self.paths_of()}
        pending: dict = {}
        while not self._stop.wait(self.interval):
            paths = list(self.paths_of())
            for path in paths:
                if path not in seen:  # newly watched: start from what is there
                    seen[path] = self._stat(path)
                    continue
                now = self._stat(path)
                if now == seen[path]:
                    pending.pop(path, None)
                    continue
                if pending.get(path) != now:  # still moving: wait one more interval
                    pending[path] = now
                    continue
                seen[path] = now
                pending.pop(path, None)
                try:
                    self.on_change(path)
                except Exception as exc:  # the watcher must outlive one bad read
                    print(f"file watcher: {type(exc).__name__}: {exc}", flush=True)
            for gone in set(seen) - set(paths):
                del seen[gone]
                pending.pop(gone, None)


def _resolve_selection(daemon: Daemon, params: dict) -> dict:
    """Turns picked handles into a selector that survives upstream edits.

    `handles` defaults to the person's current selection. The reply holds the
    selector to paste into a fillet's `edges`, with the `expected_count` that
    makes a rebuild fail loudly instead of rounding the wrong edges, and the
    `strategy` that found it: `new_since` and `structure` follow the geometry,
    and `position` only follows where it is, so it can move when a dimension does.
    """
    if params.get("handles") is None:
        selection = _selection_view(daemon)
        if selection["stale"]:
            raise DocumentError("the selection is stale: the part was rebuilt since it was picked")
        handles = selection["handles"]
    else:
        handles = _handles(params)
    shape = daemon.session.require_shape()
    replayable = daemon.session.document.for_replay()
    features = replayable["features"]
    tip = last_solid_index(features, replayable["rollback_index"])
    _, ctx = replay({"features": features, "rollback_index": tip}, cache_dir=daemon.session.cache_dir, return_context=True)
    try:
        return synthesize(shape, ctx, handles, [feature["id"] for feature in features[:tip] if feature.get("type") != "sketch"])
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None


def _face_sketch_plane(daemon: Daemon, params: dict) -> dict:
    """A sketch plane that follows a picked face: `{"feature", "face"}` for a
    new sketch's `plane`, and where it lands now.

    The face is looked up in the body that owns it, and the selector is
    synthesised against that body's tip, which is what `resolve_plane` searches.
    A face of the combined part is not a face of any one feature's result when
    the document has several bodies."""
    from .ops.sketch import plane_json, resolve_plane

    handle = params.get("handle")
    if not isinstance(handle, str) or not handle.startswith("f"):
        raise DocumentError("'handle' must be a face handle like 'f3'")
    shape = daemon.session.require_shape()
    try:
        face = _resolve_handle(shape, handle)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    ctx = daemon.session.context()
    for body in daemon.session.bodies():
        if body["reference"]:
            continue
        faces = body["shape"].faces()
        local = next((index for index, item in enumerate(faces) if item.is_same(face)), None)
        if local is not None:
            break
    else:
        raise DocumentError(f"{handle} is not on a part body")
    features = daemon.session.document.for_replay()["features"]
    tip_index = next(index for index, feature in enumerate(features) if feature["id"] == body["tip"])
    candidates = [feature["id"] for feature in features[: tip_index + 1] if feature.get("type") != "sketch"]
    try:
        found = synthesize(body["shape"], ctx, [f"f{local}"], candidates)
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None
    reference = {"feature": body["tip"], "face": found["selector"]}
    try:
        plane = resolve_plane(reference, ctx)
    except (ValueError, SelectorError) as exc:
        raise DocumentError(f"cannot sketch on {handle}: {exc}") from None
    # The plane must hold the picked face, or the selector found another one.
    if abs((face.center() - plane.origin).dot(plane.z_dir)) > 1e-6:
        raise DocumentError(f"the selector for {handle} lands on another face")
    return {"plane": reference, "resolved": plane_json(plane), "strategy": found["strategy"]}


def _highlight(daemon: Daemon, params: dict) -> dict:
    """Shows handles in the viewer, in a colour that is not the selection's.
    An empty list clears it. It does not change what is selected."""
    handles = _handles(params)
    if handles:
        shape = daemon.session.require_shape()
        for handle in handles:
            _resolve_handle(shape, handle)
    sketches = _sketch_ids(daemon, params)
    daemon.broadcast("highlight", {"handles": handles, "sketches": sketches})
    return {"handles": handles, "sketches": sketches}


def _measure(daemon: Daemon, params: dict) -> dict:
    return measure(daemon.session.require_shape(), params.get("handles"))


def _render_view(daemon: Daemon, params: dict) -> dict:
    path = params.get("path")
    if not path:
        raise DocumentError("render_view needs a 'path' to write the PNG to")
    return render_png(
        daemon.session.require_shape(),
        Path(path),
        camera=params.get("camera", "iso"),
        width=params.get("width", 640),
        height=params.get("height", 480),
        highlight=params.get("highlight"),
    )


def _export(daemon: Daemon, params: dict) -> dict:
    """STEP, STL and the viewer's mesh, whichever paths are given."""
    from .export import ExportError, write_3mf, write_step, write_stl, write_stl_dir
    from .mesh import export_mesh

    shape = daemon.session.require_shape()
    bodies = daemon.session.bodies()
    written = {}
    try:
        if params.get("step"):
            write_step(shape, params["step"], bodies)
            written["step"] = params["step"]
        if params.get("stl"):
            write_stl(shape, params["stl"])
            written["stl"] = params["stl"]
        if params.get("stl_dir"):
            written["stl_dir"] = [str(path) for path in write_stl_dir(bodies, params["stl_dir"])]
        if params.get("3mf"):
            write_3mf(bodies, params["3mf"])
            written["3mf"] = params["3mf"]
        if params.get("mesh"):
            written["mesh"] = params["mesh"]
            written["mesh_counts"] = export_mesh(
                shape, Path(params["mesh"]), daemon.session.document.appearances, daemon.session.bodies()
            )["counts"]
    except ExportError as exc:
        raise DocumentError(str(exc)) from None
    except OSError as exc:
        raise DocumentError(f"cannot write {exc.filename}: {exc.strerror}") from None
    if not written:
        raise DocumentError("export needs at least one of 'step', 'stl', 'stl_dir', '3mf' or 'mesh'")
    return written


def _get_schemas(daemon: Daemon, params: dict) -> dict:
    """Every op's JSON Schema, which is how an agent learns what it can build.

    Generated from the registry rather than written out, so an op added in M3
    is agent-callable the moment it registers.
    """
    return {name: op.schema for name, op in sorted(REGISTRY.items())}


def _validate_feature(daemon: Daemon, params: dict) -> dict:
    resolved = validate_feature(params.get("feature"), daemon.session.document.resolved_parameters())
    return {"valid": True, "resolved": resolved}


def _list_methods(daemon: Daemon, params: dict) -> dict:
    return {"methods": sorted(METHODS)}


def _ping(daemon: Daemon, params: dict) -> dict:
    return {"ok": True, "revision": daemon.session.document.revision}


METHODS = {
    "get_document": _get_document,
    "open_document": _open_document,
    "new_document": _new_document,
    "save_as": _save_as,
    "get_timeline": _get_timeline,
    "apply_edit": _apply_edit,
    "set_rollback": _set_rollback,
    "set_parameter": _set_parameter,
    **{action: _sketch_method(action) for action in SKETCH_EDIT_FIELDS},
    "undo": _undo,
    "redo": _redo,
    "preview_feature": _preview_feature,
    "get_profiles": _get_profiles,
    "describe_geometry": _describe_geometry,
    "resolve_plane": _resolve_plane,
    "get_bodies": _get_bodies,
    "check_clearance": _check_clearance,
    "set_body": _set_body,
    "set_visibility": _set_visibility,
    "set_appearances": _set_appearances,
    "get_face_appearance": _get_face_appearance,
    "set_selection": _set_selection,
    "get_selection": _get_selection,
    "resolve_conflict": _resolve_conflict,
    "get_file_status": _get_file_status,
    "resolve_selection": _resolve_selection,
    "face_sketch_plane": _face_sketch_plane,
    "highlight": _highlight,
    "measure": _measure,
    "render_view": _render_view,
    "export": _export,
    "get_schemas": _get_schemas,
    "validate_feature": _validate_feature,
    "list_methods": _list_methods,
    "ping": _ping,
}


# ---- client ----------------------------------------------------------------


class RpcError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


class Client:
    """A blocking client for the daemon. Used by the MCP server and the tests.

    Notifications arrive on the same socket as replies, interleaved with them,
    so reading for a reply skips anything without an id and hands it to
    `on_notification`. A client that ignored them would eventually read one as
    the answer to its own question.
    """

    def __init__(self, socket_path: Path, on_notification=None):
        self.socket_path = Path(socket_path)
        self.on_notification = on_notification
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(str(self.socket_path))
        self._file = self._socket.makefile("rwb")
        self._next_id = 0
        self._lock = threading.Lock()

    def call(self, method: str, **params):
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._file.write(
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode()
                + b"\n"
            )
            self._file.flush()
            while True:
                line = self._file.readline()
                if not line:
                    raise RpcError(INTERNAL_ERROR, "the daemon closed the connection")
                message = json.loads(line)
                if message.get("id") != request_id:
                    if self.on_notification and "method" in message:
                        self.on_notification(message["method"], message.get("params", {}))
                    continue
                if "error" in message:
                    error = message["error"]
                    raise RpcError(error["code"], error["message"], error.get("data"))
                return message["result"]

    def close(self) -> None:
        self._file.close()
        self._socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
