"""`sprocket web`: the viewer in a plain browser, on top of the daemon.

The spec's architecture rule is that the viewer must run outside the Swift app,
or an agent and Playwright have nothing to drive. In the app, the daemon writes
`part.mesh` and `features.json` next to the viewer (`--viewer-dir`) and Swift
forwards the viewer's edits to it. Here the same files are served from the live
document instead, and the edits arrive as `POST /rpc`. The viewer's own code
does not know the difference.

The viewer works out which edits a gesture means (viewer/edits.js) and sends
them to `apply_edit`. Nothing here knows what a gesture is.

Plain HTTP, not a WebSocket. The spec names a WebSocket bridge, but the stdlib
has no WebSocket server and one request per gesture plus a one-way event stream
does the same job. `GET /events` is a server-sent event stream that carries the
daemon's `document_changed` notification, so an agent editing the document
through the socket moves the browser too.

Routes:

    GET  /part.mesh       the built part, in the viewer's mesh format; 404 if empty
    GET  /features.json   the timeline, each entry carrying its full feature
    GET  /schemas.json    every op's JSON Schema, which the parameter form is built from
    GET  /export?format=stl|3mf|step   the built part as a download; 409 with the reason if there is none
    GET  /events          server-sent events: document_changed, selection_changed, highlight
    POST /rpc             one JSON-RPC request, the same methods the socket takes
    GET  anything else    a static file from the viewer directory
"""

import functools
import json
import tempfile
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override
from urllib.parse import parse_qs, urlsplit

from sprocket_engine.document import Document, DocumentError
from sprocket_engine.model import parse_json
from sprocket_engine.rpc import METHODS, Daemon, handle_line
from sprocket_engine.viewer_files import features_summary, mesh_bytes, schemas

HEARTBEAT_SECONDS = 15
# The `export` method's parameter names, which are also the file extensions.
EXPORT_TYPES: dict[str, str] = {
    "stl": "model/stl",
    "3mf": "model/3mf",
    "step": "model/step",
}


class WebApp:
    """Everything the HTTP handler needs that is not HTTP."""

    def __init__(self, daemon: Daemon, viewer_dir: Path) -> None:
        """Serve `daemon`'s document, and the viewer's files from `viewer_dir`."""
        self.daemon: Daemon = daemon
        self.viewer_dir: Path = Path(viewer_dir)
        self._mesh: tuple[tuple[int, int, int], bytes | None] | None = None

    @property
    def document(self) -> Document:
        """The open document."""
        return self.daemon.session.document

    def features_json(self) -> list[dict[str, object]]:
        """The timeline as the viewer reads it."""
        with self.daemon.lock:
            _, failed = self.daemon.session.shape()
            return features_summary(
                self.document, failed, self.daemon.session.context()
            )

    def mesh_bytes(self) -> bytes | None:
        """The mesh for the current revision, or None for an empty document."""
        with self.daemon.lock:
            # Two documents can sit at the same revision, so the revision alone
            # would serve one document's mesh for the other.
            key = (
                self.daemon.session.generation,
                self.document.revision,
                self.daemon.session.reference_rebuilds,
            )
            if self._mesh is None or self._mesh[0] != key:
                shape, _ = self.daemon.session.shape()
                self._mesh = (
                    key,
                    mesh_bytes(
                        shape, self.document.appearances, self.daemon.session.bodies()
                    ),
                )
            return self._mesh[1]

    def export_bytes(self, kind: str) -> tuple[bytes, str]:
        """The part as `kind` ("stl", "3mf" or "step"), and the file name to offer it under.
        Raises DocumentError when there is nothing to export.
        """
        with self.daemon.lock:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"part.{kind}"
                _ = METHODS["export"](self.daemon, {kind: str(path)})
                data = path.read_bytes()
            return (
                data,
                f"{self.document.path.stem if self.document.path else 'part'}.{kind}",
            )


