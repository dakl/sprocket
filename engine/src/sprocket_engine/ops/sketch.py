import math

from build123d import Circle, Compound, Plane, Polygon, Pos, Rectangle, Sketch
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

from ..regions import find_regions
from .registry import register

ORIGIN_PLANES = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
POINT2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}

PLANE_SCHEMA = {
    "oneOf": [
        {"enum": ["XY", "XZ", "YZ"]},
        {
            "type": "object",
            "required": ["origin", "normal", "x_dir"],
            "properties": {"origin": VECTOR3, "normal": VECTOR3, "x_dir": VECTOR3},
        },
    ]
}

# A sketch drawn in the GUI: entities plus the constraints that placed them. The
# engine does not solve constraints. The viewer's solver already placed every
# entity, so replay trusts the geometry and keeps the constraints so the sketch
# can be reopened and solved again.
ENTITY_SCHEMA = {
    "type": "object",
    "required": ["id", "kind"],
    "properties": {
        "id": {"type": "string", "description": "Stable for the life of the sketch. Profiles and constraints refer to it."},
        "kind": {"enum": ["line", "arc", "circle"]},
        "p0": {**POINT2, "description": "line start"},
        "p1": {**POINT2, "description": "line end"},
        "c": {**POINT2, "description": "arc or circle centre"},
        "r": {"type": "number", "exclusiveMinimum": 0, "description": "arc or circle radius"},
        "a0": {"type": "number", "description": "arc start angle in radians, counter-clockwise from the plane's x axis"},
        "a1": {"type": "number", "description": "arc end angle in radians. The arc runs counter-clockwise from a0 to a1."},
        "construction": {"type": "boolean", "description": "Reference geometry. It bounds no region."},
    },
}

# Kind -> the fields it needs. A reference is an entity id, or `<id>.<point>`
# where point is p0 or p1 (line, arc) or c (arc, circle).
CONSTRAINT_FIELDS = {
    "coincident": ("a", "b"),
    "horizontal": (),
    "vertical": (),
    "parallel": ("a", "b"),
    "perpendicular": ("a", "b"),
    "tangent": ("a", "b"),
    "equal": ("a", "b"),
    "fix": ("e",),
    "distance": ("a", "b", "value"),
    "radius": ("e", "value"),
    "diameter": ("e", "value"),
    "angle": ("a", "b", "value"),
}

CONSTRAINT_SCHEMA = {
    "type": "object",
    "required": ["kind"],
    "properties": {
        "kind": {"enum": sorted(CONSTRAINT_FIELDS)},
        "e": {"type": "string", "description": "the one entity or point a constraint acts on"},
        "a": {"type": "string", "description": "first entity or point"},
        "b": {"type": "string", "description": "second entity or point"},
        "value": {"type": "number", "description": "mm for distance, radius and diameter; degrees for angle"},
    },
}

SCHEMA = {
    "type": "object",
    "description": (
        "A flat drawing on a plane. Not a solid on its own: something has to extrude or revolve it. "
        "Give `entities` for a drawing of lines, arcs and circles, and every closed region of it "
        "becomes a profile an extrude can pick. Give `rect`, `polygon` or `circle` for a single shape."
    ),
    "required": ["id", "type", "plane"],
    "oneOf": [{"required": ["rect"]}, {"required": ["polygon"]}, {"required": ["circle"]}, {"required": ["entities"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "sketch"},
        # A principal plane by name, or an arbitrary plane (e.g. picked off a
        # part face) given as an origin point plus a normal and an in-plane
        # x-axis - the same three vectors build123d's Plane() takes.
        "plane": PLANE_SCHEMA,
        "rect": {
            "type": "object",
            "required": ["width", "height"],
            "properties": {
                "width": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "default": [0, 0],
                },
            },
        },
        # Closed polygon, as [u, v] points local to `plane`. The sketcher UI
        # emits this for a freeform or rectangle draw instead of `rect`.
        "polygon": {"type": "array", "items": POINT2, "minItems": 3},
        "entities": {"type": "array", "items": ENTITY_SCHEMA},
        "constraints": {"type": "array", "items": CONSTRAINT_SCHEMA},
        # A dimensioned circle. Radius rather than diameter, because every
        # other length in a document is the distance the op actually uses,
        # and a UI that wants diameter can halve it once.
        "circle": {
            "type": "object",
            "required": ["radius"],
            "properties": {
                "radius": {"type": "number", "exclusiveMinimum": 0},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "default": [0, 0],
                },
            },
        },
    },
}


def resolve_plane(plane_ref):
    if isinstance(plane_ref, str):
        return ORIGIN_PLANES[plane_ref]
    return Plane(origin=tuple(plane_ref["origin"]), z_dir=tuple(plane_ref["normal"]), x_dir=tuple(plane_ref["x_dir"]))


def _polygon_area(points) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))) / 2


def _counter_clockwise(points):
    """Returns `points` wound counter-clockwise in the plane's own uv axes.

    A face's normal follows its winding, so a clockwise polygon builds a face
    pointing away from the sketch plane's normal, and `extrude` with a positive
    distance then runs backwards into the part instead of out of it. The
    sketcher UI emits whichever winding the user happened to draw, so normalise
    it here and the sign of `distance` means the same thing for every sketch.
    """
    signed_area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
    return points if signed_area >= 0 else points[::-1]


