"""Tessellate a B-rep into the viewer's mesh file.

`export_stl` threw away the one thing the viewer most needed: which triangle
belongs to which B-rep face. An STL is a flat triangle soup, so the viewer had
to guess faces back by flood-filling across triangles whose normals matched,
and it had no edge curves at all. ocp-tessellate returns both from OCCT
directly: a vertex/normal/triangle buffer set, plus `triangles_per_face` and
`segments_per_edge` giving the exact run lengths per face and per edge, in the
order `shape.faces()` and `shape.edges()` return them. That index is the face
identity the viewer uses.

One file, not a binary plus a JSON sidecar. Two derived files can fall out of
step with each other, and a viewer reading a stale manifest against a fresh
buffer misreads every offset. A single write cannot half-land.

Layout, little-endian throughout:

    uint32           header length in bytes
    header           UTF-8 JSON, zero-padded so the payload starts 4-aligned
    payload          buffers concatenated, each 4-aligned, in the header's order

A buffer's `offset` is from the start of the payload, not the start of the
file, so the viewer adds `4 + headerLength`. That indirection is the point:
an absolute offset would have to be written into the header that determines
it, and writing a larger number makes the header longer, which moves the
offset again. Relative offsets close that loop. Both the payload start and
every offset within it are multiples of 4, so the viewer can wrap each buffer
in a typed array over the same ArrayBuffer with no copying.
"""

import json
import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, final

import numpy as np
import numpy.typing as npt
from build123d import Compound
from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.collections import (
    IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape,
)

from sprocket_engine.b3d import Location, SceneNode, is_same, tessellate_group
from sprocket_engine.context import Body
from sprocket_engine.materials import PRESETS
from sprocket_engine.model import JSONObject
from sprocket_engine.selectors import parse_selector, resolve_selector

type Floats = npt.NDArray[np.float32]
type Ints = npt.NDArray[np.int64]
type Matrix = npt.NDArray[np.float64]

FORMAT = "sprocket-mesh"
# Version 2 adds `face_materials`. A file without it is written as version 1,
# byte for byte what it was, and a viewer reads both. A version 1 reader
# refuses a version 2 file rather than draw a metal face as matte plastic.
VERSION = 1
VERSION_WITH_MATERIALS = 2
NO_MATERIAL = -1.0


@dataclass
class Mesh:
    """One tessellation's buffers, flat, in world coordinates.

    `edges` holds two points per segment. `triangles_per_face` and
    `segments_per_edge` are the run lengths in `shape.faces()` and
    `shape.edges()` order. The last two are per-face extras, None when the
    document has nothing to put in them.
    """

    vertices: Floats
    normals: Floats
    triangles: Ints
    edges: Floats
    triangles_per_face: Ints
    segments_per_edge: Ints
    face_colors: npt.NDArray[np.uint32] | None = None
    face_materials: Floats | None = None


type BufferName = Literal[
    "vertices",
    "normals",
    "indices",
    "edges",
    "triangles_per_face",
    "segments_per_edge",
    "face_colors",
    "face_materials",
]


def _buffers(
    mesh: Mesh,
) -> list[tuple[BufferName, npt.NDArray[np.generic] | None, str, str]]:
    """(header name, array, numpy dtype to store as, JSON type tag), in file order."""
    return [
        ("vertices", mesh.vertices, "<f4", "float32"),
        ("normals", mesh.normals, "<f4", "float32"),
        ("indices", mesh.triangles, "<u4", "uint32"),
        ("edges", mesh.edges, "<f4", "float32"),
        ("triangles_per_face", mesh.triangles_per_face, "<u4", "uint32"),
        ("segments_per_edge", mesh.segments_per_edge, "<u4", "uint32"),
        # Optional: one 0xRRGGBB per face, 0xFFFFFFFF for "no colour". Absent when
        # the document has no appearances, so a viewer must not require it.
        ("face_colors", mesh.face_colors, "<u4", "uint32"),
        # Optional, version 2: (roughness, metalness) per face, -1 for "no finish".
        ("face_materials", mesh.face_materials, "<f4", "float32"),
    ]


def _pad_to_4(length: int) -> int:
    return -length % 4


