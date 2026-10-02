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

FORMAT = "sprocket-mesh"
VERSION = 1

# (header name, instance key, numpy dtype to store as, JSON type tag)
_BUFFERS = (
    ("vertices", "vertices", "<f4", "float32"),
    ("normals", "normals", "<f4", "float32"),
    ("indices", "triangles", "<u4", "uint32"),
    ("edges", "edges", "<f4", "float32"),
    ("triangles_per_face", "triangles_per_face", "<u4", "uint32"),
    ("segments_per_edge", "segments_per_edge", "<u4", "uint32"),
)


def _pad_to_4(length: int) -> int:
    return -length % 4


def tessellate(shape) -> dict:
    """Returns ocp-tessellate's buffer set for one shape, as flat numpy arrays.

    ocp-tessellate nests the mesh behind a scene graph: tessellate_group
    returns (instances, shapes, states), and each node under `shapes` holds a
    `{"ref": i}` into `instances`. Sprocket documents replay to exactly one
    solid, so there is exactly one instance and this reaches straight for it
    rather than walking the tree.
    """
    instances, _shapes, _states = tessellate_convert.tessellate_group(
        *tessellate_convert.to_ocpgroup(shape)
    )
    if len(instances) != 1:
        raise ValueError(f"expected one tessellated instance, got {len(instances)}")
    instance = instances[0]
    _drop_degenerate_edges(shape, instance)
    return instance


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


def export_mesh(shape, path: Path) -> dict:
    """Writes the mesh file for `shape` and returns the header it wrote.

    The returned header is what a test or a caller should assert against: it
    carries the counts, so a change in face order or triangle count is visible
    without parsing the binary.
    """
    instance = tessellate(shape)

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
    if face_count != len(shape.faces()) or edge_count != len(shape.edges()):
        raise ValueError("tessellation disagrees with the B-rep's face or edge count")

    # An offset needs every earlier buffer's padded size, so lay the payload
    # out first and build the header from the result.
    payload = bytearray()
    entries = {}
    for name, key, dtype, type_tag in _BUFFERS:
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
        "version": VERSION,
        "counts": {
            "vertices": vertex_count,
            "triangles": triangle_count,
            "faces": face_count,
            "edges": edge_count,
            "segments": segment_count,
        },
        "buffers": entries,
    }

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
