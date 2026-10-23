"""The build123d calls whose own signatures are not fully typed.

build123d's `Shape` is generic over the OCCT shape it wraps, and these
functions and methods take or return a bare `Shape`, which a type checker
reads as `Shape[Unknown]`. Each is wrapped here once, with the type it really
has, so the rest of the engine calls a typed function. The `pyright: ignore`
comments are the only ones in the engine, and they sit on the library's side
of the line.
"""

from pathlib import Path

from build123d import (
    Compound,
    DraftAngleError,
    Edge,
    Face,
    Plane,
    ShapeList,
    Vector,
    Vertex,
)
from build123d.topology import Shape
from build123d import export_brep as _export_brep  # pyright: ignore[reportUnknownVariableType]
from build123d import export_step as _export_step  # pyright: ignore[reportUnknownVariableType]
from build123d import export_stl as _export_stl  # pyright: ignore[reportUnknownVariableType]
from build123d import import_brep as _import_brep  # pyright: ignore[reportUnknownVariableType]
from build123d import import_step as _import_step  # pyright: ignore[reportUnknownVariableType]
from OCP.TopoDS import TopoDS_Shape


def fuse(base: Compound, *tools: Compound) -> Compound:
    """Return `base` fused with every tool."""
    return base.fuse(*tools)  # pyright: ignore[reportUnknownMemberType]


def cut(base: Compound, *tools: Compound) -> Compound:
    """Return `base` with every tool cut out of it."""
    return base.cut(*tools)  # pyright: ignore[reportUnknownMemberType]


def intersect(
    base: Compound, *tools: Compound
) -> ShapeList[Shape[TopoDS_Shape]] | None:
    """Return what `base` and every tool share, or None when they share nothing."""
    return base.intersect(*tools)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def import_brep(path: Path) -> Shape[TopoDS_Shape]:
    """Return the shape a BREP file holds."""
    return _import_brep(path)  # pyright: ignore[reportUnknownVariableType]


def export_brep(shape: Compound, path: Path) -> bool:
    """Write `shape` to a BREP file, and say whether it worked."""
    return _export_brep(shape, path)


def import_step(path: Path) -> Compound:
    """Return the shapes a STEP file holds."""
    return _import_step(path)


def export_step(shape: Compound, path: Path) -> bool:
    """Write `shape` to a STEP file, and say whether it worked."""
    return _export_step(shape, path)


def export_stl(shape: Compound, path: Path) -> bool:
    """Write `shape` to a binary STL file, and say whether it worked."""
    return _export_stl(shape, path)


def is_same(a: Face | Edge | Vertex, b: Face | Edge | Vertex) -> bool:
    """Whether `a` and `b` are the same OCCT shape, not merely equal ones."""
    return a.is_same(b)  # pyright: ignore[reportUnknownMemberType]


def distance_to(shape: Edge | Face, point: Vector) -> float:
    """Return the shortest distance from `shape` to `point`."""
    return shape.distance_to(point)  # pyright: ignore[reportUnknownMemberType]


def faces_of(shape: TopoDS_Shape) -> ShapeList[Face]:
    """Return every face of an OCCT shape of any kind, a lone face included.

    build123d's `Compound` takes any shape at run time, though its signature
    names only a compound.
    """
    return Compound(shape).faces()  # pyright: ignore[reportArgumentType]


def to_world(plane: Plane, u: float, v: float) -> Vector:
    """Return the world point at (u, v) in `plane`'s own coordinates."""
    point = plane.from_local_coords((u, v))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(point, Vector):
        msg = "a plane turned a point into something else"
        raise TypeError(msg)
    return point


def draft_failure_face(error: DraftAngleError) -> Face | None:
    """Return the face a draft failed on, if build123d named one."""
    face = error.face  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return face if isinstance(face, Face) else None