def tessellate(shape: Compound) -> Mesh:
    """Returns ocp-tessellate's buffer set for one body, as flat numpy arrays.

    ocp-tessellate nests the mesh behind a scene graph: tessellate_group
    returns (instances, shapes, states), and each node under `shapes` holds a
    `{"ref": i}` into `instances`. A body's solids sit in one compound, which
    gives one instance, and this reaches straight for it rather than walking
    the tree. A compound of compounds gives one instance per child, and the
    order of those against `shape.faces()` is not known, so export_mesh
    tessellates each body on its own instead.
    """
    instances, scene = tessellate_group(shape)
    if len(instances) != 1:
        raise ValueError(f"expected one tessellated instance, got {len(instances)}")
    raw = instances[0]
    mesh = Mesh(
        vertices=raw["vertices"],
        normals=raw["normals"],
        triangles=raw["triangles"].astype(np.int64),
        edges=raw["edges"],
        triangles_per_face=raw["triangles_per_face"].astype(np.int64),
        segments_per_edge=raw["segments_per_edge"].astype(np.int64),
    )
    _place(mesh, _location_of(scene, 0))
    _drop_degenerate_edges(shape, mesh)
    return mesh


def _location_of(node: SceneNode, ref: int) -> tuple[Matrix, Matrix] | None:
    """The placement of instance `ref` in world coordinates, as (rotation 3x3,
    translation), or None when the tree does not hold it.

    The instance's buffers are in the shape's own frame. Each node on the way
    down carries a `loc` of ((x, y, z), (qx, qy, qz, qw)), and the placement
    is their product. A body made by sketches and extrudes has none, but a
    `reference` is placed by giving its shape a location, and without this
    the mesh draws it at its file position.
    """
    rotation, translation = _loc_matrix(node.get("loc"))
    if node.get("shape") == {"ref": ref}:
        return rotation, translation
    for child in node.get("parts", []):
        found = _location_of(child, ref)
        if found is not None:
            child_rotation, child_translation = found
            return rotation @ child_rotation, rotation @ child_translation + translation
    return None


def _loc_matrix(loc: Location | None) -> tuple[Matrix, Matrix]:
    if not loc:
        return np.eye(3), np.zeros(3)
    (x, y, z), (qx, qy, qz, qw) = loc
    rotation = np.array(
        [
            [
                1 - 2 * (qy * qy + qz * qz),
                2 * (qx * qy - qz * qw),
                2 * (qx * qz + qy * qw),
            ],
            [
                2 * (qx * qy + qz * qw),
                1 - 2 * (qx * qx + qz * qz),
                2 * (qy * qz - qx * qw),
            ],
            [
                2 * (qx * qz - qy * qw),
                2 * (qy * qz + qx * qw),
                1 - 2 * (qx * qx + qy * qy),
            ],
        ],
        dtype=np.float64,
    )
    return rotation, np.array([x, y, z], dtype=np.float64)


def _moved(points: Floats, rotation: Matrix, translation: Matrix) -> Floats:
    flat = points.reshape(-1, 3).astype(np.float64)
    return (flat @ rotation.T + translation).astype(points.dtype).reshape(points.shape)


def _place(mesh: Mesh, location: tuple[Matrix, Matrix] | None) -> None:
    """Moves the mesh's points and turns its normals by `location`."""
    if location is None:
        return
    rotation, translation = location
    if np.allclose(rotation, np.eye(3)) and np.allclose(translation, 0):
        return
    mesh.vertices = _moved(mesh.vertices, rotation, translation)
    mesh.edges = _moved(mesh.edges, rotation, translation)
    mesh.normals = _moved(mesh.normals, rotation, np.zeros(3))


def _concatenate(meshes: list[Mesh]) -> Mesh:
    """Buffers of several bodies end to end, face and edge order kept."""
    if len(meshes) == 1:
        return meshes[0]
    triangles: list[Ints] = []
    offset = 0
    for mesh in meshes:
        triangles.append(mesh.triangles + offset)
        offset += len(mesh.vertices.reshape(-1)) // 3
    return Mesh(
        vertices=np.concatenate([mesh.vertices for mesh in meshes]),
        normals=np.concatenate([mesh.normals for mesh in meshes]),
        triangles=np.concatenate(triangles),
        edges=np.concatenate([mesh.edges for mesh in meshes]),
        triangles_per_face=np.concatenate([mesh.triangles_per_face for mesh in meshes]),
        segments_per_edge=np.concatenate([mesh.segments_per_edge for mesh in meshes]),
    )


