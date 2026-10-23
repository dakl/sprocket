"""A daemon on a real socket, shared by the RPC and MCP suites."""

import contextlib
import json
import shutil
import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest

from sprocket_engine.document import Document
from sprocket_engine.model import JSONObject
from sprocket_engine.rpc import Client, Daemon, Session

SKETCH: JSONObject = {
    "id": "s1",
    "type": "sketch",
    "plane": "XY",
    "rect": {"width": 40, "height": 30},
}
EXTRUDE: JSONObject = {"id": "e1", "type": "extrude", "profile": "s1", "distance": 10}
ALL_EDGES: JSONObject = {"select": [{"op": "edges"}]}
BOX_DOCUMENT: JSONObject = {"features": [SKETCH, EXTRUDE], "rollback_index": 2}


@contextlib.contextmanager
def running(document: JSONObject, tmp_path: Path) -> Generator[Daemon]:
    """A daemon on a socket short enough for the kernel to bind.

    `sun_path` is 104 bytes on macOS and pytest's own tmp_path is most of that
    on its own, so the socket cannot live beside the document here.
    """
    path = tmp_path / "part.sprocket"
    _ = path.write_text(json.dumps(document))
    socket_dir = Path(tempfile.mkdtemp())
    daemon = Daemon(
        Session(Document.load(path), cache_dir=tmp_path / "cache"),
        socket_dir / "d.sock",
    )
    daemon.start_in_background()
    try:
        yield daemon
    finally:
        daemon.shutdown()
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Daemon]:
    with running(BOX_DOCUMENT, tmp_path) as started:
        yield started


@pytest.fixture
def client(daemon: Daemon) -> Iterator[Client]:
    with Client(daemon.socket_path) as connected:
        yield connected
