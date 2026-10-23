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

import io
import json
import re
import socket
import socketserver
import sys
import threading
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict, final, override

from build123d import Compound, Edge, Face, Solid

from sprocket_engine.arrays import float_list, int_list
from sprocket_engine.b3d import distance_between, intersect, is_same
from sprocket_engine.context import Body, Context
from sprocket_engine.document import (
    Document,
    DocumentError,
    Snapshot,
    StaleRevisionError,
    reference_digest,
    validate_feature,
)
from sprocket_engine.export import (
    ExportError,
    section_of,
    write_3mf,
    write_section,
    write_step,
    write_stl,
    write_stl_dir,
)
from sprocket_engine.geometry import Measurement, describe, measure, resolve_handle
from sprocket_engine.mesh import export_mesh, tessellate
from sprocket_engine.model import (
    JSON,
    EdgeAxis,
    FaceAxis,
    FacePlane,
    JSONObject,
    parse_json,
    sketch_feature,
)
from sprocket_engine.ops import REGISTRY
from sprocket_engine.ops.construct import (
    CONSTRUCT_TYPES,
    construct_feature,
    construct_geometry,
    resolve_axis,
)
from sprocket_engine.ops.sketch import (
    plane_json,
    plane_ref,
    resolve_plane,
    sketch_profiles,
)
from sprocket_engine.render import render_png
from sprocket_engine.replay import (
    NOT_SOLIDS,
    build_shape,
    failed_feature,
    last_solid_index,
    replay,
    with_settings,
)
from sprocket_engine.selectors import (
    SelectorError,
    parse_selector,
    resolve_selector,
    selector_json,
)
from sprocket_engine.sketch_reference import parse_reference, projected_json, projector
from sprocket_engine.synthesis import synthesize
from sprocket_engine.viewer_files import write_viewer_files

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application errors, in JSON-RPC's reserved-for-the-server range.
DOCUMENT_ERROR = -32000
STALE_REVISION = -32001
BUILD_ERROR = -32002

# What a method replies: an object json.dumps can write. A Mapping, so a
# TypedDict reply fits it as well as a plain dict.
type Reply = Mapping[str, object]
# A reply being built: keys added as the method goes.
type ReplyDict = dict[str, object]


def say(message: str) -> None:
    """One line on the daemon's stdout, where the app and a terminal read its log."""
    _ = sys.stdout.write(f"{message}\n")
    _ = sys.stdout.flush()


class Listener(Protocol):
    """A connected client: anything the daemon can send a notification line to."""

    def send_line(self, message: str) -> None:
        """Send one JSON line."""
        ...


class Session:
    """The live document, its built shape, and the policy for rebuilding it.

    The shape is memoised against the revision rather than rebuilt per call.
    `describe_geometry` then `measure` then `render_view` is the normal agent
    loop, and replaying a fillet three times to answer three questions about
    one unchanged document is the cost this avoids.
    """

    def __init__(self, document: Document, cache_dir: Path | None = None) -> None:
        """Hold `document`, building into `cache_dir` or the default beside it."""
        self.document: Document = document
        self._chosen_cache_dir: Path | None = Path(cache_dir) if cache_dir else None
        self.cache_dir: Path = self._chosen_cache_dir or self._default_cache_dir(
            document
        )
        self._shape: Compound | None = None
        self._failed_feature: str | None = None
        self._context: Context | None = None
        self._built_revision: int | None = None
        # Counts the documents this session has held. A revision number is
        # only unique within one document.
        self.generation: int = 0
        # Counts rebuilds caused by a referenced file changing, which leave
        # the revision as it was. A cache keyed on the revision needs it too.
        self.reference_rebuilds: int = 0

    def use(self, document: Document) -> None:
        """Switches to another document. A cache directory chosen at start-up
        stays, and otherwise the cache follows the document's folder.
        """
        self.document = document
        self.generation += 1
        self.cache_dir = self._chosen_cache_dir or self._default_cache_dir(document)
        self.invalidate()

    def moved(self) -> None:
        """The document was saved under a new path, so the default cache moves with it."""
        self.cache_dir = self._chosen_cache_dir or self._default_cache_dir(
            self.document
        )

    @staticmethod
    def _default_cache_dir(document: Document) -> Path:
        return (
            (document.path.parent / "cache") if document.path else Path.cwd() / "cache"
        )

    def invalidate(self) -> None:
        """Forget the build, so the next read rebuilds."""
        self._built_revision = None

    def shape(self) -> tuple[Compound | None, str | None]:
        """Returns (shape, failed_feature_id). The shape is None for an empty
        document, which is a valid state and not an error.
        """
        if self._built_revision != self.document.revision:
            replayable = self.document.for_replay()
            self._shape, self._failed_feature, context = build_shape(
                replayable["features"], self.document.rollback_index, self.cache_dir
            )
            self._context = context
            self._built_revision = self.document.revision
            self.document.sync_references(context.features)
        return self._shape, self._failed_feature

    def context(self) -> Context:
        """The replay context of the current build: every feature's result."""
        _ = self.shape()
        return self._context if self._context is not None else Context()

    def bodies(self) -> list[Body]:
        """The built bodies with the document's settings for each."""
        return with_settings(self.context().bodies, {**self.document.bodies})

    def require_shape(self) -> Compound:
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
            raise DocumentError(
                "this document builds to nothing, so there is no geometry yet"
            )
        return shape


@final
class Selection(TypedDict):
    """What the person has picked: handles, sketch ids, and the revision they were taken at."""

    handles: list[str]
    sketches: list[str]
    revision: int | None


@final
class Conflict(TypedDict):
    """A file changed on disk while the engine held edits it does not contain."""

    path: str
    file_sha256: str
    message: str


@dataclass
class HistoryStep:
    """One undo or redo step. A view change (set_visibility) has no log entry."""

    snapshot: Snapshot
    view_only: bool = False


# Where a face or edge is and how big: x, y, z and area or length.
type GeometryKey = tuple[float, float, float, float]


def empty_selection() -> Selection:
    """Nothing picked."""
    return {"handles": [], "sketches": [], "revision": None}


