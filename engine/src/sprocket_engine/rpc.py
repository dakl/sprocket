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
import socket
import socketserver
import threading
import traceback
from pathlib import Path

from .document import Document, DocumentError, StaleRevision, validate_feature
from .geometry import describe, measure
from .ops import REGISTRY
from .ops.sketch import sketch_profiles
from .render import render_png
from .replay import build_shape, replay
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
        self._built_revision = None
        # Counts the documents this session has held. A revision number is
        # only unique within one document.
        self.generation = 0

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
            self._shape, self._failed_feature = build_shape(
                replayable["features"], replayable["rollback_index"], self.cache_dir
            )
            self._built_revision = self.document.revision
        return self._shape, self._failed_feature

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

    def announce_change(self, opened: bool = False) -> None:
        """`opened` says the document was replaced by another, not edited. A
        viewer then drops its selection and refits the camera, and cannot
        trust that an unchanged revision number means an unchanged part."""
        viewer_error = self.publish_viewer_files()
        document = self.session.document
        self.broadcast(
            "document_changed",
            {
                "revision": document.revision,
                "timeline": document.timeline(),
                "rollback_index": document.rollback_index,
                "path": str(document.path) if document.path else None,
                "opened": opened,
                "viewer_error": viewer_error,
            },
        )

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
    if save and daemon.autosave and session.document.path:
        session.document.save()
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
    return _commit(daemon, revision)


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


def _step_history(daemon: Daemon, take_from: list, put_on: list, params: dict) -> dict:
    document = daemon.session.document
    if not take_from:
        return {"revision": document.revision, "changed": False}
    put_on.append(document.snapshot())
    revision = document.restore(take_from.pop(), params.get("author", "agent:unknown"))
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


def _describe_geometry(daemon: Daemon, params: dict) -> dict:
    return describe(daemon.session.require_shape())


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
    from build123d import export_step, export_stl

    from .mesh import export_mesh

    shape = daemon.session.require_shape()
    written = {}
    if params.get("step"):
        export_step(shape, Path(params["step"]))
        written["step"] = params["step"]
    if params.get("stl"):
        export_stl(shape, Path(params["stl"]))
        written["stl"] = params["stl"]
    if params.get("mesh"):
        written["mesh"] = params["mesh"]
        written["mesh_counts"] = export_mesh(shape, Path(params["mesh"]))["counts"]
    if not written:
        raise DocumentError("export needs at least one of 'step', 'stl' or 'mesh'")
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
    "undo": _undo,
    "redo": _redo,
    "preview_feature": _preview_feature,
    "get_profiles": _get_profiles,
    "describe_geometry": _describe_geometry,
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
