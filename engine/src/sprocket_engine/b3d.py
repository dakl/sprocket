"""The build123d calls whose own signatures are not fully typed.

build123d's `Shape` is generic over the OCCT shape it wraps, and these
functions and methods take or return a bare `Shape`, which a type checker
reads as `Shape[Unknown]`. Each is wrapped here once, with the type it really
has, so the rest of the engine calls a typed function. The `pyright: ignore`
comments sit on the library's side of that line, each with its reason.
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
from build123d import export_brep as _export_brep  # pyright: ignore[reportUnknownVariableType]  # takes a bare Shape
from build123d import export_step as _export_step  # pyright: ignore[reportUnknownVariableType]  # takes a bare Shape
from build123d import export_stl as _export_stl  # pyright: ignore[reportUnknownVariableType]  # takes a bare Shape
from build123d import import_brep as _import_brep  # pyright: ignore[reportUnknownVariableType]  # returns a bare Shape
from build123d import import_step as _import_step  # pyright: ignore[reportUnknownVariableType]  # takes a bare Shape
from build123d.topology import Shape
from OCP.TopoDS import TopoDS_Shape


def fuse(base: Compound, *tools: Compound) -> Compound:
    """Return `base` fused with every tool."""
    return base.fuse(*tools)  # pyright: ignore[reportUnknownMemberType]  # takes bare Shapes


def cut(base: Compound, *tools: Compound) -> Compound:
    """Return `base` with every tool cut out of it."""
    return base.cut(*tools)  # pyright: ignore[reportUnknownMemberType]  # takes bare Shapes


def intersect(
    base: Compound, *tools: Compound
) -> ShapeList[Shape[TopoDS_Shape]] | None:
    """Return what `base` and every tool share, or None when they share nothing."""
    return base.intersect(*tools)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # returns a list of bare Shapes


def import_brep(path: Path) -> Shape[TopoDS_Shape]:
    """Return the shape a BREP file holds."""
    return _import_brep(path)  # pyright: ignore[reportUnknownVariableType]  # returns a bare Shape


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
    return a.is_same(b)  # pyright: ignore[reportUnknownMemberType]  # takes a bare Shape


def distance_to(shape: Edge | Face, point: Vector) -> float:
    """Return the shortest distance from `shape` to `point`."""
    return shape.distance_to(point)  # pyright: ignore[reportUnknownMemberType]  # takes a bare Shape


def faces_of(shape: TopoDS_Shape) -> ShapeList[Face]:
    """Return every face of an OCCT shape of any kind, a lone face included."""
    return Compound(shape).faces()  # pyright: ignore[reportArgumentType]  # Compound takes any OCCT shape at run time; its signature names only a compound


def to_world(plane: Plane, u: float, v: float) -> Vector:
    """Return the world point at (u, v) in `plane`'s own coordinates."""
    point = plane.from_local_coords((u, v))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # typed as taking and returning any shape
    if not isinstance(point, Vector):
        raise TypeError("a plane turned a point into something else")
    return point


def to_local(plane: Plane, point: Vector) -> Vector:
    """Return `point` in `plane`'s own coordinates."""
    found = plane.to_local_coords(point)  # pyright: ignore[reportUnknownVariableType]  # typed as returning any shape
    if not isinstance(found, Vector):
        raise TypeError("a plane turned a point into something else")
    return found


def draft_failure_face(error: DraftAngleError) -> Face | None:
    """Return the face a draft failed on, if build123d named one."""
    face = error.face  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # an untyped attribute
    return face if isinstance(face, Face) else None