class Daemon:
    """Owns the socket, the session lock and the connected clients."""

    def __init__(
        self,
        session: Session,
        socket_path: Path,
        *,
        autosave: bool = True,
        viewer_dir: Path | None = None,
        recovery_path: Path | None = None,
    ) -> None:
        """Serve `session` on `socket_path`. Nothing listens until a start call."""
        self.session: Session = session
        # When set, the viewer's files are rewritten after every change (see
        # viewer_files.py). The Swift app reads them off disk.
        self.viewer_dir: Path | None = Path(viewer_dir) if viewer_dir else None
        self.socket_path: Path = Path(socket_path)
        self.autosave: bool = autosave
        # With autosave off, unsaved edits are copied here after every change and
        # the file is deleted once they are saved, so a crash loses nothing. The
        # app offers what it finds on its next launch (see `restore_recovery`).
        self.recovery_path: Path | None = Path(recovery_path) if recovery_path else None
        self.lock: threading.RLock = threading.RLock()
        # Snapshots of the editable state, one per undoable edit. Shared by
        # every client, so an undo undoes the newest edit whoever made it.
        self.undo_stack: list[HistoryStep] = []
        self.redo_stack: list[HistoryStep] = []
        # What the person has picked in the viewer. See `_set_selection`.
        self.selection: Selection = empty_selection()
        # A geometric key per selected handle, so a rebuild can find the same
        # face or edge again under its new number. See `_carry_selection`.
        self.selection_keys: dict[str, GeometryKey] = {}
        # The engine holds edits the file does not: autosave is off, or a save
        # was held back because the file changed under it.
        self.unsaved: bool = False
        # The file changed on disk while there were unsaved edits. Nothing is
        # saved or reloaded until a client calls `resolve_conflict`.
        self.conflict: Conflict | None = None
        self._watcher: FileWatcher | None = None
        # Each reference feature's digest at the last build (see
        # document.reference_digest), and the files those digests read.
        self.reference_digests: dict[str | None, str | None] = {}
        self.watched_references: list[Path] = []
        self._clients: set[Listener] = set()
        self._clients_lock: threading.Lock = threading.Lock()
        self._server: _Server | None = None

    # ---- client registry ---------------------------------------------------

    def add_client(self, handler: Listener) -> None:
        """Register a connection to hear notifications."""
        with self._clients_lock:
            self._clients.add(handler)

    def remove_client(self, handler: Listener) -> None:
        """Stop sending notifications to a connection."""
        with self._clients_lock:
            self._clients.discard(handler)

    def broadcast(self, method: str, params: Reply) -> None:
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
        or None, so a change notification can carry it to the viewer.
        """
        if self.viewer_dir is None:
            return None
        try:
            error = write_viewer_files(self.session, self.viewer_dir)
        except Exception as exc:
            # A document that will not build must not stop the edit that
            # caused it. The viewer keeps the files from the last good build.
            error = f"{type(exc).__name__}: {exc}"
        if error:
            say(f"viewer files not written: {error}")
        return error

    def announce_change(
        self,
        *,
        opened: bool = False,
        author: str | None = None,
        changes: list[JSONObject] | None = None,
    ) -> None:
        """`opened` says the document was replaced by another, not edited. A
        viewer then drops its selection and refits the camera, and cannot
        trust that an unchanged revision number means an unchanged part.

        `author` and `changes` come off the log entries of this revision, so a
        viewer can say who moved the part: `viewer`, `agent:...`, or `file`
        for a reload from disk. A view change has no entry, so no author.
        """
        viewer_error = self.publish_viewer_files()
        document = self.session.document
        self._note_references()
        self.write_recovery()
        entries: list[JSONObject] = []
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
                "unsaved": self.unsaved,
                "viewer_error": viewer_error,
                "author": author or (entries[-1].get("author") if entries else None),
                # A body's id is the id of the feature that starts it.
                "changes": changes
                if changes is not None
                else [
                    {
                        "action": entry.get("action"),
                        "feature_id": entry.get("feature_id") or entry.get("body"),
                    }
                    for entry in entries
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

    def save_document(self, *, force: bool = False) -> bool:
        """Saves unless the file changed under the engine. Returns whether it saved."""
        document = self.session.document
        on_disk = document.file_sha256()
        if (
            not force
            and on_disk is not None
            and document.saved_sha256 is not None
            and on_disk != document.saved_sha256
        ):
            self.unsaved = True
            self._enter_conflict(on_disk)
            return False
        _ = document.save()
        self.unsaved = False
        self.conflict = None
        return True

    def write_recovery(self) -> None:
        """Copies unsaved edits to `recovery_path`, or deletes it when there are none."""
        if self.recovery_path is None:
            return
        if not self.unsaved:
            self.recovery_path.unlink(missing_ok=True)
            return
        document = self.session.document
        data = {
            "path": str(document.path) if document.path else None,
            "snapshot": document.snapshot(),
        }
        temporary = self.recovery_path.with_name(self.recovery_path.name + ".tmp")
        _ = temporary.write_text(json.dumps(data))
        _ = temporary.replace(self.recovery_path)

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
        Returns what it did: None, "reloaded", "conflict" or "unreadable".
        """
        with self.lock:
            document = self.session.document
            on_disk = document.file_sha256()
            if on_disk is None or on_disk == document.saved_sha256:
                return None
            if self.unsaved:
                self._enter_conflict(on_disk)
                return "conflict"
            try:
                _ = self.reload_from_disk()
            except DocumentError as exc:
                # Half written by an editor that does not write atomically, or
                # simply broken. The next change to the file is looked at again.
                say(f"not reloading {document.path}: {exc}")
                return "unreadable"
            return "reloaded"

    def _note_references(self) -> None:
        document = self.session.document
        self.reference_digests = {
            feature_id: reference_digest(path)
            for feature_id, path in document.reference_sources()
        }
        self.watched_references = document.referenced_files()

    def watched_files(self) -> list[Path]:
        """The document's own file and every file its references read."""
        document = self.session.document
        return ([document.path] if document.path else []) + list(
            self.watched_references
        )

    def on_file_change(self, path: Path) -> str | None:
        """React to a watched file changing: reload, conflict or rebuild."""
        if path == self.session.document.path:
            return self.check_file()
        return self.check_references()

    def check_references(self) -> str | None:
        """Rebuilds when a file a reference reads has changed: the board under
        an open case, regenerated by its script. Returns "rebuilt" or None.
        The document itself does not change, so nothing is saved or logged.
        """
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
                changes=[
                    {
                        "action": "reference_changed",
                        "feature_id": feature_id,
                        "file": path.name,
                    }
                    for feature_id, path in changed
                ],
            )
            return "rebuilt"

    def reload_from_disk(self) -> ReplyDict:
        """Takes the file's content as a new revision. The engine's previous
        state goes on the undo stack, so one undo brings it back.
        """
        document = self.session.document
        if document.path is None:
            raise DocumentError("this document has no file to reload")
        fresh = Document.load(document.path)
        _push(self.undo_stack, HistoryStep(document.snapshot()))
        self.redo_stack.clear()
        revision = document.restore(fresh.snapshot(), "file", action="reload")
        document.saved_sha256 = fresh.saved_sha256
        document.saved_snapshot = fresh.saved_snapshot
        self.unsaved = False
        self.conflict = None
        return _commit(self, revision, save=False)

    def start_watching(self, interval: float = 0.25) -> None:
        """Start polling the document's file and its references."""
        if self._watcher is None:
            self._note_references()
            self._watcher = FileWatcher(
                self.watched_files, self.on_file_change, interval
            )
            self._watcher.start()

    # ---- running -----------------------------------------------------------

    def serve_forever(self) -> None:
        """Serve on this thread until shut down."""
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
        """Stop watching and serving, and remove the socket file."""
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


UNDO_LIMIT = 100


def _push(stack: list[HistoryStep], step: HistoryStep) -> None:
    """Put `step` on an undo or redo stack, which keeps the newest UNDO_LIMIT."""
    stack.append(step)
    del stack[:-UNDO_LIMIT]


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads: bool = True
    allow_reuse_address: bool = True

    # The kernel's `sun_path` is 104 bytes on macOS and 108 on Linux, so the
    # tighter of the two is what to check against.
    MAX_SOCKET_PATH: int = 103

    def __init__(self, daemon: Daemon) -> None:
        self.daemon_ref: Daemon = daemon
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
        path.chmod(0o600)


def _is_live_socket(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


class _Handler(socketserver.StreamRequestHandler):
    # Replaced per connection in `setup`.
    _write_lock: threading.Lock = threading.Lock()

    def _daemon(self) -> Daemon:
        server = self.server
        if not isinstance(server, _Server):
            raise TypeError("a handler runs under the daemon's own server")
        return server.daemon_ref

    @override
    def setup(self) -> None:
        super().setup()
        self._write_lock = threading.Lock()
        self._daemon().add_client(self)

    @override
    def finish(self) -> None:
        self._daemon().remove_client(self)
        super().finish()

    def send_line(self, message: str) -> None:
        # A broadcast comes from whichever thread made the edit, so two writes
        # can interleave mid-line and hand the client invalid JSON.
        with self._write_lock:
            _ = self.wfile.write(message.encode() + b"\n")
            self.wfile.flush()

    @override
    def handle(self) -> None:
        daemon = self._daemon()
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            response = handle_line(daemon, line)
            if response is not None:
                try:
                    self.send_line(json.dumps(response))
                except OSError:
                    return


def handle_line(daemon: Daemon, line: bytes) -> Reply | None:
    """Answer one JSON-RPC request line, or None for a notification."""
    try:
        request = parse_json(line)
    except json.JSONDecodeError as exc:
        return _error(None, PARSE_ERROR, f"not valid JSON: {exc}")
    if not isinstance(request, dict):
        return _error(None, INVALID_REQUEST, "a request must be a JSON object")

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "'params' must be a JSON object")

    handler = METHODS.get(method) if isinstance(method, str) else None
    if handler is None:
        return _error(
            request_id, METHOD_NOT_FOUND, f"no method {method!r} (try list_methods)"
        )

    try:
        with daemon.lock:
            result = handler(daemon, params)
    except StaleRevisionError as exc:
        return _error(
            request_id,
            STALE_REVISION,
            str(exc),
            data={
                "based_on": exc.based_on,
                "current": exc.current,
                "document": _document_state(daemon),
            },
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


def _error(
    request_id: JSON, code: int, message: str, data: Reply | None = None
) -> Reply:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


# ---- parameters ------------------------------------------------------------
#
# A method's params are whatever JSON a client sent. These read one each, and
# raise KeyError or TypeError, which `_handle_line` answers as invalid params.


def _text(params: JSONObject, key: str) -> str:
    value = params[key]
    if not isinstance(value, str):
        raise TypeError(f"'{key}' must be a string")
    return value


def _optional_text(params: JSONObject, key: str) -> str | None:
    value = params.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"'{key}' must be a string")
    return value


