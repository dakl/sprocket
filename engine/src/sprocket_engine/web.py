"""`sprocket web`: the viewer in a plain browser, on top of the daemon.

The spec's architecture rule is that the viewer must run outside the Swift app,
or an agent and Playwright have nothing to drive. In the app the shell writes
`part.mesh` and `features.json` next to the viewer and the viewer posts its
gestures to Swift. Here the same two files are served from the live document
instead, and the gestures arrive as `POST /command`. The viewer's own code does
not know the difference.

Plain HTTP, not a WebSocket. The spec names a WebSocket bridge, but the stdlib
has no WebSocket server and one request per gesture plus a one-way event stream
does the same job. `GET /events` is a server-sent event stream that carries the
daemon's `document_changed` notification, so an agent editing the document
through the socket moves the browser too.

Routes:

    GET  /part.mesh       the built part, in the viewer's mesh format; 404 if empty
    GET  /features.json   the timeline, each entry carrying its full feature
    GET  /schemas.json    every op's JSON Schema, which the parameter form is built from
    GET  /events          server-sent events: document_changed
    POST /command         one viewer gesture (see viewer_commands.py); undo and redo too
    POST /rpc             one JSON-RPC request, the same methods the socket takes
    GET  anything else    a static file from the viewer directory
"""

import json
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .document import DocumentError, StaleRevision
from .mesh import export_mesh
from .ops import REGISTRY
from .rpc import Daemon, _commit, _handle_line
from .viewer_commands import edits_for

HEARTBEAT_SECONDS = 15
UNDO_LIMIT = 100


class WebApp:
    """Everything the HTTP handler needs that is not HTTP."""

    def __init__(self, daemon: Daemon, viewer_dir: Path):
        self.daemon = daemon
        self.viewer_dir = Path(viewer_dir)
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._mesh: tuple[int, bytes | None] | None = None

    @property
    def document(self):
        return self.daemon.session.document

    def features_json(self) -> list[dict]:
        with self.daemon.lock:
            _, failed = self.daemon.session.shape()
            document = self.document
            entries = []
            for index, feature in enumerate(document.features):
                entry = {
                    "id": feature.get("id"),
                    "type": feature.get("type"),
                    "active": index < document.rollback_index,
                    "feature": feature,
                }
                # The viewer draws a sketch's outline from these two, and only
                # knows how for a polygon. A rect or circle sketch has none.
                if feature.get("type") == "sketch" and "polygon" in feature:
                    entry["plane"] = feature["plane"]
                    entry["polygon"] = feature["polygon"]
                if feature.get("id") == failed:
                    entry["failed"] = True
                entries.append(entry)
            return entries

    def mesh_bytes(self) -> bytes | None:
        """The mesh for the current revision, or None for an empty document."""
        with self.daemon.lock:
            revision = self.document.revision
            if self._mesh is None or self._mesh[0] != revision:
                shape, _ = self.daemon.session.shape()
                if shape is None:
                    self._mesh = (revision, None)
                else:
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "part.mesh"
                        export_mesh(shape, path)
                        self._mesh = (revision, path.read_bytes())
            return self._mesh[1]

    def schemas(self) -> dict:
        return {name: op.schema for name, op in sorted(REGISTRY.items())}

    def command(self, message: dict) -> dict:
        """Runs one viewer gesture and returns what a caller needs to refresh."""
        kind = message.get("type") if isinstance(message, dict) else None
        with self.daemon.lock:
            if kind == "undo":
                self._step_history(self._undo, self._redo, "undo")
            elif kind == "redo":
                self._step_history(self._redo, self._undo, "redo")
            else:
                before = self.document.snapshot()
                edits = edits_for(self.document, message)
                revision = self.document.apply_edits(edits, message.get("base_revision"), "viewer")
                _commit(self.daemon, revision)
                # Rolling back is navigation, not an edit: undoing it would
                # leave the user wondering what Cmd+Z just did to the part.
                if kind != "set_rollback":
                    self._undo.append(before)
                    del self._undo[:-UNDO_LIMIT]
                    self._redo.clear()
            _, failed = self.daemon.session.shape()
            return {"revision": self.document.revision, "failed_feature": failed}

    def _step_history(self, take_from: list, put_on: list, author: str) -> None:
        if not take_from:
            return
        put_on.append(self.document.snapshot())
        revision = self.document.restore(take_from.pop(), f"viewer:{author}")
        _commit(self.daemon, revision)


class _EventStream:
    """One open `/events` connection, registered as a daemon client."""

    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()

    def send_line(self, message: str) -> None:
        self.write(f"event: document_changed\ndata: {message}\n\n")

    def write(self, text: str) -> None:
        with self.lock:
            self.handler.wfile.write(text.encode())
            self.handler.wfile.flush()


def make_handler(app: WebApp):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(app.viewer_dir), **kwargs)

        def log_message(self, format, *args):
            pass

        def end_headers(self):
            # The mesh and the timeline change under a stable URL, so a cached
            # copy is a stale part.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send_json(self, body, status=HTTPStatus.OK):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            try:
                if route == "/part.mesh":
                    data = app.mesh_bytes()
                    if data is None:
                        self.send_error(HTTPStatus.NOT_FOUND, "the document builds to nothing")
                        return
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif route == "/features.json":
                    self._send_json(app.features_json())
                elif route == "/schemas.json":
                    self._send_json(app.schemas())
                elif route == "/events":
                    self._stream_events()
                else:
                    super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self):
            route = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._send_json({"error": f"not valid JSON: {exc}"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                if route == "/command":
                    self._send_json(app.command(body))
                elif route == "/rpc":
                    response = _handle_line(app.daemon, json.dumps(body).encode())
                    self._send_json(response if response is not None else {})
                else:
                    self._send_json({"error": f"no route {route}"}, HTTPStatus.NOT_FOUND)
            except StaleRevision as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
            except DocumentError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _stream_events(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            stream = _EventStream(self)
            app.daemon.add_client(stream)
            try:
                # Tells the browser the stream is live, and lets it read the
                # current revision without a second request.
                stream.write(f": connected at revision {app.document.revision}\n\n")
                while True:
                    time.sleep(HEARTBEAT_SECONDS)
                    stream.write(": ping\n\n")
            except OSError:
                pass
            finally:
                app.daemon.remove_client(stream)

    return Handler


def make_server(app: WebApp, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    return server
