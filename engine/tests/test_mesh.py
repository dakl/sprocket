"""The mesh file's whole purpose is that triangle N belongs to B-rep face K
(see mesh.py). The viewer builds one geometry group per face from
`triangles_per_face` and highlights a picked face by its group index, so if
those run lengths stop lining up with `shape.faces()` the viewer highlights
the wrong surface while still drawing a part that looks correct. That failure
is invisible on screen, so it gets a test.

The reader here is deliberately written from the format docstring rather than
from mesh.py's constants: it is a second implementation, so a change to the
layout has to be made in two places to pass.
"""

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from sprocket_engine.mesh import export_mesh
from sprocket_engine.replay import replay

# The fixture builds its own document rather than reading examples/box.json.
# That file is a scratchpad people edit while trying the app out, and the
# pinned counts below then fail for a reason that has nothing to do with the
# code. It happened: the counts were first written from an edited copy, so CI
# read the committed file and failed while every local run passed.
BOX_DOCUMENT = {
    "schema_version": 1,
    "units": {"length": "mm"},
    "features": [
        {"id": "f1", "type": "sketch", "plane": "XY", "rect": {"width": 40, "height": 20, "center": [0, 0]}},
        {"id": "f2", "type": "extrude", "profile": "f1", "distance": 10},
    ],
}

_NUMPY_TYPES = {"float32": "<f4", "uint32": "<u4"}


def read_mesh(path: Path) -> tuple[dict, dict]:
    """Parses a mesh file the way the viewer does. Returns (header, buffers)."""
    raw = path.read_bytes()
    (header_length,) = struct.unpack_from("<I", raw, 0)
    header = json.loads(raw[4 : 4 + header_length].rstrip(b"\0"))
    payload_start = 4 + header_length

    buffers = {}
    for name, entry in header["buffers"].items():
        offset = payload_start + entry["offset"]
        assert offset % 4 == 0, f"{name} is not 4-aligned, a typed array cannot wrap it"
        buffers[name] = np.frombuffer(
            raw, dtype=_NUMPY_TYPES[entry["type"]], count=entry["length"], offset=offset
        )
    return header, buffers


@pytest.fixture(scope="module")
def box_mesh(tmp_path_factory):
    part = replay(BOX_DOCUMENT)
    path = tmp_path_factory.mktemp("mesh") / "part.mesh"
    header = export_mesh(part, path)
    return part, header, read_mesh(path)


def test_counts_in_the_header_match_what_the_buffers_hold(box_mesh):
    _part, written_header, (header, buffers) = box_mesh

    assert header == written_header, "export_mesh returned a header the file does not contain"
    assert header["format"] == "sprocket-mesh"
    counts = header["counts"]

    assert len(buffers["vertices"]) == counts["vertices"] * 3
    assert len(buffers["normals"]) == counts["vertices"] * 3
    assert len(buffers["indices"]) == counts["triangles"] * 3
    assert len(buffers["edges"]) == counts["segments"] * 2 * 3
    assert len(buffers["triangles_per_face"]) == counts["faces"]
    assert len(buffers["segments_per_edge"]) == counts["edges"]


def test_every_face_owns_a_disjoint_run_of_triangles(box_mesh):
    """Face identity is a triangle range, so the ranges have to tile the index
    buffer exactly: no gap leaves triangles belonging to no face, and no
    overlap puts one triangle in two faces."""
    part, _written, (header, buffers) = box_mesh

    per_face = buffers["triangles_per_face"]
    assert len(per_face) == len(part.faces())
    assert per_face.sum() == header["counts"]["triangles"]
    assert (per_face > 0).all(), "a face with no triangles cannot be picked"

    # Each face's triangles must also index its own vertices and nobody
    # else's. ocp-tessellate splits vertices per face, which is what lets the
    # viewer shade a face flat and highlight it by vertex range.
    indices = buffers["indices"]
    offset = 0
    previous_max = -1
    for face_index, triangle_count in enumerate(per_face):
        face_indices = indices[offset * 3 : (offset + triangle_count) * 3]
        assert face_indices.min() > previous_max, f"face {face_index} shares vertices with an earlier face"
        previous_max = int(face_indices.max())
        offset += triangle_count
    assert previous_max == header["counts"]["vertices"] - 1


def test_edges_are_ready_to_draw_as_line_segments(box_mesh):
    """The viewer draws edges with LineSegments, which consumes the buffer as
    consecutive point pairs. `segments_per_edge` then names which pairs came
    from which B-rep edge."""
    part, _written, (header, buffers) = box_mesh

    per_edge = buffers["segments_per_edge"]
    assert len(per_edge) == len(part.edges())
    assert per_edge.sum() == header["counts"]["segments"]
    assert len(buffers["edges"]) % 6 == 0


def test_box_topology_is_pinned(box_mesh):
    """A guard on OCCT, not on this code. Tessellation output is stable within
    an OCP version and reorders across upgrades (see ENGINE_VERSIONS), so these
    numbers changing means face indices moved and every stored face reference
    now points somewhere else."""
    _part, _written, (header, _buffers) = box_mesh

    assert header["counts"] == {
        "vertices": 24,
        "triangles": 12,
        "faces": 6,
        "edges": 12,
        "segments": 12,
    }


def test_normals_come_from_the_brep_rather_than_the_triangles(box_mesh):
    """The STL path had to call computeVertexNormals(), which averages
    triangle normals and so rounds off a curved face. These are OCCT's own
    surface normals, so they are unit length and, on a planar face, identical
    across the face."""
    _part, _written, (_header, buffers) = box_mesh

    normals = buffers["normals"].reshape(-1, 3)
    lengths = np.linalg.norm(normals, axis=1)
    assert np.allclose(lengths, 1.0, atol=1e-5)

    first_face_triangles = int(buffers["triangles_per_face"][0])
    first_face_vertices = buffers["indices"][: first_face_triangles * 3]
    face_normals = normals[np.unique(first_face_vertices)]
    assert np.allclose(face_normals, face_normals[0], atol=1e-5), "face 0 of a box should be planar"
