"""STEP, STL, 3MF and section (DXF, SVG) export with errors a person can act on.

build123d hides why a write failed. `export_step` raises a bare "Failed to
write STEP file", and `export_stl` returns False on a denied path. So each path
is opened here first, which raises the OS error with its reason, and a False
from build123d becomes an error that names the file.
"""

import copy
import re
from pathlib import Path

from build123d import (
    Color,
    Compound,
    ExportDXF,
    ExportSVG,
    Face,
    Mesher,
    ShapeList,
    export_step,
    export_stl,
)


class ExportError(Exception):
    pass


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


def _made(bodies) -> list[dict]:
    """The bodies that are part of what is made: every one but the references."""
    return [body for body in bodies or [] if not body["reference"]]


def _labelled(body: dict):
    """A copy of the body's shape carrying its name and colour, which build123d
    writes into STEP and 3MF. A copy, because the original sits in the cache.
    """
    shape = copy.copy(body["shape"])
    shape.label = body.get("name") or body["id"]
    if body.get("color"):
        shape.color = Color(body["color"])
    return shape


def write_step(shape, path, bodies=None) -> None:
    """With bodies that have names or colours, or more than one body, writes a
    compound of labelled solids so the STEP carries them. Otherwise writes
    `shape` as it is, which keeps a plain one-body document byte-identical.
    """
    path = Path(path)
    _check_writable(path, "STEP")
    made = _made(bodies)
    if len(made) > 1 or any(
        body.get("color") or body.get("name", body["id"]) != body["id"] for body in made
    ):
        shape = Compound(children=[_labelled(body) for body in made])
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


def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "body"


def write_stl_dir(bodies, directory) -> list[Path]:
    """One STL per body, named after the body, in an existing directory."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ExportError(
            f"cannot write STL files to {directory}: the directory does not exist"
        )
    written = []
    for body in _made(bodies):
        target = directory / f"{_file_name(body.get('name') or body['id'])}.stl"
        write_stl(body["shape"], target)
        written.append(target)
    return written


def write_3mf(bodies, path) -> None:
    """One 3MF with an object per body, carrying names and colours."""
    path = Path(path)
    _check_writable(path, "3MF")
    mesher = Mesher()
    for body in _made(bodies):
        # One mesh per solid. A body of several solids meshed as one compound
        # fails lib3mf's validity check (the Orange Pi heatsink, whose ears
        # touch the plate only along an edge, did).
        for solid in body["shape"].solids():
            try:
                mesher.add_shape(_labelled({**body, "shape": solid}))
            except Exception as exc:
                raise ExportError(
                    f"cannot write 3MF file {path}: body {body.get('name') or body['id']}: {exc}"
                ) from None
    try:
        mesher.write(str(path))
    except Exception as exc:
        raise ExportError(f"cannot write 3MF file {path}: {exc}") from None


def section_of(shape, plane):
    """The faces where `plane` cuts `shape`, moved into the plane's own 2D frame
    (its x_dir is +X, z = 0), or None when the plane misses the part.
    """
    box = shape.bounding_box()
    # A plane's origin can sit far from the part (a face plane's is the world
    # origin projected onto it), so the cutting face reaches past the whole box.
    reach = (box.center() - plane.origin).length + box.diagonal + 1
    cut = shape.intersect(Face.make_rect(2 * reach, 2 * reach, plane))
    faces = (
        []
        if cut is None
        else (
            cut.faces() if isinstance(cut, ShapeList) or hasattr(cut, "faces") else []
        )
    )
    return plane.to_local_coords(Compound(list(faces))) if faces else None


def write_section(section, path) -> None:
    """A section from `section_of` as DXF or SVG, chosen by the file's extension."""
    path = Path(path)
    kind = path.suffix.lower().lstrip(".")
    if kind not in ("dxf", "svg"):
        raise ExportError(
            f"cannot write a section to {path}: the file must end in .dxf or .svg"
        )
    _check_writable(path, kind.upper())
    if kind == "dxf":
        drawing = ExportDXF()
        drawing.add_shape(section)
    else:
        drawing = ExportSVG()
        drawing.add_layer("section", fill_color=0xC8783C)
        drawing.add_shape(section, layer="section")
    drawing.write(str(path))