def _optional_int(params: JSONObject, key: str) -> int | None:
    value = params.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise TypeError(f"'{key}' must be an integer")
    return value


def _author(params: JSONObject, default: str) -> str:
    return _optional_text(params, "author") or default


def _object(params: JSONObject, key: str) -> JSONObject:
    value = params.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"'{key}' must be an object")
    return value


# ---- methods ---------------------------------------------------------------


@final
class DocumentState(TypedDict):
    """The whole editable document, as get_document and a stale-revision error carry it."""

    path: str | None
    revision: int
    features: list[JSONObject]
    rollback_index: int
    parameters: JSONObject
    appearances: list[JSONObject]
    bodies: dict[str, JSONObject]


def _document_state(daemon: Daemon) -> DocumentState:
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


def _commit(
    daemon: Daemon, revision: int, *, save: bool = True, opened: bool = False
) -> ReplyDict:
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
            _ = daemon.save_document()
        else:
            # Exact, not sticky: an undo back to what the file holds is not an unsaved edit.
            daemon.unsaved = session.document.has_unsaved_changes()
    _carry_selection(daemon)
    daemon.announce_change(opened=opened)

    timeline = session.document.timeline()
    try:
        shape, failed = session.shape()
    except Exception as exc:
        return {
            "revision": revision,
            "timeline": timeline,
            "built": {"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
        }
    return {
        "revision": revision,
        "timeline": timeline,
        "built": {
            "ok": failed is None,
            "failed_feature": failed,
            "volume": round(shape.volume, 9) if shape is not None else None,
        },
    }


def _get_document(daemon: Daemon, _params: JSONObject) -> Reply:
    return _document_state(daemon)


def _file_path(params: JSONObject) -> Path:
    """The document path a file method was given. Absolute, because the daemon's
    working directory is not something a caller can see, and `.sprocket`, because
    these methods write files and should not be a way to write any other kind.
    """
    raw = params.get("path")
    if not isinstance(raw, str) or not raw:
        raise DocumentError("this needs a 'path'")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DocumentError(f"{raw!r} is not an absolute path")
    if path.suffix != ".sprocket":
        raise DocumentError(f"{path.name!r} is not a .sprocket file")
    return path


def _switch_to(daemon: Daemon, document: Document) -> Reply:
    """Makes `document` the open one. The undo history belonged to the old one,
    so it goes, and nothing is written: opening a file must not touch it.
    """
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
    return {
        "path": str(document.path),
        **_commit(daemon, document.revision, save=False, opened=True),
    }


def _open_document(daemon: Daemon, params: JSONObject) -> Reply:
    """Replaces the open document with the one at `path`. A file that will not
    load is refused and the open document is left as it was.
    """
    return _switch_to(daemon, Document.load(_file_path(params)))


def _new_document(daemon: Daemon, params: JSONObject) -> Reply:
    """An empty document saved at `path`. An existing file is refused rather
    than overwritten, because the caller picked a name, not a replacement.
    """
    path = _file_path(params)
    if path.exists():
        raise DocumentError(f"{path} already exists")
    document = Document(path=path)
    _ = document.save()
    return _switch_to(daemon, document)


def _save_as(daemon: Daemon, params: JSONObject) -> Reply:
    """Writes the open document to `path`. Edits are saved there from now on."""
    document = daemon.session.document
    path = document.save(_file_path(params))
    daemon.unsaved = False
    daemon.conflict = None
    daemon.session.moved()
    daemon.announce_change()
    return {"path": str(path), "revision": document.revision}


def _save(daemon: Daemon, _params: JSONObject) -> Reply:
    """Writes the open document to its own file. The app calls it on Save when
    the daemon runs with `--no-autosave`. A file changed on disk since the last
    save is refused, not overwritten: that is `resolve_conflict`'s choice.
    """
    document = daemon.session.document
    if document.path is None:
        raise DocumentError("this document has no file yet: use save_as")
    if not daemon.save_document():
        raise DocumentError(
            daemon.conflict["message"] if daemon.conflict else "the save was refused"
        )
    daemon.announce_change()
    return {"path": str(document.path), "revision": document.revision}


def _document_moved(daemon: Daemon, params: JSONObject) -> Reply:
    """The file was renamed or moved by the app. Follows it without writing,
    so unsaved edits stay unsaved.
    """
    path = _file_path(params)
    if not path.exists():
        raise DocumentError(f"{path} does not exist")
    daemon.session.document.path = path
    daemon.session.moved()
    return {"path": str(path)}


def _objects(value: JSON, what: str) -> list[JSONObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError(f"{what} must be a list of objects")
    return [item for item in value if isinstance(item, dict)]


def _recovered_snapshot(raw: str) -> Snapshot:
    """The snapshot a recovery file holds: {"path", "snapshot"}."""
    data = parse_json(Path(raw).read_text())
    if not isinstance(data, dict):
        raise TypeError("a recovery file is a JSON object")
    snapshot = _object(data, "snapshot")
    rollback_index = snapshot["rollback_index"]
    if isinstance(rollback_index, bool) or not isinstance(rollback_index, int):
        raise TypeError("'rollback_index' must be an integer")
    bodies = snapshot.get("bodies", {})
    if not isinstance(bodies, dict):
        raise TypeError("'bodies' must be an object")
    return {
        "features": _objects(snapshot["features"], "'features'"),
        "rollback_index": rollback_index,
        "parameters": _object(snapshot, "parameters"),
        "appearances": _objects(snapshot.get("appearances", []), "'appearances'"),
        "bodies": {
            body_id: settings
            for body_id, settings in bodies.items()
            if isinstance(settings, dict)
        },
    }


def _restore_recovery(daemon: Daemon, params: JSONObject) -> Reply:
    """Puts back the edits a recovery file holds (see `Daemon.write_recovery`)
    as one undoable step. They stay unsaved until the person saves them.
    """
    raw = params.get("file")
    if not raw or not isinstance(raw, str):
        raise DocumentError("this needs a 'file'")
    try:
        snapshot = _recovered_snapshot(raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DocumentError(f"cannot read the recovery file {raw}: {exc}") from exc
    document = daemon.session.document
    _push(daemon.undo_stack, HistoryStep(document.snapshot()))
    daemon.redo_stack.clear()
    revision = document.restore(snapshot, _author(params, "app"), action="recover")
    return _commit(daemon, revision)


def _get_timeline(daemon: Daemon, params: JSONObject) -> Reply:
    document = daemon.session.document
    _, failed = daemon.session.shape() if params.get("build", False) else (None, None)
    return {
        "revision": document.revision,
        "rollback_index": document.rollback_index,
        "timeline": document.timeline(),
        "failed_feature": failed,
    }


def _edit(daemon: Daemon, edits: list[JSON], params: JSONObject) -> Reply:
    """Applies edits, and makes them undoable unless they only move the rollback bar.

    Scrubbing the timeline is navigation. Undoing it would leave the user
    wondering what Cmd+Z just did to the part.
    """
    document = daemon.session.document
    before = document.snapshot()
    revision = document.apply_edits(
        edits, _optional_int(params, "base_revision"), _author(params, "agent:unknown")
    )
    if any(
        isinstance(edit, dict) and edit.get("op") != "set_rollback" for edit in edits
    ):
        _push(daemon.undo_stack, HistoryStep(before))
        daemon.redo_stack.clear()
    result = _commit(daemon, revision)
    # Fields whose "=param" expression a new number replaced, for the viewer to say so.
    unlinked = [
        item
        for entry in document.log[-len(edits) :]
        for item in _list(entry, "unlinked")
    ]
    if unlinked:
        result["unlinked"] = unlinked
    # Sketches a parameter moved that could not be solved again, because the solver is not installed.
    unsolved = [
        item
        for entry in document.log[-len(edits) :]
        for item in _list(entry, "unsolved")
    ]
    if unsolved:
        result["unsolved"] = unsolved
    return result


def _list(value: JSONObject, key: str) -> list[JSON]:
    found = value.get(key, [])
    return found if isinstance(found, list) else []


def _apply_edit(daemon: Daemon, params: JSONObject) -> Reply:
    edits = params.get("edits")
    if edits is None and "edit" in params:
        edits = [params["edit"]]
    if not isinstance(edits, list) or not edits:
        raise DocumentError("apply_edits needs a non-empty list of edits")
    return _edit(daemon, edits, params)


def _set_rollback(daemon: Daemon, params: JSONObject) -> Reply:
    return _edit(daemon, [{"op": "set_rollback", "index": params.get("index")}], params)


def _set_parameter(daemon: Daemon, params: JSONObject) -> Reply:
    return _edit(
        daemon,
        [
            {
                "op": "set_parameter",
                "name": params.get("name"),
                "value": params.get("value"),
            }
        ],
        params,
    )


def _set_visibility(daemon: Daemon, params: JSONObject) -> Reply:
    """Shows or hides a body. Undoable, but not an edit: no log entry, no
    timeline row. Hidden bodies still export.
    """
    document = daemon.session.document
    before = document.snapshot()
    base_revision = _optional_int(params, "base_revision")
    if base_revision is not None and base_revision != document.revision:
        raise StaleRevisionError(base_revision, document.revision)
    revision = document.set_visibility(_text(params, "id"), params.get("visible"))
    _push(daemon.undo_stack, HistoryStep(before, view_only=True))
    daemon.redo_stack.clear()
    return _commit(daemon, revision)


def _set_body(daemon: Daemon, params: JSONObject) -> Reply:
    """Name, colour and material are a logged edit. `visible`, when given, goes through
    `set_visibility` first, so it stays view state.
    """
    result: Reply | None = None
    if "visible" in params:
        result = _set_visibility(
            daemon,
            {
                "id": params.get("id"),
                "visible": params["visible"],
                "base_revision": params.get("base_revision"),
            },
        )
    changes = {
        name: params[name] for name in ("name", "color", "material") if name in params
    }
    if changes:
        based: JSONObject = {} if result is None else {"base_revision": None}
        result = _edit(
            daemon,
            [{"op": "set_body", "id": params.get("id"), **changes}],
            {**params, **based},
        )
    if result is None:
        raise DocumentError("set_body needs name, color, material or visible")
    return result


def _set_appearances(daemon: Daemon, params: JSONObject) -> Reply:
    return _edit(
        daemon,
        [{"op": "set_appearances", "appearances": params.get("appearances")}],
        params,
    )


def _face_handle(params: JSONObject) -> str:
    handle = params.get("handle")
    if not isinstance(handle, str) or not handle.startswith("f"):
        raise DocumentError("'handle' must be a face handle like 'f3'")
    return handle


def _resolve(shape: Compound, handle: str) -> Face | Edge:
    try:
        return resolve_handle(shape, handle)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None


def _get_face_appearance(daemon: Daemon, params: JSONObject) -> Reply:
    """What colours one face, and a selector that picks out just that face.

    `entries` are the appearances that reach the face, with how many faces
    each one colours, so a client can tell an entry made for this face from a
    wider one. `selector` is a `within` box around the face's centre, checked
    against the current build to match this face alone. Appearances resolve
    without a replay context, so a box is the selector that works there, and
    it survives any rebuild that leaves the face where it is.
    """
    handle = _face_handle(params)
    shape = daemon.session.require_shape()
    face = _resolve(shape, handle)
    entries: list[JSONObject] = []
    for index, appearance in enumerate(daemon.session.document.appearances):
        try:
            picked = resolve_selector(shape, parse_selector(appearance.get("select")))
        except SelectorError:
            continue
        if any(is_same(item, face) for item in picked):
            entries.append(
                {
                    "index": index,
                    "color": appearance.get("color"),
                    "material": appearance.get("material"),
                    "faces": len(picked),
                }
            )
    centre = list(face.center())
    selector: JSONObject | None = None
    for half in (0.05, 0.001):
        box = [round(value - half, 6) for value in centre] + [
            round(value + half, 6) for value in centre
        ]
        candidate: JSONObject = {
            "select": [{"op": "faces"}, {"op": "within", "box": [*box]}]
        }
        picked = resolve_selector(shape, parse_selector(candidate))
        if len(picked) == 1 and is_same(picked[0], face):
            selector = candidate
            break
    if selector is None:
        raise DocumentError(
            f"no box around {handle}'s centre picks it out alone: another face shares that centre"
        )
    return {
        "handle": handle,
        "centre": [round(value, 6) for value in centre],
        "entries": entries,
        "selector": selector,
    }


def _get_bodies(daemon: Daemon, _params: JSONObject) -> Reply:
    """The built bodies in order, with their settings, volume and face count."""
    return {
        "bodies": [
            {
                "id": body.id,
                "name": body.name,
                "color": body.color,
                "material": body.material,
                "visible": body.visible,
                "reference": body.reference,
                "tip": body.tip,
                "volume": round(body.shape.volume, 6),
                "faces": len(body.shape.faces()),
            }
            for body in daemon.session.bodies()
        ]
    }


def _check_clearance(daemon: Daemon, _params: JSONObject) -> Reply:
    """The gap between each part body and each reference body: the smallest
    distance, and the volume they share. A distance of 0 with no shared volume
    means they touch.
    """
    bodies = daemon.session.bodies()
    parts = [body for body in bodies if not body.reference]
    references = [body for body in bodies if body.reference]
    if not references:
        raise DocumentError("the document has no reference body to check against")
    pairs: list[JSONObject] = []
    for part in parts:
        for reference in references:
            pieces = intersect(part.shape, reference.shape) or []
            overlap = sum(
                piece.volume for piece in pieces if isinstance(piece, Compound | Solid)
            )
            pairs.append(
                {
                    "body": part.id,
                    "reference": reference.id,
                    "distance": round(distance_between(part.shape, reference.shape), 6),
                    "overlap_volume": round(overlap, 6),
                }
            )
    return {"pairs": pairs}


SKETCH_EDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "add_constraint": ("constraint",),
    "update_constraint": ("constraint_id", "changes"),
    "remove_constraint": ("constraint_id",),
    "add_entity": ("entity",),
    "add_reference": ("source",),
    "update_entity": ("entity_id", "changes"),
    "remove_entity": ("entity_id",),
}


def _sketch_method(action: str) -> "Method":
    """A daemon method that does one `edit_sketch` action on the sketch named by `feature`.

    The reply carries the solved sketch, so a caller sees where the solver put
    everything without a second request.
    """

    def method(daemon: Daemon, params: JSONObject) -> Reply:
        edit: JSONObject = {
            "op": "edit_sketch",
            "action": action,
            "id": params.get("feature"),
        }
        edit.update(
            {
                field: params[field]
                for field in SKETCH_EDIT_FIELDS[action]
                if field in params
            }
        )
        result = _edit(daemon, [edit], params)
        document = daemon.session.document
        entry = document.log[-1]
        index = entry["index"]
        if not isinstance(index, int):
            raise TypeError("a sketch edit's log entry names its index")
        sketch = document.features[index]
        detail = {
            key: entry[key]
            for key in ("constraint_id", "entity_id", "dof", "redundant")
            if key in entry
        }
        return {
            **result,
            **detail,
            "sketch": {
                "entities": sketch["entities"],
                "constraints": sketch["constraints"],
            },
        }

    return method


def _step_history(
    daemon: Daemon,
    take_from: list[HistoryStep],
    put_on: list[HistoryStep],
    params: JSONObject,
) -> Reply:
    document = daemon.session.document
    if not take_from:
        return {"revision": document.revision, "changed": False}
    step = take_from.pop()
    # A view change (set_visibility) undoes and redoes without a log entry.
    put_on.append(HistoryStep(document.snapshot(), view_only=step.view_only))
    revision = document.restore(
        step.snapshot, _author(params, "agent:unknown"), log=not step.view_only
    )
    return {**_commit(daemon, revision), "changed": True}


def _undo(daemon: Daemon, params: JSONObject) -> Reply:
    return _step_history(daemon, daemon.undo_stack, daemon.redo_stack, params)


def _redo(daemon: Daemon, params: JSONObject) -> Reply:
    return _step_history(daemon, daemon.redo_stack, daemon.undo_stack, params)


def _preview_feature(daemon: Daemon, params: JSONObject) -> Reply:
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
        document.features,
        document.rollback_index,
        document.parameters,
        document.revision,
        [],
    )
    index = _optional_int(params, "index")
    index = trial.rollback_index if index is None else index
    _ = trial.apply_edits(
        [
            {"op": "insert", "feature": params.get("feature"), "index": index},
            {"op": "set_rollback", "index": index + 1},
        ],
        None,
        "preview",
    )

    try:
        shape, ctx = replay(
            trial.for_replay(), cache_dir=daemon.session.cache_dir, return_context=True
        )
    except Exception as exc:
        failed = failed_feature(exc)
        return {
            "would_build": False,
            "failed_feature": failed.feature_id if failed else None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    feature = params.get("feature") or {}
    construct = (
        construct_feature(feature)
        if isinstance(feature, dict) and feature.get("type") in CONSTRUCT_TYPES
        else None
    )
    if construct is not None:
        # No solid to measure: the answer is where the construct feature lands.
        try:
            return {
                "would_build": True,
                "construct": construct_geometry(construct, ctx),
            }
        except Exception as exc:
            return {"would_build": False, "reason": f"{type(exc).__name__}: {exc}"}
    if shape is None:
        return {"would_build": False, "reason": "the previewed feature built nothing"}
    built: ReplyDict = {
        "would_build": True,
        "volume": round(shape.volume, 9),
        "area": round(shape.area, 9),
        "counts": {
            "faces": len(shape.faces()),
            "edges": len(shape.edges()),
            "solids": len(shape.solids()),
        },
    }
    if params.get("mesh"):
        built["mesh"] = _preview_mesh(shape)
    return built


def _preview_mesh(shape: Compound) -> Reply:
    """Flat triangle and edge-line coordinates of every solid, for a viewer
    that draws the previewed part over the real one. No face or edge identity:
    a preview is only looked at, never picked.
    """
    vertices: list[float] = []
    triangles: list[int] = []
    edges: list[float] = []
    for solid in shape.solids():
        mesh = tessellate(solid)
        offset = len(vertices) // 3
        triangles.extend(int_list(mesh.triangles.reshape(-1) + offset))
        vertices.extend(float_list(mesh.vertices.reshape(-1).round(4)))
        edges.extend(float_list(mesh.edges.reshape(-1).round(4)))
    return {"vertices": vertices, "triangles": triangles, "edges": edges}


def _get_profiles(daemon: Daemon, params: JSONObject) -> Reply:
    """The regions of one sketch that an extrude can pick, each named by the
    entity ids that bound it. An agent reads this to write `profiles`.
    """
    feature_id = params.get("feature")
    document = daemon.session.document
    feature = document.for_replay()["features"][document.index_of(feature_id)]
    if feature.get("type") != "sketch":
        raise DocumentError(
            f"feature {feature_id!r} is a {feature.get('type')}, not a sketch"
        )
    # As the build left it: a sketch with projected references is solved around them there.
    built = (
        daemon.session.context().features.get(feature_id)
        if isinstance(feature_id, str)
        else None
    )
    sketch = sketch_feature(built if built is not None else feature)
    if sketch is None:
        raise DocumentError(f"feature {feature_id!r} is not a sketch")
    return {"feature": feature_id, "profiles": sketch_profiles(sketch)}


def _resolve_plane(daemon: Daemon, params: JSONObject) -> Reply:
    """Where a sketch plane reference lands on the current build, as an explicit
    origin, normal and x_dir. An agent checks a face plane with this before it
    draws on it.
    """
    plane = params.get("plane")
    if plane is None:
        raise DocumentError("resolve_plane needs 'plane'")
    try:
        return {
            "plane": plane_json(
                resolve_plane(plane_ref(plane), daemon.session.context())
            )
        }
    except (KeyError, ValueError, SelectorError) as exc:
        raise DocumentError(f"plane does not resolve: {exc}") from None


def _describe_geometry(daemon: Daemon, _params: JSONObject) -> Reply:
    return {
        **describe(daemon.session.require_shape()),
        "selection": _selection_view(daemon),
    }


def _face_edges(daemon: Daemon, params: JSONObject) -> Reply:
    """The edge handles that bound face `handle`, in the order the part numbers them.
    The viewer's Fillet and Chamfer use it to round every edge of a picked face.
    """
    handle = _face_handle(params)
    shape = daemon.session.require_shape()
    face = _resolve(shape, handle)
    bounding = face.edges()
    return {
        "edges": [
            f"e{index}"
            for index, edge in enumerate(shape.edges())
            if any(is_same(edge, other) for other in bounding)
        ]
    }


# ---- selection -------------------------------------------------------------
#
# The person's selection lives here, not in the viewer, so an agent can read it:
# "fillet these" has nothing to act on otherwise. The viewer sends it on every
# pick. It holds handles from describe_geometry (`f2`, `e7`) and sketch ids, and
# the revision the handles were taken at, because a rebuild renumbers faces and
# edges.

_HANDLE = re.compile(r"[fe]\d+")


def _handles(params: JSONObject) -> list[str]:
    handles = params.get("handles", [])
    if not isinstance(handles, list) or not all(
        isinstance(item, str) and _HANDLE.fullmatch(item) for item in handles
    ):
        raise DocumentError("'handles' must be a list like ['f2', 'e7']")
    return [item for item in handles if isinstance(item, str)]


def _sketch_ids(daemon: Daemon, params: JSONObject) -> list[str]:
    ids = params.get("sketches", [])
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise DocumentError("'sketches' must be a list of sketch feature ids")
    sketches = {
        feature_id
        for feature in daemon.session.document.features
        if feature.get("type") == "sketch"
        and isinstance(feature_id := feature.get("id"), str)
    }
    return [item for item in ids if isinstance(item, str) and item in sketches]


def _selection_view(daemon: Daemon) -> ReplyDict:
    """The selection with `describe_geometry`'s entry for each handle: the sentence,
    and the centre, length or area an agent builds a selector from.

    `stale` is true when the part was rebuilt since the handles were taken.
    The sentences are left out then, because `f2` may be a different face now.
    """
    document = daemon.session.document
    selection = daemon.selection
    stale = bool(selection["handles"]) and selection["revision"] != document.revision
    view: ReplyDict = {
        "handles": [*selection["handles"]],
        "sketches": [*selection["sketches"]],
        "revision": selection["revision"],
        "stale": stale,
    }
    if selection["handles"] and not stale:
        shape, _ = daemon.session.shape()
        if shape is not None:
            described = describe(shape)
            items: dict[str, Reply] = {
                item["handle"]: item for item in described["faces"]
            }
            items.update({item["handle"]: item for item in described["edges"]})
            view["descriptions"] = [
                items.get(handle, {"handle": handle, "description": None})
                for handle in selection["handles"]
            ]
    return view


def _set_selection(daemon: Daemon, params: JSONObject) -> Reply:
    """What the viewer calls on every pick. `revision` is the revision the
    handles were picked at, and defaults to the current one.
    """
    revision = params.get("revision", daemon.session.document.revision)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise DocumentError("'revision' must be an integer")
    daemon.selection = {
        "handles": _handles(params),
        "sketches": _sketch_ids(daemon, params),
        "revision": revision,
    }
    daemon.selection_keys = (
        _selection_keys(daemon) if revision == daemon.session.document.revision else {}
    )
    view = _selection_view(daemon)
    daemon.broadcast("selection_changed", view)
    return view


def _get_selection(daemon: Daemon, _params: JSONObject) -> Reply:
    return _selection_view(daemon)


def _geometry_key(item: Face | Edge) -> GeometryKey:
    """Where a face or edge is and how big: what stays the same when a rebuild
    renumbers it but leaves it in place. Rounded to 0.1 µm.
    """
    centre = item.center()
    if isinstance(item, Edge):
        size = item.length
    else:
        size = item.area or item.length or 0.0
    return (
        round(centre.X, 4),
        round(centre.Y, 4),
        round(centre.Z, 4),
        round(size, 4),
    )


def _selection_keys(daemon: Daemon) -> dict[str, GeometryKey]:
    handles = daemon.selection["handles"]
    if not handles:
        return {}
    shape, _ = daemon.session.shape()
    if shape is None:
        return {}
    keys: dict[str, GeometryKey] = {}
    for handle in handles:
        try:
            keys[handle] = _geometry_key(resolve_handle(shape, handle))
        except ValueError:
            pass
    return keys


def _carry_selection(daemon: Daemon) -> None:
    """Keeps the person's selection across a rebuild: each picked face or edge
    is looked up again by position and size, and keeps its place in the
    selection under its new handle. One that is gone, or no longer unique,
    drops out. Runs before `document_changed` goes out, so a viewer that asks
    for the selection after it reloads gets the carried one.
    """
    selection = daemon.selection
    revision = daemon.session.document.revision
    if not selection["handles"] or selection["revision"] == revision:
        return
    keys = daemon.selection_keys
    shape, _ = daemon.session.shape() if keys else (None, None)
    found: dict[tuple[str, GeometryKey], list[str]] = {}
    if shape is not None:
        groups: tuple[tuple[str, Sequence[Face | Edge]], ...] = (
            ("f", shape.faces()),
            ("e", shape.edges()),
        )
        for kind, items in groups:
            wanted = {key for handle, key in keys.items() if handle[0] == kind}
            for index, item in enumerate(items if wanted else []):
                key = _geometry_key(item)
                if key in wanted:
                    found.setdefault((kind, key), []).append(f"{kind}{index}")
    carried_keys: dict[str, GeometryKey] = {}
    for handle in selection["handles"]:
        key = keys.get(handle)
        matches = found.get((handle[0], key), []) if key is not None else []
        if len(matches) == 1 and key is not None:
            carried_keys[matches[0]] = key
    feature_ids = {
        feature_id
        for feature in daemon.session.document.features
        if isinstance(feature_id := feature.get("id"), str)
    }
    sketches = [item for item in selection["sketches"] if item in feature_ids]
    daemon.selection = {
        "handles": list(carried_keys),
        "sketches": sketches,
        "revision": revision,
    }
    daemon.selection_keys = carried_keys
    daemon.broadcast("selection_changed", _selection_view(daemon))


def _resolve_conflict(daemon: Daemon, params: JSONObject) -> Reply:
    """Ends a conflict between the engine's edits and a file changed on disk.
    `keep: "file"` reloads the file, and the engine's state goes on the undo
    stack. `keep: "mine"` writes the engine's state over the file.
    """
    keep = params.get("keep")
    if keep not in ("file", "mine"):
        raise DocumentError('\'keep\' must be "file" or "mine"')
    if daemon.conflict is None and not daemon.unsaved:
        return {"revision": daemon.session.document.revision, "changed": False}
    if keep == "file":
        result = daemon.reload_from_disk()
    else:
        _ = daemon.save_document(force=True)
        daemon.announce_change()
        result: ReplyDict = {"revision": daemon.session.document.revision}
    daemon.broadcast(
        "document_conflict",
        {"path": str(daemon.session.document.path), "resolved": keep},
    )
    return {**result, "changed": True}


def _get_file_status(daemon: Daemon, _params: JSONObject) -> Reply:
    document = daemon.session.document
    return {
        "path": str(document.path) if document.path else None,
        "revision": document.revision,
        "unsaved": daemon.unsaved,
        "conflict": daemon.conflict,
        "changed_on_disk": document.path is not None
        and document.file_sha256() not in (None, document.saved_sha256),
    }


type Stat = tuple[int, int, int] | None


class FileWatcher:
    """Polls some files' stat and calls `on_change(path)` once one has changed
    and then held still for one interval, so a writer that is not atomic is
    read after it finishes. Polling, not FSEvents: stdlib only, it survives an
    atomic rename (a new inode), and a few stat calls a second cost nothing.
    `paths_of()` is asked every interval, so the set can change: Save As,
    Open, or a reference added. Each path is checked the first time it is
    polled, the ones there at start included: it may have changed between the
    daemon noting it and this thread's first stat, and a stat taken after the
    change would hide it for good. The callbacks compare content hashes, so a
    check of an unchanged file is a no-op.
    """

    def __init__(
        self,
        paths_of: Callable[[], list[Path]],
        on_change: Callable[[Path], object],
        interval: float = 0.25,
    ) -> None:
        """Watch the paths `paths_of` names, every `interval` seconds."""
        self.paths_of: Callable[[], list[Path]] = paths_of
        self.on_change: Callable[[Path], object] = on_change
        self.interval: float = interval
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _stat(path: Path) -> Stat:
        try:
            info = path.stat()
        except (OSError, TypeError):
            return None
        return (info.st_ino, info.st_size, info.st_mtime_ns)

    def start(self) -> None:
        """Start polling on a thread of its own."""
        self._thread.start()

    def stop(self) -> None:
        """Stop polling after the current pass."""
        self._stop.set()

    def _run(self) -> None:
        seen: dict[Path, Stat] = {}  # empty, so the first poll checks every path
        pending: dict[Path, Stat] = {}
        while not self._stop.wait(self.interval):
            paths = list(self.paths_of())
            for path in paths:
                now = self._stat(path)
                if path in seen:
                    if now == seen[path]:
                        _ = pending.pop(path, None)
                        continue
                    if pending.get(path) != now:  # still moving: wait one more interval
                        pending[path] = now
                        continue
                seen[path] = now
                _ = pending.pop(path, None)
                try:
                    _ = self.on_change(path)
                except Exception as exc:  # the watcher must outlive one bad read
                    say(f"file watcher: {type(exc).__name__}: {exc}")
            for gone in set(seen) - set(paths):
                del seen[gone]
                _ = pending.pop(gone, None)


def _tip_candidates(daemon: Daemon, body: Body) -> list[str]:
    """The solid features up to `body`'s tip: what `new_since` may name for it."""
    features = daemon.session.document.for_replay()["features"]
    tip_index = next(
        index for index, feature in enumerate(features) if feature["id"] == body.tip
    )
    return [
        feature_id
        for feature in features[: tip_index + 1]
        if feature.get("type") not in NOT_SOLIDS
        and isinstance(feature_id := feature.get("id"), str)
    ]


def _owner(
    daemon: Daemon,
    picked: Face | Edge,
    kind: Literal["face", "edge"],
    *,
    parts_only: bool,
) -> tuple[Body, int] | None:
    """The body `picked` is on, and its index among that body's faces or edges."""
    for body in daemon.session.bodies():
        if parts_only and body.reference:
            continue
        items: Sequence[Face | Edge] = (
            body.shape.faces() if kind == "face" else body.shape.edges()
        )
        local = next(
            (index for index, item in enumerate(items) if is_same(item, picked)), None
        )
        if local is not None:
            return body, local
    return None


def _resolve_selection(daemon: Daemon, params: JSONObject) -> Reply:
    """Turns picked handles into a selector that survives upstream edits.

    `handles` defaults to the person's current selection. The reply holds the
    selector to paste into a fillet's `edges`, with the `expected_count` that
    makes a rebuild fail loudly instead of rounding the wrong edges, and the
    `strategy` that found it: `new_since` and `structure` follow the geometry,
    and `position` only follows where it is, so it can move when a dimension does.
    """
    if params.get("handles") is None:
        if bool(daemon.selection["handles"]) and (
            daemon.selection["revision"] != daemon.session.document.revision
        ):
            raise DocumentError(
                "the selection is stale: the part was rebuilt since it was picked"
            )
        handles = daemon.selection["handles"]
    else:
        handles = _handles(params)
    shape = daemon.session.require_shape()
    features = daemon.session.document.for_replay()["features"]
    tip = last_solid_index(features, daemon.session.document.rollback_index)
    _, ctx = replay(
        {"features": features, "rollback_index": tip},
        cache_dir=daemon.session.cache_dir,
        return_context=True,
    )
    try:
        return synthesize(
            shape,
            ctx,
            handles,
            [
                feature_id
                for feature in features[:tip]
                if feature.get("type") not in NOT_SOLIDS
                and isinstance(feature_id := feature.get("id"), str)
            ],
        )
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None


def _face_sketch_plane(daemon: Daemon, params: JSONObject) -> Reply:
    """A sketch plane that follows a picked face: `{"feature", "face"}` for a
    new sketch's `plane`, and where it lands now.

    The face is looked up in the body that owns it, and the selector is
    synthesised against that body's tip, which is what `resolve_plane` searches.
    A face of the combined part is not a face of any one feature's result when
    the document has several bodies.
    """
    handle = _face_handle(params)
    shape = daemon.session.require_shape()
    face = _resolve(shape, handle)
    ctx = daemon.session.context()
    owner = _owner(daemon, face, "face", parts_only=True)
    if owner is None:
        raise DocumentError(f"{handle} is not on a part body")
    body, local = owner
    try:
        found = synthesize(
            body.shape, ctx, [f"f{local}"], _tip_candidates(daemon, body)
        )
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None
    reference: FacePlane = {"feature": body.tip, "face": found["selector"]}
    try:
        plane = resolve_plane(reference, ctx)
    except (ValueError, SelectorError) as exc:
        raise DocumentError(f"cannot sketch on {handle}: {exc}") from None
    # The plane must hold the picked face, or the selector found another one.
    if abs((face.center() - plane.origin).dot(plane.z_dir)) > 1e-6:
        raise DocumentError(f"the selector for {handle} lands on another face")
    return {
        "plane": reference,
        "resolved": plane_json(plane),
        "strategy": found["strategy"],
    }


def _pick_axis(daemon: Daemon, params: JSONObject) -> Reply:
    """An axis reference for a picked edge (`e7`) or cylindrical face (`f3`), and where it lands now.

    The reference is `{"base": <the body's tip>, "edge" or "face": <selector>}`, so a
    construct axis, a revolve or a pattern that holds it follows the geometry when an
    upstream dimension moves it. `resolved` is the origin and direction it gives today.
    """
    handle = params.get("handle")
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
        raise DocumentError("'handle' must be a face or edge handle like 'f3' or 'e7'")
    shape = daemon.session.require_shape()
    picked = _resolve(shape, handle)
    ctx = daemon.session.context()
    kind: Literal["face", "edge"] = "face" if handle[0] == "f" else "edge"
    owner = _owner(daemon, picked, kind, parts_only=False)
    if owner is None:
        raise DocumentError(f"{handle} is not on a body")
    body, local = owner
    try:
        found = synthesize(
            body.shape, ctx, [f"{handle[0]}{local}"], _tip_candidates(daemon, body)
        )
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None
    reference: EdgeAxis | FaceAxis = (
        {"base": body.tip, "face": found["selector"]}
        if kind == "face"
        else {"base": body.tip, "edge": found["selector"]}
    )
    try:
        axis = resolve_axis(reference, ctx)
    except (KeyError, ValueError, SelectorError) as exc:
        raise DocumentError(f"{handle} has no axis: {exc}") from None
    return {
        "axis": reference,
        "resolved": {
            "origin": [round(v, 9) for v in axis.position],
            "direction": [round(v, 9) for v in axis.direction],
        },
        "strategy": found["strategy"],
    }


def _edge_source(daemon: Daemon, handle: str) -> JSONObject:
    """A reference source for the body edge `handle` (`e7`) of the current build: the
    body's tip feature and a selector that finds the edge again on every rebuild.
    """
    shape = daemon.session.require_shape()
    edge = _resolve(shape, handle)
    ctx = daemon.session.context()
    owner = _owner(daemon, edge, "edge", parts_only=False)
    if owner is None:
        raise DocumentError(f"{handle} is not an edge of a body")
    body, local = owner
    try:
        found = synthesize(
            body.shape, ctx, [f"e{local}"], _tip_candidates(daemon, body)
        )
    except SelectorError as exc:
        raise DocumentError(str(exc)) from None
    return {"feature": body.tip, "edge": selector_json(found["selector"])}


def _project_reference(daemon: Daemon, params: JSONObject) -> Reply:
    """What projecting an edge into a sketch gives, without changing the document.

    The viewer calls it while a sketch is open, because a new sketch is not in
    the document yet. `plane` is the sketch's plane reference. The edge is named
    by `source` ({sketch, entity} or {feature, edge}), or by `handle`, an `e7`
    from the current build, which the reply turns into a `source`. The reply
    holds the `source` and the `entity` fields (kind and geometry) it projects to.
    """
    plane = params.get("plane")
    if plane is None:
        raise DocumentError("project_reference needs 'plane'")
    source = params.get("source")
    if source is None:
        handle = params.get("handle")
        if not isinstance(handle, str):
            raise DocumentError("project_reference needs 'source' or 'handle'")
        source = _edge_source(daemon, handle)
    if not isinstance(source, dict):
        raise DocumentError("'source' must be an object")
    ctx = daemon.session.context()
    try:
        entity = projector(plane_ref(plane), ctx)(parse_reference(source))
    except (KeyError, ValueError, SelectorError) as exc:
        raise DocumentError(f"cannot project that: {exc}") from None
    return {"source": source, "entity": projected_json(entity)}


def _highlight(daemon: Daemon, params: JSONObject) -> Reply:
    """Shows handles in the viewer, in a colour that is not the selection's.
    An empty list clears it. It does not change what is selected.
    """
    handles = _handles(params)
    if handles:
        shape = daemon.session.require_shape()
        for handle in handles:
            _ = resolve_handle(shape, handle)
    sketches = _sketch_ids(daemon, params)
    daemon.broadcast("highlight", {"handles": handles, "sketches": sketches})
    return {"handles": handles, "sketches": sketches}


def _measure(daemon: Daemon, params: JSONObject) -> Reply:
    """The part, one body (`body`), or one or two handles. The whole part also
    lists each part body's volume and area.
    """
    session = daemon.session
    shape = session.require_shape()
    parts = [body for body in session.bodies() if not body.reference]
    body_id = params.get("body")
    handles = params.get("handles")
    if handles is not None and not isinstance(handles, list):
        raise TypeError("'handles' must be a list")
    if body_id is not None:
        if handles:
            raise DocumentError("measure takes a body or handles, not both")
        body = next((body for body in parts if body.id == body_id), None)
        if body is None:
            raise DocumentError(
                f"no part body {body_id!r}; part bodies are {', '.join(body.id for body in parts) or 'none'}"
            )
        return {"body": body_id, **measure(body.shape)}
    measured: Measurement = measure(shape, handles)
    if handles:
        return measured
    return {
        **measured,
        "bodies": [
            {
                "id": body.id,
                "name": body.name,
                "volume": round(body.shape.volume, 9),
                "area": round(body.shape.area, 9),
            }
            for body in parts
        ],
    }


def _section(daemon: Daemon, params: JSONObject) -> Reply:
    """Where a plane cuts the part: the cut's area and 2D extent in the plane's
    own frame, and, given a `path` ending .dxf or .svg, the section drawn there.
    """
    plane_value = params.get("plane")
    if plane_value is None:
        raise DocumentError("section needs 'plane'")
    try:
        plane = resolve_plane(plane_ref(plane_value), daemon.session.context())
    except (KeyError, ValueError, SelectorError) as exc:
        raise DocumentError(f"plane does not resolve: {exc}") from None
    section = section_of(daemon.session.require_shape(), plane)
    result: ReplyDict = {"plane": plane_json(plane), "faces": 0, "area": 0.0}
    if section is not None:
        box = section.bounding_box()
        result.update(
            {
                "faces": len(section.faces()),
                "area": round(section.area, 9),
                "bounding_box": {
                    "min": [round(box.min.X, 9), round(box.min.Y, 9)],
                    "max": [round(box.max.X, 9), round(box.max.Y, 9)],
                },
            }
        )
    path = _optional_text(params, "path")
    if path:
        if section is None:
            raise DocumentError(
                "the plane misses the part, so there is no section to write"
            )
        try:
            write_section(section, path)
        except ExportError as exc:
            raise DocumentError(str(exc)) from None
        result["written"] = path
    return result


def _camera(params: JSONObject) -> str | list[float]:
    camera = params.get("camera", "iso")
    if isinstance(camera, str):
        return camera
    if isinstance(camera, list) and all(
        isinstance(item, int | float) and not isinstance(item, bool) for item in camera
    ):
        return [float(item) for item in camera if isinstance(item, int | float)]
    raise TypeError("'camera' must be a name or a three-number direction")


def _render_view(daemon: Daemon, params: JSONObject) -> Reply:
    path = params.get("path")
    if not path or not isinstance(path, str):
        raise DocumentError("render_view needs a 'path' to write the PNG to")
    highlight = params.get("highlight")
    if highlight is not None and not isinstance(highlight, list):
        raise TypeError("'highlight' must be a list")
    width = _optional_int(params, "width")
    height = _optional_int(params, "height")
    return render_png(
        daemon.session.require_shape(),
        Path(path),
        camera=_camera(params),
        width=640 if width is None else width,
        height=480 if height is None else height,
        highlight=highlight,
    )


def _export(daemon: Daemon, params: JSONObject) -> Reply:
    """STEP, STL and the viewer's mesh, whichever paths are given."""
    shape = daemon.session.require_shape()
    bodies = daemon.session.bodies()
    written: ReplyDict = {}
    try:
        if step := _optional_text(params, "step"):
            write_step(shape, step, bodies)
            written["step"] = step
        if stl := _optional_text(params, "stl"):
            write_stl(shape, stl)
            written["stl"] = stl
        if stl_dir := _optional_text(params, "stl_dir"):
            written["stl_dir"] = [str(path) for path in write_stl_dir(bodies, stl_dir)]
        if three_mf := _optional_text(params, "3mf"):
            write_3mf(bodies, three_mf)
            written["3mf"] = three_mf
        if mesh := _optional_text(params, "mesh"):
            written["mesh"] = mesh
            counts = export_mesh(
                shape,
                Path(mesh),
                daemon.session.document.appearances,
                daemon.session.bodies(),
            )["counts"]
            written["mesh_counts"] = counts
    except ExportError as exc:
        raise DocumentError(str(exc)) from None
    except OSError as exc:
        filename: object = getattr(exc, "filename", None)
        raise DocumentError(f"cannot write {filename}: {exc.strerror}") from None
    if not written:
        raise DocumentError(
            "export needs at least one of 'step', 'stl', 'stl_dir', '3mf' or 'mesh'"
        )
    return written


def _get_schemas(_daemon: Daemon, _params: JSONObject) -> Reply:
    """Every op's JSON Schema, which is how an agent learns what it can build.

    Generated from the registry rather than written out, so an op added in M3
    is agent-callable the moment it registers.
    """
    return {name: op.schema for name, op in sorted(REGISTRY.items())}


def _validate_feature(daemon: Daemon, params: JSONObject) -> Reply:
    resolved = validate_feature(
        params.get("feature"), daemon.session.document.resolved_parameters()
    )
    return {"valid": True, "resolved": resolved}


def _list_methods(_daemon: Daemon, _params: JSONObject) -> Reply:
    return {"methods": sorted(METHODS)}


def _ping(daemon: Daemon, _params: JSONObject) -> Reply:
    return {"ok": True, "revision": daemon.session.document.revision}


type Method = Callable[[Daemon, JSONObject], Reply]

METHODS: dict[str, Method] = {
    "get_document": _get_document,
    "open_document": _open_document,
    "new_document": _new_document,
    "save_as": _save_as,
    "save": _save,
    "document_moved": _document_moved,
    "restore_recovery": _restore_recovery,
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
    "face_edges": _face_edges,
    "resolve_plane": _resolve_plane,
    "project_reference": _project_reference,
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
    "pick_axis": _pick_axis,
    "highlight": _highlight,
    "measure": _measure,
    "render_view": _render_view,
    "export": _export,
    "section": _section,
    "get_schemas": _get_schemas,
    "validate_feature": _validate_feature,
    "list_methods": _list_methods,
    "ping": _ping,
}


# ---- client ----------------------------------------------------------------


class RpcError(Exception):
    """An error reply from the daemon, with its JSON-RPC code and data."""

    def __init__(self, code: int, message: str, data: JSON = None) -> None:
        """Hold the reply's code, message and data."""
        super().__init__(message)
        self.code: int = code
        self.data: JSON = data


type Notification = Callable[[str, JSON], object]


class Client:
    """A blocking client for the daemon. Used by the MCP server and the tests.

    Notifications arrive on the same socket as replies, interleaved with them,
    so reading for a reply skips anything without an id and hands it to
    `on_notification`. A client that ignored them would eventually read one as
    the answer to its own question.
    """

    def __init__(
        self, socket_path: Path, on_notification: Notification | None = None
    ) -> None:
        """Connect to the daemon listening on `socket_path`."""
        self.socket_path: Path = Path(socket_path)
        self.on_notification: Notification | None = on_notification
        self._socket: socket.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(str(self.socket_path))
        self._file: io.BufferedRWPair = self._socket.makefile("rwb")
        self._next_id: int = 0
        self._lock: threading.Lock = threading.Lock()

    def call(self, method: str, **params: JSON) -> JSONObject:
        """Call `method` and return its result, or raise RpcError with its error."""
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            _ = self._file.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }
                ).encode()
                + b"\n"
            )
            self._file.flush()
            while True:
                line = self._file.readline()
                if not line:
                    raise RpcError(INTERNAL_ERROR, "the daemon closed the connection")
                message = parse_json(line)
                if not isinstance(message, dict):
                    continue
                if message.get("id") != request_id:
                    notified = message.get("method")
                    if self.on_notification and isinstance(notified, str):
                        _ = self.on_notification(notified, message.get("params", {}))
                    continue
                if "error" in message:
                    error = message["error"]
                    if not isinstance(error, dict):
                        raise RpcError(INTERNAL_ERROR, "the daemon sent a bad error")
                    code, text = error.get("code"), error.get("message")
                    raise RpcError(
                        code if isinstance(code, int) else INTERNAL_ERROR,
                        str(text),
                        error.get("data"),
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RpcError(
                        INTERNAL_ERROR, "the daemon sent a result that is not an object"
                    )
                return result

    def close(self) -> None:
        """Close the connection."""
        self._file.close()
        self._socket.close()

    def __enter__(self) -> "Client":
        """Use the client in a `with` block, which closes it."""
        return self

    def __exit__(self, *exception: object) -> None:
        """Close the connection."""
        self.close()
