"""The closed regions of a sketch: what a user picks and extrudes.

Lines, arcs and circles cross each other and form a planar arrangement. A
rectangle crossed by a circle is three regions, not one. This module cuts a
large frame face along every entity, keeps the cells that do not touch the
frame, and names each cell by the entity ids that bound it, as SPEC.md asks:
`{"sketch": "f3", "bounded_by": ["e1", "e2", "e5"]}`, never "region 0".

Everything here is in the sketch's own 2D frame: the XY plane at the origin.
The caller moves a region onto the sketch plane.
"""

import math
from dataclasses import dataclass

from build123d import Compound, Edge, Face, Plane, Vector, Wire
from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.gp import gp_Pnt
from OCP.OCP.collections import List_TopoDS_Shape as ShapeList
from OCP.TopAbs import TopAbs_IN

# Endpoints the solver leaves a hair apart still have to meet, or a closed
# rectangle reads as four loose lines and yields no region.
FUZZY = 1e-6
# How far a split edge's midpoint may sit from the entity it came from.
ON_ENTITY = 1e-5
TWO_PI = 2 * math.pi


class ProfileError(Exception):
    pass


@dataclass
class Region:
    face: Face  # in the sketch frame, normal +Z
    bounded_by: list[str]  # sorted entity ids
    inside: tuple[float, float]  # a point strictly inside, to tell equal bounded_by apart
    outline: list[tuple[float, float]]  # tessellated outer boundary, counter-clockwise
    holes: list[list[tuple[float, float]]]
    area: float


def entity_edge(entity: dict) -> Edge:
    kind = entity["kind"]
    if kind == "line":
        return Edge.make_line(tuple(entity["p0"]), tuple(entity["p1"]))
    centre = entity["c"]
    frame = Plane(origin=(centre[0], centre[1], 0))
    if kind == "circle":
        return Edge.make_circle(entity["r"], frame)
    start = entity["a0"]
    end = entity["a1"]
    while end <= start:
        end += TWO_PI
    return Edge.make_circle(entity["r"], frame, start_angle=math.degrees(start), end_angle=math.degrees(end))


def _split(edges: list[Edge]):
    """The cells of the arrangement, plus the frame they were cut from."""
    box = Compound(edges).bounding_box()
    margin = max(box.size.X, box.size.Y) * 0.25 + 1
    centre = box.center()
    frame = Face.make_rect(box.size.X + 2 * margin, box.size.Y + 2 * margin, Plane(origin=(centre.X, centre.Y, 0)))
    frame_arguments, tools = ShapeList(), ShapeList()
    frame_arguments.Append(frame.wrapped)
    for edge in edges:
        tools.Append(edge.wrapped)
    splitter = BRepAlgoAPI_Splitter()
    splitter.SetArguments(frame_arguments)
    splitter.SetTools(tools)
    splitter.SetFuzzyValue(FUZZY)
    splitter.Build()
    if not splitter.IsDone():
        raise ProfileError("could not find the closed regions of this sketch")
    return list(Compound(splitter.Shape()).faces()), frame.bounding_box()


def _touches(cell: Face, frame_box) -> bool:
    """The outside cell runs to the frame. A real region never does, because
    the frame is a quarter of the sketch wider on every side."""
    box = cell.bounding_box()
    return (
        box.min.X <= frame_box.min.X + FUZZY
        or box.max.X >= frame_box.max.X - FUZZY
        or box.min.Y <= frame_box.min.Y + FUZZY
        or box.max.Y >= frame_box.max.Y - FUZZY
    )


def _clean(cell: Face) -> Face:
    """Drops what the splitter leaves inside a cell. A line that ends inside a
    region stays behind as a wire that is not closed, and a face carrying it
    extrudes to an invalid solid."""
    face = Face(cell.outer_wire(), [wire for wire in cell.inner_wires() if wire.is_closed])
    return face if face.normal_at().Z > 0 else Face(Wire(face.outer_wire().edges()).reversed(), face.inner_wires())


