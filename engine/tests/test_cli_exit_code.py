"""The exit code is the only thing a shell caller or the Swift app's one-shot
path can read, and until now it said nothing: sprocket_engine.main() dropped
cli.main()'s return value, so a build that failed gracefully - the error path
that prints a readable message and returns 1 - still exited 0. Everything here
goes through the real entry point for that reason; calling cli.main() alone
would not have caught it.
"""

import json
import sys
from pathlib import Path

import pytest

import sprocket_engine
from sprocket_engine.cli import build_document

GOLDEN_DOCUMENT = Path(__file__).parent / "golden" / "rect_extrude" / "document.json"


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    """Runs the console-script entry point (sprocket_engine:main, per
    pyproject) and returns the code it exits with.
    """
    monkeypatch.setattr(sys, "argv", ["sprocket", *argv])
    with pytest.raises(SystemExit) as exit_info:
        sprocket_engine.main()
    code = exit_info.value.code
    return 0 if code is None else code


def test_a_graceful_build_failure_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache dir that cannot be created makes replay() raise before it reaches
    any feature, so build_document returns (False, message) rather than
    raising - the graceful failure path. It must still exit 1.
    """
    blocked_cache = tmp_path / "cache"
    _ = blocked_cache.write_text("a file where the cache dir should go")

    code = _run(
        monkeypatch,
        [
            "build",
            str(GOLDEN_DOCUMENT),
            "--stl",
            str(tmp_path / "part.stl"),
            "--cache-dir",
            str(blocked_cache),
        ],
    )

    assert code == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_a_successful_build_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _run(
        monkeypatch,
        [
            "build",
            str(GOLDEN_DOCUMENT),
            "--stl",
            str(tmp_path / "part.stl"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert code == 0
    assert (tmp_path / "part.stl").exists()


def test_a_missing_document_is_a_readable_error_not_a_traceback(
    tmp_path: Path, capsys, monkeypatch
):
    code = _run(
        monkeypatch,
        [
            "build",
            str(tmp_path / "does-not-exist.sprocket"),
            "--stl",
            str(tmp_path / "part.stl"),
        ],
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert "cannot read document" in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize(
    "document, expected",
    [
        (
            '{"features": {"f1": {"type": "sketch"}}}',
            "'features' must be a list of objects",
        ),
        ('{"features": ["f1"]}', "'features' must be a list of objects"),
        ('{"units": {"length": "mm"}}', "'features' must be a list of objects"),
        (
            '{"features": [], "rollback_index": "2"}',
            "'rollback_index' must be an integer",
        ),
        # The type and the range are separate messages now: telling someone an
        # integer "must be an integer" sends them looking at the wrong thing.
        # Document.load is the one copy of both checks.
        (
            '{"features": [], "rollback_index": 3}',
            "'rollback_index' is 3, outside 0..0",
        ),
        ("{not json", "is not valid JSON"),
        ("[]", "must be a JSON object"),
    ],
)
def test_a_malformed_document_is_a_readable_error_not_a_traceback(
    tmp_path: Path, capsys, monkeypatch, document: str, expected: str
):
    document_path = tmp_path / "document.json"
    _ = document_path.write_text(document)

    code = _run(
        monkeypatch, ["build", str(document_path), "--stl", str(tmp_path / "part.stl")]
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert expected in stderr
    assert "Traceback" not in stderr


def test_a_binary_file_is_a_readable_error_not_a_traceback(
    tmp_path: Path, capsys, monkeypatch
):
    """Picking an .stl or .step instead of the .json is an easy mistake. read_text
    raises UnicodeDecodeError for it - a ValueError, so the OSError clause in
    _load_document does not catch it.
    """
    document_path = tmp_path / "part.stl"
    _ = document_path.write_bytes(b"solid \x80\x00\xff binary stl body")

    code = _run(
        monkeypatch, ["build", str(document_path), "--stl", str(tmp_path / "part.stl")]
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert "is not UTF-8 text" in stderr
    assert "Traceback" not in stderr


def test_serve_reports_a_malformed_document_the_same_way(tmp_path: Path) -> None:
    """`serve` swallows every exception into one response line, so a traceback
    there reached the app as an unreadable `output`. Same message as `build`.
    """
    document_path = tmp_path / "document.json"
    _ = document_path.write_text(json.dumps({"features": {"f1": {"type": "sketch"}}}))

    success, output, failed_id = build_document(
        document_path, None, None, tmp_path / "cache"
    )

    assert success is False
    assert "'features' must be a list of objects" in output
    assert failed_id is None


@pytest.mark.parametrize("flag", ["--step", "--stl"])
def test_an_export_to_a_missing_directory_names_the_directory(
    tmp_path: Path, capsys, monkeypatch, flag
):
    target = tmp_path / "nowhere" / "part.out"
    code = _run(
        monkeypatch,
        [
            "build",
            str(GOLDEN_DOCUMENT),
            flag,
            str(target),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert code == 1
    stderr = capsys.readouterr().err
    assert f"the directory {target.parent} does not exist" in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize("flag", ["--step", "--stl"])
def test_an_export_to_a_read_only_directory_says_permission_denied(
    tmp_path: Path, capsys, monkeypatch, flag
):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        code = _run(
            monkeypatch,
            [
                "build",
                str(GOLDEN_DOCUMENT),
                flag,
                str(locked / "part.out"),
                "--cache-dir",
                str(tmp_path / "cache"),
            ],
        )
    finally:
        locked.chmod(0o700)

    assert code == 1
    stderr = capsys.readouterr().err
    assert f"{locked / 'part.out'}: permission denied" in stderr