def _drop_degenerate_edges(shape: Compound, mesh: Mesh) -> None:
    """Removes the zero-length edges ocp-tessellate draws and build123d hides.

    A sphere's pole or a cone's apex is a degenerate edge: a B-rep edge with no
    length. `shape.edges()` skips them, so the mesh's edge runs have to skip
    them too, or every edge after the first one is off by one and the counts
    stop matching. A filleted tooth ring hit this with 52 of them. ocp-tessellate
    emits one 1-segment run per degenerate edge, in the order of OCCT's edge map.

    If the run count does not match the map, an edge was skipped for another
    reason and the order is unknown. The mesh is left alone and `export_mesh`
    reports the mismatch.
    """
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape.wrapped, TopAbs_EDGE, edge_map)
    keep = np.array(
        [
            not BRep_Tool.Degenerated_s(TopoDS.Edge(edge_map.FindKey(index)))
            for index in range(1, edge_map.Extent() + 1)
        ],
        dtype=bool,
    )
    runs = mesh.segments_per_edge
    if keep.all() or len(runs) != len(keep):
        return
    keep_segment = np.repeat(keep, runs)
    mesh.edges = mesh.edges.reshape(-1, 6)[keep_segment].reshape(-1, 3)
    mesh.segments_per_edge = runs[keep]


NO_COLOUR = 0xFFFFFFFF


def face_colours(
    shape: Compound, appearances: Sequence[JSONObject]
) -> npt.NDArray[np.uint32]:
    """One 0xRRGGBB per face of `shape`, in `shape.faces()` order.

    `appearances` is the document's list of {"color": "#rrggbb", "select": <selector>}.
    They apply in order and a later one wins, so a whole-part colour comes
    first and the exceptions follow. A face no appearance names stays NO_COLOUR
    and the viewer draws it in its default.
    """
    colours = np.full(len(shape.faces()), NO_COLOUR, dtype="<u4")
    for appearance, indices in _painted(shape, appearances):
        colours[indices] = int(_text(appearance, "color").lstrip("#"), 16)
    return colours


def _text(appearance: JSONObject, key: str) -> str:
    value = appearance.get(key)
    if not isinstance(value, str):
        raise ValueError(f"an appearance's {key!r} must be a string")
    return value


def _painted(
    shape: Compound, appearances: Sequence[JSONObject]
) -> Iterator[tuple[JSONObject, list[int]]]:
    """(appearance, face indices it reaches) for each appearance, in order."""
    faces = shape.faces()
    for appearance in appearances:
        picked = resolve_selector(shape, parse_selector(appearance.get("select")))
        yield (
            appearance,
            [
                index
                for index, face in enumerate(faces)
                if any(is_same(face, item) for item in picked)
            ],
        )


def face_materials(
    materials: Sequence[str | None],
    face_counts: Sequence[int],
    shape: Compound | None,
    appearances: Sequence[JSONObject],
) -> Floats | None:
    """(roughness, metalness) per face: each body's finish (`materials`, one per
    run of `face_counts`), then appearances that name one. None when nothing has
    a finish, so the buffer is left out.
    """
    if not any(materials) and not any("material" in item for item in appearances):
        return None
    values = np.full((sum(face_counts), 2), NO_MATERIAL, dtype="<f4")
    start = 0
    for material, count in zip(materials, face_counts, strict=True):
        if material:
            values[start : start + count] = PRESETS[material]
        start += count
    if shape is not None:
        for appearance, indices in _painted(
            shape, [item for item in appearances if "material" in item]
        ):
            values[indices] = PRESETS[_text(appearance, "material")]
    return values


def body_colours(
    bodies: Sequence[Body],
    face_counts: Sequence[int],
    shape: Compound | None,
    appearances: Sequence[JSONObject],
) -> npt.NDArray[np.uint32]:
    """Body colours first, then appearances over the part's faces, which win."""
    colours = np.full(sum(face_counts), NO_COLOUR, dtype="<u4")
    start = 0
    for body, count in zip(bodies, face_counts, strict=True):
        if body.color:
            colours[start : start + count] = int(body.color.lstrip("#"), 16)
        start += count
    if appearances and shape is not None:
        painted = face_colours(shape, appearances)
        named = np.not_equal(painted, NO_COLOUR)
        colours[: len(painted)][named] = painted[named]
    return colours


@final
class BodyEntry(TypedDict):
    """One body in the mesh header, in face order."""

    id: str
    tip: str
    name: str
    color: str | None
    visible: bool
    reference: bool
    faces: int
    edges: int
    material: NotRequired[str]


@final
class BufferEntry(TypedDict):
    """Where one buffer sits in the payload."""

    offset: int
    length: int
    type: str


@final
class MeshCounts(TypedDict):
    """How much the mesh holds."""

    vertices: int
    triangles: int
    faces: int
    edges: int
    segments: int


@final
class MeshHeader(TypedDict):
    """The JSON header of a mesh file."""

    format: str
    version: int
    counts: MeshCounts
    buffers: dict[str, BufferEntry]
    bodies: NotRequired[list[BodyEntry]]


