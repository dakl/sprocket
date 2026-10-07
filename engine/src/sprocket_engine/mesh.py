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
from pathlib import Path

import numpy as np
import ocp_tessellate.convert as tessellate_convert
from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from ocp_tessellate.ocp_utils import TopTools_IndexedMapOfShape

from .materials import PRESETS
from .selectors import resolve_selector

FORMAT = "sprocket-mesh"
# Version 2 adds `face_materials`. A file without it is written as version 1,
# byte for byte what it was, and a viewer reads both. A version 1 reader
# refuses a version 2 file rather than draw a metal face as matte plastic.
VERSION = 1
VERSION_WITH_MATERIALS = 2
NO_MATERIAL = -1.0

# (header name, instance key, numpy dtype to store as, JSON type tag)
_BUFFERS = (
    ("vertices", "vertices", "<f4", "float32"),
    ("normals", "normals", "<f4", "float32"),
    ("indices", "triangles", "<u4", "uint32"),
    ("edges", "edges", "<f4", "float32"),
    ("triangles_per_face", "triangles_per_face", "<u4", "uint32"),
    ("segments_per_edge", "segments_per_edge", "<u4", "uint32"),
    # Optional: one 0xRRGGBB per face, 0xFFFFFFFF for "no colour". Absent when
    # the document has no appearances, so a viewer must not require it.
    ("face_colors", "face_colors", "<u4", "uint32"),
    # Optional, version 2: (roughness, metalness) per face, -1 for "no finish".
    ("face_materials", "face_materials", "<f4", "float32"),
)


def _pad_to_4(length: int) -> int:
    return -length % 4


def tessellate(shape) -> dict:
    """Returns ocp-tessellate's buffer set for one body, as flat numpy arrays.

    ocp-tessellate nests the mesh behind a scene graph: tessellate_group
    returns (instances, shapes, states), and each node under `shapes` holds a
    `{"ref": i}` into `instances`. A body's solids sit in one compound, which
    gives one instance, and this reaches straight for it rather than walking
    the tree. A compound of compounds gives one instance per child, and the
    order of those against `shape.faces()` is not known, so export_mesh
    tessellates each body on its own instead.
    """
    instances, _shapes, _states = tessellate_convert.tessellate_group(
        *tessellate_convert.to_ocpgroup(shape)
    )
    if len(instances) != 1:
        raise ValueError(f"expected one tessellated instance, got {len(instances)}")
    instance = instances[0]
    _drop_degenerate_edges(shape, instance)
    return instance


def _concatenate(instances: list[dict]) -> dict:
    """Buffers of several bodies end to end, face and edge order kept."""
    if len(instances) == 1:
        return instances[0]
    joined = {}
    for key in ("vertices", "normals", "edges", "triangles_per_face", "segments_per_edge"):
        joined[key] = np.concatenate([instance[key] for instance in instances])
    triangles, offset = [], 0
    for instance in instances:
        triangles.append(instance["triangles"].astype(np.int64) + offset)
        offset += len(instance["vertices"].reshape(-1)) // 3
    joined["triangles"] = np.concatenate(triangles)
    return joined


def _drop_degenerate_edges(shape, instance: dict) -> None:
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
    runs = instance["segments_per_edge"]
    if keep.all() or len(runs) != len(keep):
        return
    keep_segment = np.repeat(keep, runs)
    instance["edges"] = instance["edges"].reshape(-1, 6)[keep_segment].reshape(-1, 3)
    instance["segments_per_edge"] = runs[keep]


NO_COLOUR = 0xFFFFFFFF


def face_colours(shape, appearances) -> np.ndarray:
    """One 0xRRGGBB per face of `shape`, in `shape.faces()` order.

    `appearances` is the document's list of {"color": "#rrggbb", "select": <selector>}.
    They apply in order and a later one wins, so a whole-part colour comes
    first and the exceptions follow. A face no appearance names stays NO_COLOUR
    and the viewer draws it in its default.
    """
    colours = np.full(len(shape.faces()), NO_COLOUR, dtype="<u4")
    for appearance, indices in _painted(shape, appearances):
        colours[indices] = int(appearance["color"].lstrip("#"), 16)
    return colours


def _painted(shape, appearances):
    """(appearance, face indices it reaches) for each appearance, in order."""
    faces = shape.faces()
    for appearance in appearances or []:
        picked = resolve_selector(shape, appearance["select"])
        yield appearance, [index for index, face in enumerate(faces) if any(face.is_same(item) for item in picked)]


