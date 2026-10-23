"""STEP, STL, 3MF and section (DXF, SVG) export with errors a person can act on.

build123d hides why a write failed. `export_step` raises a bare "Failed to
write STEP file", and `export_stl` returns False on a denied path. So each path
is opened here first, which raises the OS error with its reason, and a False
from build123d becomes an error that names the file.
"""

import copy
import re
from pathlib import Path

from collections.abc import Sequence

from build123d import Color, Compound, Face, Mesher, Plane, Solid

from sprocket_engine.b3d import (
    add_to_3mf,
    export_step,
    export_stl,
    section_faces,
    to_local_shape,
    write_3mf_file,
    write_dxf,
    write_svg,
)
from sprocket_engine.context import Body


class ExportError(Exception):
    """A file the engine could not write, with the reason a person can act on."""


def _check_writable(path: Path, kind: str) -> None:
    if not path.parent.is_dir():
        raise ExportError(
            f"cannot write {kind} file {path}: the directory {path.parent} does not exist"
        )
    try:
        with path.open("ab"):
            pass
    except PermissionError:
        raise ExportError(
            f"cannot write {kind} file {path}: permission denied"
        ) from None
    except OSError as exc:
        raise ExportError(
            f"cannot write {kind} file {path}: {exc.strerror or exc}"
        ) from None


def _made(bodies: Sequence[Body] | None) -> list[Body]:
    """The bodies that are part of what is made: every one but the references."""
    return [body for body in bodies or [] if not body.reference]


def _labelled(body: Body, shape: Solid | None = None) -> Compound | Solid:
    """A copy of the body's shape, or of `shape`, one solid of it, carrying the
    body's name and colour, which build123d writes into STEP and 3MF. A copy,
    because the original sits in the cache.
    """
    labelled = copy.copy(body.shape if shape is None else shape)
    labelled.label = body.name or body.id
    if body.color:
        labelled.color = Color(body.color)
    return labelled


def write_step(
    shape: Compound, path: Path | str, bodies: Sequence[Body] | None = None
) -> None:
    """With bodies that have names or colours, or more than one body, writes a
    compound of labelled solids so the STEP carries them. Otherwise writes
    `shape` as it is, which keeps a plain one-body document byte-identical.
    """
    path = Path(path)
    _check_writable(path, "STEP")
    made = _made(bodies)
    if len(made) > 1 or any(body.color or body.name != body.id for body in made):
        shape = Compound(children=[_labelled(body) for body in made])
    try:
        written = export_step(shape, path)
    except Exception as exc:
        raise ExportError(f"cannot write STEP file {path}: {exc}") from None
    if written is False:
        raise ExportError(f"cannot write STEP file {path}: the STEP writer refused it")


def write_stl(shape: Compound, path: Path | str) -> None:
    """Writes `shape` as one binary STL."""
    path = Path(path)
    _check_writable(path, "STL")
    try:
        written = export_stl(shape, path)
    except Exception as exc:
        raise ExportError(f"cannot write STL file {path}: {exc}") from None
    if written is False:
        raise ExportError(f"cannot write STL file {path}: the STL writer refused it")


def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "body"


def write_stl_dir(bodies: Sequence[Body], directory: Path | str) -> list[Path]:
    """One STL per body, named after the body, in an existing directory."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ExportError(
            f"cannot write STL files to {directory}: the directory does not exist"
        )
    written: list[Path] = []
    for body in _made(bodies):
        target = directory / f"{_file_name(body.name or body.id)}.stl"
        write_stl(body.shape, target)
        written.append(target)
    return written


def write_3mf(bodies: Sequence[Body], path: Path | str) -> None:
    """One 3MF with an object per body, carrying names and colours."""
    path = Path(path)
    _check_writable(path, "3MF")
    mesher = Mesher()
    for body in _made(bodies):
        # One mesh per solid. A body of several solids meshed as one compound
        # fails lib3mf's validity check (the Orange Pi heatsink, whose ears
        # touch the plate only along an edge, did).
        for solid in body.shape.solids():
            try:
                add_to_3mf(mesher, _labelled(body, solid))
            except Exception as exc:
                raise ExportError(
                    f"cannot write 3MF file {path}: body {body.name or body.id}: {exc}"
                ) from None
    try:
        write_3mf_file(mesher, path)
    except Exception as exc:
        raise ExportError(f"cannot write 3MF file {path}: {exc}") from None


def section_of(shape: Compound, plane: Plane) -> Compound | None:
    """The faces where `plane` cuts `shape`, moved into the plane's own 2D frame
    (its x_dir is +X, z = 0), or None when the plane misses the part.
    """
    box = shape.bounding_box()
    # A plane's origin can sit far from the part (a face plane's is the world
    # origin projected onto it), so the cutting face reaches past the whole box.
    reach = (box.center() - plane.origin).length + box.diagonal + 1
    faces = section_faces(shape, Face.make_rect(2 * reach, 2 * reach, plane))
    return to_local_shape(plane, Compound(faces)) if faces else None


def write_section(section: Compound, path: Path | str) -> None:
    """A section from `section_of` as DXF or SVG, chosen by the file's extension."""
    path = Path(path)
    kind = path.suffix.lower().lstrip(".")
    if kind not in ("dxf", "svg"):
        raise ExportError(
            f"cannot write a section to {path}: the file must end in .dxf or .svg"
        )
    _check_writable(path, kind.upper())
    if kind == "dxf":
        write_dxf(section, path)
    else:
        write_svg(section, path, fill_color=0xC8783C)
