"""Describing and measuring a built shape, for an agent rather than a viewer.

An agent cannot pick a face with a mouse. It gets this instead: every face and
edge with a stable handle and a sentence a language model can reason about
("planar face, normal +Z, area 400.0 mm2, top of the part"), plus the numbers
to check its own work against.

Handles are `f<i>` and `e<i>`, indexing `shape.faces()` and `shape.edges()`.
That is the same order `mesh.py` writes `triangles_per_face` and
`segments_per_edge` in, and therefore the same face identity the viewer picks
with. One ordering across the whole system, so a handle an agent names is the
face a human sees highlighted.
"""

import math

from build123d import GeomType

AXIS_NAMES = (
    ((1, 0, 0), "+X"),
    ((-1, 0, 0), "-X"),
    ((0, 1, 0), "+Y"),
    ((0, -1, 0), "-Y"),
    ((0, 0, 1), "+Z"),
    ((0, 0, -1), "-Z"),
)

# How far from an axis a normal may point and still be called "+Z". 1e-6 is
# tighter than any real chamfer, so a 0.5 degree draft never reads as flat.
AXIS_TOLERANCE = 1e-6

# Which end of the bounding box a face has to sit against to earn "top of the
# part". Tessellation and OCCT both round, so an exact comparison misses.
EXTREME_TOLERANCE = 1e-6


def _xyz(vector) -> list[float]:
    return [round(vector.X, 9), round(vector.Y, 9), round(vector.Z, 9)]


def _axis_name(normal) -> str | None:
    for axis, name in AXIS_NAMES:
        if (
            abs(normal.X - axis[0]) < AXIS_TOLERANCE
            and abs(normal.Y - axis[1]) < AXIS_TOLERANCE
            and abs(normal.Z - axis[2]) < AXIS_TOLERANCE
        ):
            return name
    return None


def _position_word(centre, box, normal_axis: str | None) -> str | None:
    """Names where a face sits, but only along its own normal.

    A +Z face at the top of the box is "the top". A +Z face halfway up is a
    step, and has no useful one-word position, so this returns None rather
    than inventing one. Saying nothing beats saying "middle".
    """
    words = {
        "+Z": "top",
        "-Z": "bottom",
        "+X": "right",
        "-X": "left",
        "+Y": "back",
        "-Y": "front",
    }
    if normal_axis not in words:
        return None
    axis = normal_axis[1]
    value = getattr(centre, axis)
    high = getattr(box.max, axis)
    low = getattr(box.min, axis)
    wanted = high if normal_axis[0] == "+" else low
    if abs(value - wanted) > EXTREME_TOLERANCE * max(1.0, abs(wanted)):
        return None
    return words[normal_axis]


def describe_faces(shape) -> list[dict]:
    box = shape.bounding_box()
    described = []
    for index, face in enumerate(shape.faces()):
        kind = face.geom_type.name
        centre = face.center()
        entry = {
            "handle": f"f{index}",
            "kind": kind,
            "area": round(face.area, 9),
            "centre": _xyz(centre),
        }
        axis = None
        if kind == "PLANE":
            normal = face.normal_at(centre)
            axis = _axis_name(normal)
            entry["normal"] = _xyz(normal)
        position = _position_word(centre, box, axis)
        entry["description"] = _face_sentence(kind, axis, entry["area"], position)
        described.append(entry)
    return described


def _face_sentence(
    kind: str, axis: str | None, area: float, position: str | None
) -> str:
    words = [f"{kind.lower()} face"]
    if axis:
        words.append(f"normal {axis}")
    words.append(f"area {area:.3f} mm2")
    if position:
        words.append(f"the {position} of the part")
    return ", ".join(words)


def describe_edges(shape) -> list[dict]:
    return [
        {
            "handle": f"e{index}",
            "kind": edge.geom_type.name,
            "length": round(edge.length, 9),
            "centre": _xyz(edge.center()),
            "description": f"{edge.geom_type.name.lower()} edge, length {edge.length:.3f} mm",
        }
        for index, edge in enumerate(shape.edges())
    ]


def describe(shape) -> dict:
    return {
        "faces": describe_faces(shape),
        "edges": describe_edges(shape),
        "counts": {
            "faces": len(shape.faces()),
            "edges": len(shape.edges()),
            "vertices": len(shape.vertices()),
            "solids": len(shape.solids()),
        },
    }


def _resolve_handle(shape, handle: str):
    if not isinstance(handle, str) or len(handle) < 2 or handle[0] not in "fe":
        raise ValueError(f"handle {handle!r} must look like 'f0' or 'e3'")
    try:
        index = int(handle[1:])
    except ValueError:
        raise ValueError(f"handle {handle!r} must look like 'f0' or 'e3'") from None
    items = shape.faces() if handle[0] == "f" else shape.edges()
    if not 0 <= index < len(items):
        raise ValueError(
            f"handle {handle!r} is out of range, this shape has {len(items)}"
        )
    return items[index]


def measure(shape, handles: list[str] | None = None) -> dict:
    """Measures the whole shape, one handle, or the gap between two.

    Two handles give `distance`, the closest the two pieces of geometry come,
    and `centre_distance` between their centres, which is the question an agent
    often asks ("how far is this hole from that edge"). Where both have a
    direction (flat faces, straight edges) they also give `angle`.
    """
    handles = handles or []
    if not handles:
        box = shape.bounding_box()
        return {
            "volume": round(shape.volume, 9),
            "area": round(shape.area, 9),
            "bounding_box": {
                "min": _xyz(box.min),
                "max": _xyz(box.max),
                "size": _xyz(box.size),
            },
            "centre_of_mass": _xyz(shape.center()),
        }

    items = [_resolve_handle(shape, handle) for handle in handles]
    if len(items) == 1:
        item = items[0]
        measured = {"handle": handles[0], "centre": _xyz(item.center())}
        measured["area" if handles[0][0] == "f" else "length"] = round(
            item.area if handles[0][0] == "f" else item.length, 9
        )
        return measured
    if len(items) == 2:
        first, second = (item.center() for item in items)
        measured = {
            "handles": handles,
            "distance": round(items[0].distance_to(items[1]), 9),
            "centre_distance": round((second - first).length, 9),
            "centre_delta": _xyz(second - first),
        }
        angle = _angle(*items)
        if angle is not None:
            measured["angle"] = angle
        return measured
    raise ValueError("measure takes no handles, one handle, or two")


def _direction(item):
    """('normal', n) for a flat face, ('line', d) for a straight edge, else None."""
    if item.geom_type == GeomType.PLANE:
        return "normal", item.normal_at(item.center())
    if item.geom_type == GeomType.LINE:
        return "line", (item.position_at(1) - item.position_at(0)).normalized()
    return None


def _angle(a, b) -> float | None:
    """Degrees. Two flat faces: between their normals, 0 to 180, so faces that
    face each other read 180. Two straight edges: 0 to 90, since a line has no
    way round. A face and an edge: the line's angle to the plane, 0 to 90.
    """
    found = _direction(a), _direction(b)
    if None in found:
        return None
    (kind_a, vector_a), (kind_b, vector_b) = found
    cosine = max(-1.0, min(1.0, vector_a.dot(vector_b)))
    degrees = math.degrees(math.acos(cosine))
    if kind_a == kind_b == "normal":
        return round(degrees, 9)
    if kind_a == kind_b == "line":
        return round(min(degrees, 180 - degrees), 9)
    return round(abs(90 - degrees), 9)
