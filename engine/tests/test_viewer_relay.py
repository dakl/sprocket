"""agent_pointer and screenshot_app: the daemon relays a request to the viewer
and returns the viewer's answer. A fake viewer on a raw socket stands in for
the real one, so the relay, the timeout and the PNG hand-off are under test
without a browser.
"""

import base64
import json
import socket
import threading
from pathlib import Path

import pytest

from sprocket_engine import rpc
from sprocket_engine.rpc import Client, RpcError

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="
)


def fake_viewer(socket_path: str, answer, ready: threading.Event, stop: threading.Event) -> None:
    """Answers every agent_pointer or capture_app it hears with `answer(method, params)`."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        sock.settimeout(0.2)
        sock.sendall(b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}\n')
        ready.set()
        buffer = b""
        while not stop.is_set():
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("method") in ("agent_pointer", "capture_app"):
                    params = message["params"]
                    reply = {
                        "request": params["request"],
                        **answer(message["method"], params),
                    }
                    sock.sendall(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "viewer_reply",
                                "params": reply,
                            }
                        ).encode()
                        + b"\n"
                    )


@pytest.fixture
def viewer(daemon):
    """Starts a fake viewer; the test sets `viewer.answer` before calling."""
    holder = type("Viewer", (), {})()
    holder.answer = lambda _method, params: {"result": {"heard": params}}
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(
        target=fake_viewer,
        # A lambda, not holder.answer: each test swaps in its own answer later.
        args=(str(daemon.socket_path), lambda m, p: holder.answer(m, p), ready, stop),  # noqa: PLW0108
        daemon=True,
    )
    thread.start()
    assert ready.wait(5)
    yield holder
    stop.set()
    thread.join(2)


def test_a_click_reaches_the_viewer_and_its_answer_comes_back(client, viewer):
    viewer.answer = lambda _method, params: {
        "result": {"element": "button#tool-fillet", "x": params["x"], "y": params["y"]}
    }
    assert client.call("agent_pointer", action="click", x=12, y=34, shift=True) == {
        "element": "button#tool-fillet",
        "x": 12,
        "y": 34,
    }


@pytest.mark.usefixtures("viewer")
def test_only_the_pointer_fields_are_relayed(client):
    heard = client.call("agent_pointer", action="key", key="Enter", junk="x")["heard"]
    assert heard["action"] == "key"
    assert heard["key"] == "Enter"
    assert "junk" not in heard


def test_a_viewer_error_comes_back_as_the_call_error(client, viewer):
    viewer.answer = lambda _method, _params: {"error": "nothing at that point"}
    with pytest.raises(RpcError, match="nothing at that point"):
        client.call("agent_pointer", action="click", x=1, y=1)


def test_an_unknown_action_is_refused_before_asking(client):
    with pytest.raises(RpcError, match="'action' must be one of"):
        client.call("agent_pointer", action="wiggle")


def test_no_viewer_times_out_with_a_reason(client, monkeypatch):
    monkeypatch.setattr(rpc, "POINTER_TIMEOUT_S", 0.3)
    with pytest.raises(RpcError, match="no viewer answered agent_pointer"):
        client.call("agent_pointer", action="move", x=1, y=1)


def test_a_waiting_call_does_not_hold_up_other_clients(daemon, client, monkeypatch):
    monkeypatch.setattr(rpc, "POINTER_TIMEOUT_S", 1.5)
    errors = []
    waiting = threading.Thread(
        target=lambda: errors.append(
            pytest.raises(RpcError, client.call, "agent_pointer", action="move", x=1, y=1)
        )
    )
    waiting.start()
    with Client(daemon.socket_path) as other:
        assert other.call("ping")["ok"]
    waiting.join(5)
    assert errors


def test_a_canvas_capture_is_written_where_asked(client, viewer, tmp_path: Path):
    viewer.answer = lambda _method, _params: {
        "result": {
            "png_base64": base64.b64encode(PNG).decode(),
            "scope": "canvas",
            "width": 1,
            "height": 1,
        }
    }
    path = tmp_path / "shot.png"
    result = client.call("screenshot_app", path=str(path))
    assert result == {"path": str(path), "scope": "canvas", "width": 1, "height": 1}
    assert path.read_bytes() == PNG


def test_the_app_writes_the_file_itself(client, viewer, tmp_path: Path):
    def app(_method, params):
        Path(params["path"]).write_bytes(PNG)
        return {"result": {"scope": "window", "width": 1, "height": 1, "scale": 2}}

    viewer.answer = app
    path = tmp_path / "window.png"
    assert client.call("screenshot_app", path=str(path))["scope"] == "window"
    assert path.read_bytes() == PNG


def test_an_answer_with_no_png_is_an_error(client, viewer, tmp_path: Path):
    viewer.answer = lambda _method, _params: {"result": {}}
    with pytest.raises(RpcError, match="wrote no PNG"):
        client.call("screenshot_app", path=str(tmp_path / "none.png"))