_POINTS = {"line": {"p0", "p1"}, "arc": {"p0", "p1", "c"}, "circle": {"c"}}
_ENTITY_FIELDS = {"line": ("p0", "p1"), "arc": ("c", "r", "a0", "a1"), "circle": ("c", "r")}


def check_entities(feature: dict) -> None:
    """The checks a JSON Schema cannot make: fields per kind, unique ids, and
    constraints that name something that exists. A constraint on a deleted
    entity would otherwise replay fine and break the next time the viewer
    solves the sketch."""
    kinds = {}
    for entity in feature["entities"]:
        entity_id = entity["id"]
        if entity_id in kinds:
            raise ValueError(f"sketch {feature['id']!r} has two entities with id {entity_id!r}")
        missing = [name for name in _ENTITY_FIELDS[entity["kind"]] if name not in entity]
        if missing:
            raise ValueError(f"{entity['kind']} {entity_id!r} needs {', '.join(missing)}")
        kinds[entity_id] = entity["kind"]

    def check_reference(constraint, name):
        reference = constraint[name]
        entity_id, _, point = reference.partition(".")
        if entity_id not in kinds:
            raise ValueError(f"constraint {constraint['kind']!r} refers to {reference!r}, and there is no entity {entity_id!r}")
        if point and point not in _POINTS[kinds[entity_id]]:
            raise ValueError(f"constraint {constraint['kind']!r} refers to {reference!r}, but a {kinds[entity_id]} has no point {point!r}")

    for constraint in feature.get("constraints", []):
        wanted = CONSTRAINT_FIELDS[constraint["kind"]]
        missing = [name for name in wanted if name not in constraint]
        if missing:
            raise ValueError(f"constraint {constraint['kind']!r} needs {', '.join(missing)}")
        # horizontal and vertical act on one line, or on two points.
        if constraint["kind"] in ("horizontal", "vertical") and not ("e" in constraint or ("a" in constraint and "b" in constraint)):
            raise ValueError(f"constraint {constraint['kind']!r} needs 'e', or 'a' and 'b'")
        for name in ("a", "b", "e"):
            if name in constraint:
                check_reference(constraint, name)


def _round(points):
    return [[round(u, 6), round(v, 6)] for u, v in points]


def sketch_profiles(feature: dict) -> list[dict]:
    """The regions a user can pick from `feature`, as plain JSON.

    An entity sketch gives one per closed region. A sketch of one shape gives
    one with no boundary ids, which `extrude` reads as "the whole sketch". Each
    carries an outline and its holes in the sketch's own [u, v] frame, so the
    viewer can fill it and preview an extrude without asking the engine again.
    """
    if "entities" in feature:
        return [
            {
                "bounded_by": region.bounded_by,
                "inside": [round(region.inside[0], 6), round(region.inside[1], 6)],
                "outline": _round(region.outline),
                "holes": [_round(hole) for hole in region.holes],
                "area": round(region.area, 6),
            }
            for region in find_regions(feature["entities"])
        ]
    if "polygon" in feature:
        outline = _counter_clockwise([tuple(point) for point in feature["polygon"]])
        area = _polygon_area(outline)
    elif "circle" in feature:
        (cx, cy), radius = feature["circle"].get("center", [0, 0]), feature["circle"]["radius"]
        outline = [(cx + radius * math.cos(step * math.tau / 48), cy + radius * math.sin(step * math.tau / 48)) for step in range(48)]
        area = math.pi * radius**2
    else:
        rect = feature["rect"]
        (cx, cy), half_w, half_h = rect.get("center", [0, 0]), rect["width"] / 2, rect["height"] / 2
        outline = [(cx - half_w, cy - half_h), (cx + half_w, cy - half_h), (cx + half_w, cy + half_h), (cx - half_w, cy + half_h)]
        area = rect["width"] * rect["height"]
    # The mean of the outline is inside every shape a legacy sketch can be
    # except a concave polygon, which is why `extrude` ignores `inside` here.
    inside = [sum(point[axis] for point in outline) / len(outline) for axis in (0, 1)]
    return [{"bounded_by": [], "inside": _round([inside])[0], "outline": _round(outline), "holes": [], "area": round(area, 6)}]


def _empty_sketch() -> Sketch:
    """A sketch with nothing closed in it yet, such as a lone construction line.
    build123d's own empty Sketch has no shape underneath, so the BREP cache
    cannot write it."""
    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    return Sketch(compound)


@register("sketch", SCHEMA)
def apply(feature: dict, ctx):
    plane = resolve_plane(feature["plane"])
    if "entities" in feature:
        check_entities(feature)
        regions = find_regions(feature["entities"])
        # Every closed region, side by side: an extrude of the whole sketch
        # then gives the union of what was drawn, and a profile pick gives one.
        return Sketch(plane * Compound([region.face for region in regions])) if regions else _empty_sketch()
    if "polygon" in feature:
        points = _counter_clockwise([tuple(point) for point in feature["polygon"]])
        return plane * Polygon(*points)
    if "circle" in feature:
        circle = feature["circle"]
        cx, cy = circle.get("center", [0, 0])
        return plane * Pos(cx, cy) * Circle(circle["radius"])
    rect = feature["rect"]
    cx, cy = rect.get("center", [0, 0])
    return plane * Pos(cx, cy) * Rectangle(rect["width"], rect["height"])
