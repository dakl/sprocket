"""The build123d and ocp-tessellate calls whose own signatures are not fully typed.

build123d's `Shape` is generic over the OCCT shape it wraps, and these
functions and methods take or return a bare `Shape`, which a type checker
reads as `Shape[Unknown]`. Each is wrapped here once, with the type it really
has, so the rest of the engine calls a typed function. The `pyright: ignore`
comments sit on the library's side of that line, each with its reason.
"""

from pathlib import Path
from typing import NotRequired, TypedDict, cast, final

import numpy as np
import numpy.typing as npt
import ocp_tessellate.convert as _tessellate  # pyright: ignore[reportMissingTypeStubs]  # ships no types

from build123d import (
    Compound,
    ExportDXF,
    ExportSVG,
    Mesher,
    Solid,
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


def distance_between(a: Face | Edge, b: Face | Edge) -> float:
    """Return the closest the two shapes come."""
    return a.distance_to(b)  # pyright: ignore[reportUnknownMemberType]  # takes a bare Shape


@final
class RawInstance(TypedDict):
    """One tessellated body as ocp-tessellate returns it: flat buffers in the shape's own frame."""

    vertices: npt.NDArray[np.float32]
    normals: npt.NDArray[np.float32]
    triangles: npt.NDArray[np.int32]
    edges: npt.NDArray[np.float32]
    triangles_per_face: npt.NDArray[np.int32]
    segments_per_edge: npt.NDArray[np.int32]


type Location = tuple[tuple[float, float, float], tuple[float, float, float, float]]


@final
class SceneRef(TypedDict):
    """A scene node's pointer into the instance list."""

    ref: int


class SceneNode(TypedDict):
    """A node of ocp-tessellate's scene tree: a placement, and an instance or children."""

    loc: NotRequired[Location | None]
    shape: NotRequired[SceneRef]
    parts: NotRequired[list["SceneNode"]]


def tessellate_group(shape: Compound) -> tuple[list[RawInstance], SceneNode]:
    """Return ocp-tessellate's buffers for `shape`, and the scene tree that places them."""
    instances, scene, _states = _tessellate.tessellate_group(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # returns bare lists and dicts
        *_tessellate.to_ocpgroup(shape)
    )
    # The library returns bare lists and dicts; these say what they hold.
    return (
        cast("list[RawInstance]", cast("object", instances)),
        cast("SceneNode", cast("object", scene)),
    )


def section_faces(shape: Compound, cutter: Face) -> list[Face]:
    """Return the faces where `cutter` crosses `shape`, none when it misses."""
    found = shape.intersect(cutter)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # returns a list of bare Shapes
    return [] if found is None else list(found.faces())


def to_local_shape(plane: Plane, shape: Compound) -> Compound:
    """Return `shape` moved into `plane`'s own coordinates."""
    moved = plane.to_local_coords(shape)  # pyright: ignore[reportUnknownVariableType]  # typed as returning any shape
    if not isinstance(moved, Compound):
        raise TypeError("a plane turned a compound into something else")
    return moved


def add_to_3mf(mesher: Mesher, shape: Compound | Solid) -> None:
    """Add one labelled shape to a 3MF being built."""
    _ = mesher.add_shape(shape)  # pyright: ignore[reportUnknownMemberType]  # takes bare Shapes


def write_3mf_file(mesher: Mesher, path: Path) -> None:
    """Write a built 3MF."""
    mesher.write(str(path))  # pyright: ignore[reportUnknownMemberType]  # a partially unknown signature


def write_dxf(section: Compound, path: Path) -> None:
    """Write a flat section as DXF."""
    drawing = ExportDXF()
    _ = drawing.add_shape(section)  # pyright: ignore[reportUnknownMemberType]  # takes bare Shapes
    drawing.write(str(path))  # pyright: ignore[reportUnknownMemberType]  # a partially unknown signature


def write_svg(section: Compound, path: Path, fill_color: int) -> None:
    """Write a flat section as SVG, filled with `fill_color`."""
    drawing = ExportSVG()
    _ = drawing.add_layer("section", fill_color=fill_color)  # pyright: ignore[reportUnknownMemberType]  # a partially unknown signature
    _ = drawing.add_shape(section, layer="section")  # pyright: ignore[reportUnknownMemberType]  # takes bare Shapes
    drawing.write(str(path))  # pyright: ignore[reportUnknownMemberType]  # a partially unknown signature