class _EventStream:
    """One open `/events` connection, registered as a daemon client."""

    def __init__(self, handler: SimpleHTTPRequestHandler) -> None:
        """Write events to `handler`'s open response."""
        self.handler: SimpleHTTPRequestHandler = handler
        self.lock: threading.Lock = threading.Lock()

    def send_line(self, message: str) -> None:
        """Pass on one daemon notification as a named server-sent event."""
        # The daemon's own notification, named so the page can listen to each kind.
        parsed = parse_json(message)
        method = parsed.get("method") if isinstance(parsed, dict) else None
        self.write(f"event: {method}\ndata: {message}\n\n")

    def write(self, text: str) -> None:
        """Write raw event-stream text."""
        with self.lock:
            _ = self.handler.wfile.write(text.encode())
            self.handler.wfile.flush()


def make_handler(app: WebApp) -> Callable[..., SimpleHTTPRequestHandler]:
    """The request handler class for `app`, serving its viewer folder."""

    class Handler(SimpleHTTPRequestHandler):
        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

        @override
        def end_headers(self) -> None:
            # The mesh and the timeline change under a stable URL, so a cached
            # copy is a stale part.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send_json(self, body: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            _ = self.wfile.write(payload)

        @override
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            try:
                if route == "/part.mesh":
                    data = app.mesh_bytes()
                    if data is None:
                        self.send_error(
                            HTTPStatus.NOT_FOUND, "the document builds to nothing"
                        )
                        return
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    _ = self.wfile.write(data)
                elif route == "/features.json":
                    self._send_json(app.features_json())
                elif route == "/schemas.json":
                    self._send_json(schemas())
                elif route == "/export":
                    self._send_export()
                elif route == "/events":
                    if self._is_local_request():
                        self._stream_events()
                    else:
                        self._send_json(
                            {"error": "requests from other origins are refused"},
                            HTTPStatus.FORBIDDEN,
                        )
                else:
                    super().do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self._send_json(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def _send_export(self) -> None:
            kind = parse_qs(urlsplit(self.path).query).get("format", [""])[0]
            if not self._is_local_request():
                self._send_json(
                    {"error": "requests from other origins are refused"},
                    HTTPStatus.FORBIDDEN,
                )
            elif kind not in EXPORT_TYPES:
                self._send_json(
                    {"error": f"format must be one of {', '.join(EXPORT_TYPES)}"},
                    HTTPStatus.BAD_REQUEST,
                )
            else:
                try:
                    data, name = app.export_bytes(kind)
                except DocumentError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", EXPORT_TYPES[kind])
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{name}"'
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                _ = self.wfile.write(data)

        def _is_local_request(self) -> bool:
            """Refuses requests from other web pages.

            Anything on this machine can reach 127.0.0.1, including a page open
            in the user's browser, and a POST with a plain-text body needs no
            permission from the server. The Host check stops DNS rebinding and
            the Origin check stops a cross-site page. A script with no Origin
            header, such as curl or a test, is the user's own process.
            """
            address = self.server.server_address
            port: object = address[1] if isinstance(address, tuple) else None
            local = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in local and (
                origin is None or origin.removeprefix("http://") in local
            )

        def do_POST(self) -> None:
            if not self._is_local_request():
                self._send_json(
                    {"error": "requests from other origins are refused"},
                    HTTPStatus.FORBIDDEN,
                )
                return
            route = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = parse_json(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._send_json(
                    {"error": f"not valid JSON: {exc}"}, HTTPStatus.BAD_REQUEST
                )
                return
            try:
                if route == "/rpc":
                    response = handle_line(app.daemon, json.dumps(body).encode())
                    self._send_json(response if response is not None else {})
                else:
                    self._send_json(
                        {"error": f"no route {route}"}, HTTPStatus.NOT_FOUND
                    )
            except Exception as exc:
                self._send_json(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def _stream_events(self) -> None:
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

    # The stdlib's own way to give the handler its folder.
    return functools.partial(Handler, directory=str(app.viewer_dir))


class _Server(ThreadingHTTPServer):
    # The stdlib default is 5. A page load opens about eight connections at
    # once, and when the accept queue is full the kernel resets the extra ones,
    # so a <script src> fails with ERR_CONNECTION_RESET and the page never
    # starts. The OS caps this value (128 on macOS), so a large number is safe.
    request_queue_size: int = 128


def make_server(app: WebApp, port: int) -> ThreadingHTTPServer:
    """An HTTP server for `app` on 127.0.0.1:`port`."""
    server = _Server(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    return server