def export_mesh(
    shape: Compound | None,
    path: Path,
    appearances: Sequence[JSONObject] | None = None,
    bodies: Sequence[Body] | None = None,
) -> MeshHeader:
    """Writes the mesh file for `shape` and returns the header it wrote.

    `bodies` is the build's body list (see replay.find_bodies) with each body's
    settings. With it, each body is tessellated on its own, parts first and
    reference bodies after them, and the header lists them in face order so the
    viewer can colour, hide and skip them when picking.

    The returned header is what a test or a caller should assert against: it
    carries the counts, so a change in face order or triangle count is visible
    without parsing the binary.
    """
    appearances = appearances or []
    body_entries: list[BodyEntry] = []
    if bodies:
        ordered = [body for body in bodies if not body.reference] + [
            body for body in bodies if body.reference
        ]
        meshes = [tessellate(body.shape) for body in ordered]
        mesh = _concatenate(meshes)
        face_counts = [len(item.triangles_per_face) for item in meshes]
        for body, item, count in zip(ordered, meshes, face_counts, strict=True):
            entry: BodyEntry = {
                "id": body.id,
                # The newest feature of the body: what a Modify tool names as its base.
                "tip": body.tip,
                "name": body.name,
                "color": body.color,
                "visible": body.visible,
                "reference": body.reference,
                "faces": count,
                "edges": len(item.segments_per_edge),
            }
            # Only when set, so a document with no finishes writes the same bytes as before.
            if body.material:
                entry["material"] = body.material
            body_entries.append(entry)
        if appearances or any(body.color for body in ordered):
            mesh.face_colors = body_colours(ordered, face_counts, shape, appearances)
        mesh.face_materials = face_materials(
            [body.material for body in ordered], face_counts, shape, appearances
        )
        expected_faces = sum(len(body.shape.faces()) for body in ordered)
        expected_edges = sum(len(body.shape.edges()) for body in ordered)
    else:
        if shape is None:
            raise ValueError("a mesh needs a shape or bodies")
        mesh = tessellate(shape)
        if appearances:
            mesh.face_colors = face_colours(shape, appearances)
            mesh.face_materials = face_materials(
                [None], [len(shape.faces())], shape, appearances
            )
        expected_faces, expected_edges = len(shape.faces()), len(shape.edges())

    vertex_count = len(mesh.vertices) // 3
    triangle_count = len(mesh.triangles) // 3
    face_count = len(mesh.triangles_per_face)
    edge_count = len(mesh.segments_per_edge)
    segment_count = int(mesh.segments_per_edge.sum())

    # These four have bitten this pipeline before as silent corruption rather
    # than an error: a buffer whose length disagrees with its run lengths draws
    # a plausible-looking part with the wrong faces highlighted. Fail here.
    if triangle_count != int(mesh.triangles_per_face.sum()):
        raise ValueError("triangles_per_face does not sum to the triangle count")
    if len(mesh.edges) != 2 * segment_count:
        raise ValueError("edges is not two points per segment")
    if len(mesh.normals) != len(mesh.vertices):
        raise ValueError("one normal per vertex expected")
    if face_count != expected_faces or edge_count != expected_edges:
        raise ValueError("tessellation disagrees with the B-rep's face or edge count")

    # An offset needs every earlier buffer's padded size, so lay the payload
    # out first and build the header from the result.
    payload = bytearray()
    entries: dict[str, BufferEntry] = {}
    for name, buffer, dtype, type_tag in _buffers(mesh):
        if buffer is None:
            continue
        array = buffer.reshape(-1).astype(dtype, copy=False)
        entries[name] = {
            "offset": len(payload),
            "length": int(array.size),
            "type": type_tag,
        }
        payload += array.tobytes()
        payload += b"\0" * _pad_to_4(len(payload))

    header: MeshHeader = {
        "format": FORMAT,
        "version": VERSION if mesh.face_materials is None else VERSION_WITH_MATERIALS,
        "counts": {
            "vertices": vertex_count,
            "triangles": triangle_count,
            "faces": face_count,
            "edges": edge_count,
            "segments": segment_count,
        },
        "buffers": entries,
    }
    if body_entries:
        header["bodies"] = body_entries

    header_bytes = json.dumps(header).encode()
    # The recorded header length includes the padding, so the payload starts at
    # exactly 4 + that. Pad by the header's own length, not the total: the
    # leading uint32 is already 4 bytes, so it does not shift the alignment.
    padding = _pad_to_4(len(header_bytes))

    path = Path(path)
    with path.open("wb") as out:
        _ = out.write(struct.pack("<I", len(header_bytes) + padding))
        _ = out.write(header_bytes)
        _ = out.write(b"\0" * padding)
        _ = out.write(payload)
    return header