def face_materials(bodies, face_counts, shape, appearances) -> np.ndarray | None:
    """(roughness, metalness) per face: the body's finish, then appearances
    that name one. None when nothing has a finish, so the buffer is left out."""
    if not any(body.get("material") for body in bodies) and not any("material" in item for item in appearances or []):
        return None
    values = np.full((sum(face_counts), 2), NO_MATERIAL, dtype="<f4")
    start = 0
    for body, count in zip(bodies, face_counts):
        if body.get("material"):
            values[start:start + count] = PRESETS[body["material"]]
        start += count
    if shape is not None:
        for appearance, indices in _painted(shape, [item for item in appearances or [] if "material" in item]):
            values[indices] = PRESETS[appearance["material"]]
    return values


def body_colours(bodies, face_counts, shape, appearances) -> np.ndarray:
    """Body colours first, then appearances over the part's faces, which win."""
    colours = np.full(sum(face_counts), NO_COLOUR, dtype="<u4")
    start = 0
    for body, count in zip(bodies, face_counts):
        if body.get("color"):
            colours[start:start + count] = int(body["color"].lstrip("#"), 16)
        start += count
    if appearances and shape is not None:
        painted = face_colours(shape, appearances)
        named = painted != NO_COLOUR
        colours[: len(painted)][named] = painted[named]
    return colours


def export_mesh(shape, path: Path, appearances=None, bodies=None) -> dict:
    """Writes the mesh file for `shape` and returns the header it wrote.

    `bodies` is the build's body list (see replay.find_bodies) with each body's
    settings. With it, each body is tessellated on its own, parts first and
    reference bodies after them, and the header lists them in face order so the
    viewer can colour, hide and skip them when picking.

    The returned header is what a test or a caller should assert against: it
    carries the counts, so a change in face order or triangle count is visible
    without parsing the binary.
    """
    body_entries = []
    if bodies:
        ordered = [body for body in bodies if not body["reference"]] + [body for body in bodies if body["reference"]]
        instances = [tessellate(body["shape"]) for body in ordered]
        instance = _concatenate(instances)
        face_counts = [len(item["triangles_per_face"]) for item in instances]
        for body, item, count in zip(ordered, instances, face_counts):
            body_entries.append({
                "id": body["id"],
                # The newest feature of the body: what a Modify tool names as its base.
                "tip": body.get("tip", body["id"]),
                "name": body.get("name", body["id"]),
                "color": body.get("color"),
                "visible": body.get("visible", True),
                "reference": body["reference"],
                "faces": count,
                "edges": len(item["segments_per_edge"]),
                # Only when set, so a document with no finishes writes the same bytes as before.
                **({"material": body["material"]} if body.get("material") else {}),
            })
        if appearances or any(body.get("color") for body in ordered):
            instance["face_colors"] = body_colours(ordered, face_counts, shape, appearances)
        finishes = face_materials(ordered, face_counts, shape, appearances)
        if finishes is not None:
            instance["face_materials"] = finishes
        expected_faces = sum(len(body["shape"].faces()) for body in ordered)
        expected_edges = sum(len(body["shape"].edges()) for body in ordered)
    else:
        instance = tessellate(shape)
        if appearances:
            instance["face_colors"] = face_colours(shape, appearances)
            finishes = face_materials([{}], [len(shape.faces())], shape, appearances)
            if finishes is not None:
                instance["face_materials"] = finishes
        expected_faces, expected_edges = len(shape.faces()), len(shape.edges())

    vertex_count = len(instance["vertices"]) // 3
    triangle_count = len(instance["triangles"]) // 3
    face_count = len(instance["triangles_per_face"])
    edge_count = len(instance["segments_per_edge"])
    segment_count = int(instance["segments_per_edge"].sum())

    # These four have bitten this pipeline before as silent corruption rather
    # than an error: a buffer whose length disagrees with its run lengths draws
    # a plausible-looking part with the wrong faces highlighted. Fail here.
    if triangle_count != int(instance["triangles_per_face"].sum()):
        raise ValueError("triangles_per_face does not sum to the triangle count")
    if len(instance["edges"]) != 2 * segment_count:
        raise ValueError("edges is not two points per segment")
    if len(instance["normals"]) != len(instance["vertices"]):
        raise ValueError("one normal per vertex expected")
    if face_count != expected_faces or edge_count != expected_edges:
        raise ValueError("tessellation disagrees with the B-rep's face or edge count")

    # An offset needs every earlier buffer's padded size, so lay the payload
    # out first and build the header from the result.
    payload = bytearray()
    entries = {}
    for name, key, dtype, type_tag in _BUFFERS:
        if key not in instance:
            continue
        array = instance[key].reshape(-1).astype(dtype, copy=False)
        entries[name] = {
            "offset": len(payload),
            "length": int(array.size),
            "type": type_tag,
        }
        payload += array.tobytes()
        payload += b"\0" * _pad_to_4(len(payload))

    header = {
        "format": FORMAT,
        "version": VERSION_WITH_MATERIALS if "face_materials" in instance else VERSION,
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
        out.write(struct.pack("<I", len(header_bytes) + padding))
        out.write(header_bytes)
        out.write(b"\0" * padding)
        out.write(payload)
    return header
