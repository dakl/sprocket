"""`serve` keeps one process warm across builds instead of re-importing
build123d per request (see cli.py's module docstring). The one thing that
must hold: several requests on one stdin stream each get their own response
line, success or failure, without the loop dying on a bad request.
"""

import io
import json
from pathlib import Path

from sprocket_engine.cli import _serve

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def test_serve_answers_one_line_per_request(monkeypatch, capsys, tmp_path):
    stl_out = tmp_path / "part.stl"
    requests = (
        json.dumps(
            {
                "document": str(EXAMPLES_DIR / "box.json"),
                "stl": str(stl_out),
                "cache_dir": str(tmp_path / "cache"),
            }
        )
        + "\n"
        + json.dumps({"document": str(tmp_path / "does-not-exist.json")})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(requests))

    _serve()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["success"] is True
    assert stl_out.exists()

    second = json.loads(lines[1])
    assert second["success"] is False