def _points(wire: Wire) -> list[tuple[float, float]]:
    """A wire as a polyline, one vertex per tessellation step, in wire order.

    An edge may be stored in either direction, so each one is laid down from
    whichever of its ends is nearer to where the polyline stopped. The ends of
    neighbouring edges rarely coincide exactly: a sketch whose coordinates were
    rounded to 6 decimals leaves gaps of about 1e-7. Testing for an exact match
    reversed nearly every edge of such a wire and drew a self-crossing outline.
    The first edge has nothing before it, so it is judged against the second.
    """
    edges = wire.edges()
    points: list[tuple[float, float]] = []
    for index, edge in enumerate(edges):
        steps = 1 if edge.geom_type.name == "LINE" else 24
        along = [edge.position_at(step / steps) for step in range(steps + 1)]
        if points:
            here = Vector(*points[-1], 0)
            if (here - along[-1]).length < (here - along[0]).length:
                along.reverse()
        elif len(edges) > 1:
            following = [edges[1].position_at(0), edges[1].position_at(1)]
            end_gap = min((along[-1] - point).length for point in following)
            start_gap = min((along[0] - point).length for point in following)
            if start_gap < end_gap:
                along.reverse()
        points.extend((point.X, point.Y) for point in along[1 if points else 0:])
    return points


def _signed_area(points) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def _counter_clockwise(points):
    return points if _signed_area(points) >= 0 else points[::-1]


def _interior_point(face: Face) -> tuple[float, float]:
    """The centre of the face's largest triangle. A face's own centre can sit
    outside it: a crescent's does."""
    vertices, triangles = face.tessellate(0.01)
    best, best_area = None, -1.0
    for a, b, c in triangles:
        area = abs((vertices[b].X - vertices[a].X) * (vertices[c].Y - vertices[a].Y)
                   - (vertices[c].X - vertices[a].X) * (vertices[b].Y - vertices[a].Y))
        if area > best_area:
            best_area = area
            best = (
                (vertices[a].X + vertices[b].X + vertices[c].X) / 3,
                (vertices[a].Y + vertices[b].Y + vertices[c].Y) / 3,
            )
    return best


def _owner(edge: Edge, source: list[tuple[str, Edge]]) -> str | None:
    """The entity a split edge came from: the one its midpoint lies on."""
    midpoint = edge.position_at(0.5)
    nearest_id, nearest = None, ON_ENTITY
    for entity_id, source_edge in source:
        distance = source_edge.distance_to(midpoint)
        if distance < nearest:
            nearest_id, nearest = entity_id, distance
    return nearest_id


def find_regions(entities: list[dict]) -> list[Region]:
    """Every closed region of `entities`, construction geometry ignored.

    Ordered by bounding ids then position, so two runs agree. The order is for
    display only. A region is named by `bounded_by` and `inside`.
    """
    source = [(entity["id"], entity_edge(entity)) for entity in entities if not entity.get("construction")]
    if not source:
        return []
    cells, frame_box = _split([edge for _, edge in source])

    regions = []
    for cell in cells:
        if _touches(cell, frame_box):
            continue
        face = _clean(cell)
        bounded_by = sorted({_owner(edge, source) for edge in face.edges()} - {None})
        regions.append(
            Region(
                face=face,
                bounded_by=bounded_by,
                inside=_interior_point(face),
                outline=_counter_clockwise(_points(face.outer_wire())),
                holes=[_points(wire) for wire in face.inner_wires()],
                area=face.area,
            )
        )
    regions.sort(key=lambda region: (region.bounded_by, round(region.inside[0], 6), round(region.inside[1], 6)))
    return regions


def _contains(region: Region, point: tuple[float, float]) -> bool:
    classifier = BRepClass_FaceClassifier(region.face.wrapped, gp_Pnt(point[0], point[1], 0), FUZZY)
    return classifier.State() == TopAbs_IN


def pick_region(regions: list[Region], reference: dict) -> Region:
    """Resolves `{"bounded_by": [...], "inside": [u, v]}` to one region.

    `bounded_by` alone can match two regions: a circle across one side of a
    rectangle leaves an outside half and an inside half, both bounded by that
    side and the circle. `inside` breaks the tie. A reference that matches
    nothing is the sketch analogue of a selector count mismatch, so it raises
    rather than picking something else.
    """
    wanted = sorted(reference["bounded_by"])
    matches = [region for region in regions if region.bounded_by == wanted]
    if len(matches) > 1:
        point = reference.get("inside")
        if point is None:
            raise ProfileError(f"profile bounded by {wanted} matches {len(matches)} regions, and gives no 'inside' point to choose one")
        matches = [region for region in matches if _contains(region, tuple(point))]
    if len(matches) != 1:
        raise ProfileError(f"profile bounded by {wanted} matches {len(matches)} regions, expected exactly 1")
    return matches[0]
