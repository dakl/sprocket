"""STEP and STL export with errors a person can act on.

build123d hides why a write failed. `export_step` raises a bare "Failed to
write STEP file", and `export_stl` returns False on a denied path. So each path
is opened here first, which raises the OS error with its reason, and a False
from build123d becomes an error that names the file.
"""

from pathlib import Path

from build123d import export_step, export_stl


class ExportError(Exception):
    pass


def _check_writable(path: Path, kind: str) -> None:
    if not path.parent.is_dir():
        raise ExportError(f"cannot write {kind} file {path}: the directory {path.parent} does not exist")
    try:
        with path.open("ab"):
            pass
    except PermissionError:
        raise ExportError(f"cannot write {kind} file {path}: permission denied") from None
    except OSError as exc:
        raise ExportError(f"cannot write {kind} file {path}: {exc.strerror or exc}") from None


def write_step(shape, path) -> None:
    path = Path(path)
    _check_writable(path, "STEP")
    try:
        written = export_step(shape, path)
    except Exception as exc:
        raise ExportError(f"cannot write STEP file {path}: {exc}") from None
    if written is False:
        raise ExportError(f"cannot write STEP file {path}: the STEP writer refused it")


def write_stl(shape, path) -> None:
    path = Path(path)
    _check_writable(path, "STL")
    try:
        written = export_stl(shape, path)
    except Exception as exc:
        raise ExportError(f"cannot write STL file {path}: {exc}") from None
    if written is False:
        raise ExportError(f"cannot write STL file {path}: the STL writer refused it")
