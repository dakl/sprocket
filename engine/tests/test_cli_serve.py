"""`serve` keeps one process warm across builds instead of re-importing
build123d per request (see cli.py's module docstring). The one thing that
must hold: several requests on one stdin stream each get their own response
line, success or failure, without the loop dying on a bad request.
"""

import io
import json
from pathlib import Path

from sprocket_engine.cli import _build_document, _serve

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def test_rollback_to_empty_clears_stale_exports_instead_of_failing(tmp_path: Path):
    document_path = tmp_path / "document.json"
    document = json.loads(
        (
            Path(__file__).parent / "golden" / "rect_extrude" / "document.json"
        ).read_text()
    )
    document["rollback_index"] = 0
    document_path.write_text(json.dumps(document))

    stl_out = tmp_path / "part.stl"
    stl_out.write_bytes(b"stale from before the rollback")
    mesh_out = tmp_path / "part.mesh"
    mesh_out.write_bytes(b"stale from before the rollback")

    success, output, failed_id = _build_document(
        document_path, None, stl_out, tmp_path / "cache", mesh_out
    )

    assert success is True
    assert "empty document" in output
    assert failed_id is None
    assert not stl_out.exists()
    assert not mesh_out.exists()


def test_a_broken_feature_still_exports_the_part_before_it(tmp_path: Path):
    """Editing a sketch can leave a later op unbuildable. The user needs the
    part they still have, plus the id of the op that no longer applies."""
    document_path = tmp_path / "document.json"
    document = json.loads(
        (
            Path(__file__).parent / "golden" / "rect_extrude" / "document.json"
        ).read_text()
    )
    document["features"].append(
        {
            "id": "f3",
            "type": "extrude_face",
            "base": "f2",
            # An ambiguous selector: it matches all 6 faces, so the op refuses
            # to guess. Editing a sketch can turn a once-unique selector into
            # this.
            "face": {"from": "part", "select": [{"op": "faces"}]},
            "distance": 5,
        }
    )
    document["rollback_index"] = 3
    document_path.write_text(json.dumps(document))

    stl_out = tmp_path / "part.stl"
    success, output, failed_id = _build_document(
        document_path, None, stl_out, tmp_path / "cache"
    )

    assert success is True
    assert failed_id == "f3"
    assert "no longer builds" in output
    assert stl_out.exists()


def test_serve_answers_one_line_per_request(monkeypatch, capsys, tmp_path):
    stl_out = tmp_path / "part.stl"
    mesh_out = tmp_path / "part.mesh"
    requests = (
        json.dumps(
            {
                "document": str(EXAMPLES_DIR / "box.sprocket"),
                "stl": str(stl_out),
                "mesh": str(mesh_out),
                "cache_dir": str(tmp_path / "cache"),
            }
        )
        + "\n"
        + json.dumps({"document": str(tmp_path / "does-not-exist.sprocket")})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(requests))

    _serve()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["success"] is True
    assert stl_out.exists()
    # The app asks for the mesh, not the STL (see EngineRunner.build), so a
    # renamed request key has to fail here.
    assert mesh_out.exists()

    second = json.loads(lines[1])
    assert second["success"] is False
