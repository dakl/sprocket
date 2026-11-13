import math

from build123d import (
    Circle,
    Compound,
    GeomType,
    Plane,
    Polygon,
    Pos,
    Rectangle,
    Sketch,
    Vector,
    Wire,
)
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.GeomLib import GeomLib_IsPlanarSurface
from OCP.TopoDS import TopoDS_Compound

from ..regions import FUZZY, ProfileError, entity_edge, find_regions
from ..selectors import SELECTOR_DESCRIPTION, SelectorError, resolve_selector
from .registry import register

ORIGIN_PLANES = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
POINT2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}

OFFSET = {"type": "number", "description": "mm along the plane normal, default 0"}

PLANE_SCHEMA = {
    "oneOf": [
        {"enum": ["XY", "XZ", "YZ"]},
        {
            "type": "object",
            "required": ["origin", "normal", "x_dir"],
            "description": (
                "An explicit plane. The sketch's v axis is normal x x_dir (right-handed), so u runs right and v up "
                "when you look at the sketch from the side its normal points to. A plane facing -Z with x_dir +X "
                "therefore has v along world -Y: it is the part seen from below."
            ),
            "properties": {"origin": VECTOR3, "normal": VECTOR3, "x_dir": VECTOR3},
        },
        {
            "type": "object",
            "required": ["name"],
            "description": 'An origin plane moved along its normal, as {"name": "XY", "offset": 5}.',
            "properties": {"name": {"enum": ["XY", "XZ", "YZ"]}, "offset": OFFSET},
        },
        {
            "type": "object",
            "required": ["construct"],
            "description": 'A construct plane feature (type `plane`), optionally moved along its normal: {"construct": "f3", "offset": 2}.',
            "properties": {
                "construct": {"type": "string", "description": "id of a plane feature"},
                "offset": OFFSET,
            },
        },
        {
            "type": "object",
            "required": ["feature", "face"],
            "description": (
                "On a planar face of an earlier feature, found again on every rebuild, so the sketch moves with "
                "the face. The normal points out of the part. Sketch coordinates stay world-aligned: the origin is "
                "the world origin projected onto the face. On a horizontal face u is world +X. On a vertical face "
                "v is world +Z (up)."
            ),
            "properties": {
                "feature": {
                    "type": "string",
                    "description": "the feature whose result holds the face",
                },
                "face": {"type": "object", "description": SELECTOR_DESCRIPTION},
                "offset": OFFSET,
            },
        },
    ]
}

# A sketch drawn in the GUI: entities plus the constraints that placed them.
# Replay does not solve constraints: whoever last edited the sketch, the viewer's
# solver or sketch_edit.py's, already placed every entity, so replay trusts the
# geometry and keeps the constraints so the sketch can be solved again.
ENTITY_SCHEMA = {
    "type": "object",
    "required": ["id", "kind"],
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable for the life of the sketch. Profiles and constraints refer to it.",
        },
        "kind": {"enum": ["line", "arc", "circle"]},
        "p0": {**POINT2, "description": "line start"},
        "p1": {**POINT2, "description": "line end"},
        "c": {**POINT2, "description": "arc or circle centre"},
        "r": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "arc or circle radius",
        },
        "a0": {
            "type": "number",
            "description": "arc start angle in radians, counter-clockwise from the plane's x axis",
        },
        "a1": {
            "type": "number",
            "description": "arc end angle in radians. The arc runs counter-clockwise from a0 to a1.",
        },
        "construction": {
            "type": "boolean",
            "description": "Reference geometry. It bounds no region.",
        },
        "reference": {
            "type": "object",
            "description": (
                "Marks a projected reference: this entity is an edge of an earlier sketch or body, projected onto this "
                "sketch's plane and locked. Its numbers are refreshed from the source on every rebuild, so do not edit them. "
                '{"sketch": "f1", "entity": "e3"} names an entity of an earlier sketch. '
                '{"feature": "f2", "edge": <selector>} names one straight or circular edge of that feature\'s body.'
            ),
        },
    },
}

# What the sketch plane gives every sketch. `origin` is a point, and a constraint
# may name it, as in `{"kind": "coincident", "a": "e1.c", "b": "origin"}`.
# `axis_u` and `axis_v` are the lines through it along the plane's x and y
# directions, for `{"kind": "point_on", "a": "e1.p0", "b": "axis_u"}`.
ORIGIN = "origin"
PLANE_REFERENCES = (ORIGIN, "axis_u", "axis_v")

# Kind -> the fields it needs. A reference is an entity id, `<id>.<point>`
# where point is p0 or p1 (line, arc) or c (arc, circle), or `origin`.
CONSTRAINT_FIELDS = {
    "coincident": ("a", "b"),
    "point_on": ("a", "b"),
    "horizontal": (),
    "vertical": (),
    "parallel": ("a", "b"),
    "perpendicular": ("a", "b"),
    "tangent": ("a", "b"),
    "equal": ("a", "b"),
    "fix": ("e",),
    # Points a and b are mirror images across the line e.
    "symmetric": ("a", "b", "e"),
    "distance": ("a", "b", "value"),
    # b's u (distance_x) or v (distance_y) minus a's. Signed, so a before b gives a positive value.
    "distance_x": ("a", "b", "value"),
    "distance_y": ("a", "b", "value"),
    "radius": ("e", "value"),
    "diameter": ("e", "value"),
    "angle": ("a", "b", "value"),
}

CONSTRAINT_SCHEMA = {
    "type": "object",
    "required": ["kind"],
    "properties": {
        "id": {
            "type": "string",
            "description": "c<n>. Stable for the life of the sketch. Filled in when missing.",
        },
        "kind": {"enum": sorted(CONSTRAINT_FIELDS)},
        "e": {
            "type": "string",
            "description": "the one entity or point a constraint acts on. For symmetric, the line the points mirror across",
        },
        "a": {
            "type": "string",
            "description": "first entity or point. For point_on, the point. For symmetric, a point",
        },
        "b": {
            "type": "string",
            "description": "second entity or point. For point_on, the line, circle or arc it lies on",
        },
        "value": {
            "type": "number",
            "description": "mm for distance, distance_x, distance_y, radius and diameter; degrees for angle",
        },
    },
}

SKETCH_LINEAR = {
    "type": "object",
    "required": ["count", "step"],
    "properties": {
        "count": {
            "type": "integer",
            "minimum": 1,
            "description": "copies, the original included",
        },
        "step": {**POINT2, "description": "[du, dv] between neighbouring copies"},
    },
}

# Copies of entities, made before regions are found. A copy of `e1` is `e1#1`,
# `e1#2` and so on, which is the id a profile's bounded_by names.
SKETCH_PATTERN_SCHEMA = {
    "type": "object",
    "required": ["entities"],
    "oneOf": [{"required": ["linear"]}, {"required": ["circular"]}],
    "properties": {
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "ids of the entities to copy",
        },
        "linear": SKETCH_LINEAR,
        "linear2": {
            **SKETCH_LINEAR,
            "description": "a second direction, which makes a grid",
        },
        "circular": {
            "type": "object",
            "required": ["center", "count"],
            "properties": {
                "center": POINT2,
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "copies, the original included",
                },
                "angle": {
                    "type": "number",
                    "description": "degrees spanned, default 360 (copies spread evenly)",
                },
            },
        },
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
    "oneOf": [
        {"required": ["rect"]},
        {"required": ["polygon"]},
        {"required": ["circle"]},
        {"required": ["entities"]},
    ],
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
        "patterns": {"type": "array", "items": SKETCH_PATTERN_SCHEMA},
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


def resolve_plane(plane_ref, ctx=None):
    """The build123d Plane a sketch sits on. A face plane needs `ctx`, the
    replay context, to find the face in an earlier feature's result."""
    if isinstance(plane_ref, str):
        return ORIGIN_PLANES[plane_ref]
    if "name" in plane_ref:
        plane = ORIGIN_PLANES[plane_ref["name"]]
    elif "construct" in plane_ref:
        from .construct import construct_plane

        plane = construct_plane(plane_ref["construct"], ctx)
    elif "feature" in plane_ref:
        plane = _face_plane(plane_ref, ctx)
    else:
        return Plane(
            origin=tuple(plane_ref["origin"]),
            z_dir=tuple(plane_ref["normal"]),
            x_dir=tuple(plane_ref["x_dir"]),
        )
    return plane.offset(plane_ref.get("offset", 0))


def _face_plane(plane_ref, ctx) -> Plane:
    feature_id = plane_ref["feature"]
    shape = ctx.results.get(feature_id) if ctx is not None else None
    if shape is None:
        raise ValueError(
            f"sketch plane names {feature_id!r}, which is not built before this sketch"
        )
    faces = resolve_selector(shape, plane_ref["face"], ctx)
    if len(faces) != 1:
        raise SelectorError(
            f"sketch plane face selector matched {len(faces)} faces, expected exactly 1"
        )
    face = faces[0]
    # A ruled loft's flat sides are B-spline surfaces, so ask whether it is flat, not what type it is.
    if (
        face.geom_type != GeomType.PLANE
        and not GeomLib_IsPlanarSurface(
            BRep_Tool.Surface_s(face.wrapped), 1e-7
        ).IsPlanar()
    ):
        raise ValueError(
            f"a sketch needs a planar face, and the selector found a {face.geom_type.name.lower()} face"
        )
    normal = face.normal_at(face.center()).normalized()
    origin = normal * normal.dot(face.center())
    if abs(normal.Z) > 0.999:
        x_dir = Vector(1, 0, 0)
    else:
        # A vertical or slanted face: v (= normal x x_dir) points as close to world up as it can.
        x_dir = Vector(0, 0, 1).cross(normal).normalized()
    return Plane(origin=origin, z_dir=normal, x_dir=x_dir)


def plane_json(plane: Plane) -> dict:
    """A Plane as the explicit {origin, normal, x_dir} form, which is what the viewer draws on."""
    return {
        "origin": [round(value, 9) for value in plane.origin],
        "normal": [round(value, 9) for value in plane.z_dir],
        "x_dir": [round(value, 9) for value in plane.x_dir],
    }


def _polygon_area(points) -> float:
    return (
        abs(
            sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
            )
        )
        / 2
    )


def _counter_clockwise(points):
    """Returns `points` wound counter-clockwise in the plane's own uv axes.

    A face's normal follows its winding, so a clockwise polygon builds a face
    pointing away from the sketch plane's normal, and `extrude` with a positive
    distance then runs backwards into the part instead of out of it. The
    sketcher UI emits whichever winding the user happened to draw, so normalise
    it here and the sign of `distance` means the same thing for every sketch.
    """
    signed_area = sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )
    return points if signed_area >= 0 else points[::-1]


def _moved(entity: dict, index: int, du: float, dv: float, turn: float, centre) -> dict:
    """A copy of `entity` turned by `turn` radians about `centre`, then shifted by (du, dv)."""
    cos, sin = math.cos(turn), math.sin(turn)

    def point(uv):
        u, v = uv[0] - centre[0], uv[1] - centre[1]
        return [centre[0] + u * cos - v * sin + du, centre[1] + u * sin + v * cos + dv]

    copy = {**entity, "id": f"{entity['id']}#{index}"}
    for name in ("p0", "p1", "c"):
        if name in entity:
            copy[name] = point(entity[name])
    for name in ("a0", "a1"):
        if name in entity:
            copy[name] = entity[name] + turn
    return copy


def sketch_entities(feature: dict) -> list[dict]:
    """The sketch's entities with every pattern copy added after them."""
    entities = list(feature.get("entities", []))
    by_id = {entity["id"]: entity for entity in entities}
    for pattern in feature.get("patterns", []):
        missing = [
            entity_id for entity_id in pattern["entities"] if entity_id not in by_id
        ]
        if missing:
            raise ValueError(
                f"sketch pattern names {', '.join(missing)}, which are not entities of this sketch"
            )
        if "linear" in pattern:
            first, second = (
                pattern["linear"],
                pattern.get("linear2", {"count": 1, "step": [0, 0]}),
            )
            moves = [
                (
                    i * first["step"][0] + j * second["step"][0],
                    i * first["step"][1] + j * second["step"][1],
                    0.0,
                    (0, 0),
                )
                for i in range(first["count"])
                for j in range(second["count"])
            ]
        else:
            circular = pattern["circular"]
            angle, count = circular.get("angle", 360), circular["count"]
            step = angle / count if angle % 360 == 0 else angle / max(count - 1, 1)
            moves = [
                (0.0, 0.0, math.radians(step * k), circular["center"])
                for k in range(count)
            ]
        for index, (du, dv, turn, centre) in enumerate(moves[1:], start=1):
            entities.extend(
                _moved(by_id[entity_id], index, du, dv, turn, centre)
                for entity_id in pattern["entities"]
            )
    return entities


PATH_SCHEMA = {
    "type": "object",
    "description": "Entities of a sketch that join end to end into one path.",
    "required": ["sketch"],
    "properties": {
        "sketch": {
            "type": "string",
            "description": "id of a sketch feature with entities",
        },
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "ids of the entities on the path. Default: every entity that is not construction.",
        },
    },
}


def sketch_path(path_ref: dict, ctx) -> Wire:
    """The wire a `PATH_SCHEMA` reference names, placed on its sketch's plane."""
    sketch = ctx.features.get(path_ref["sketch"])
    if sketch is None or sketch["type"] != "sketch" or "entities" not in sketch:
        raise ProfileError(
            f"path refers to {path_ref['sketch']!r}, which is not a sketch with entities earlier in the document"
        )
    entities = sketch_entities(sketch)
    wanted = path_ref.get("entities")
    if wanted:
        known = {entity["id"] for entity in entities}
        missing = [entity_id for entity_id in wanted if entity_id not in known]
        if missing:
            raise ProfileError(
                f"path names {', '.join(missing)}, which are not entities of sketch {path_ref['sketch']!r}"
            )
        entities = [entity for entity in entities if entity["id"] in wanted]
    else:
        entities = [entity for entity in entities if not entity.get("construction")]
    plane = resolve_plane(sketch["plane"], ctx)
    wires = Wire.combine(
        [plane * entity_edge(entity) for entity in entities], tol=FUZZY
    )
    if len(wires) != 1:
        raise ProfileError(
            f"the path in sketch {path_ref['sketch']!r} is {len(wires)} separate pieces, not one chain"
        )
    return wires[0]


_POINTS = {"line": {"p0", "p1"}, "arc": {"p0", "p1", "c"}, "circle": {"c"}}
_ENTITY_FIELDS = {
    "line": ("p0", "p1"),
    "arc": ("c", "r", "a0", "a1"),
    "circle": ("c", "r"),
}


def check_entities(feature: dict) -> None:
    """The checks a JSON Schema cannot make: fields per kind, unique ids, and
    constraints that name something that exists. A constraint on a deleted
    entity would otherwise replay fine and break the next time the viewer
    solves the sketch."""
    kinds = {}
    for entity in feature["entities"]:
        entity_id = entity["id"]
        if entity_id in kinds:
            raise ValueError(
                f"sketch {feature['id']!r} has two entities with id {entity_id!r}"
            )
        missing = [
            name for name in _ENTITY_FIELDS[entity["kind"]] if name not in entity
        ]
        if missing:
            raise ValueError(
                f"{entity['kind']} {entity_id!r} needs {', '.join(missing)}"
            )
        kinds[entity_id] = entity["kind"]

    def check_reference(constraint, name):
        reference = constraint[name]
        if reference in PLANE_REFERENCES:
            return
        entity_id, _, point = reference.partition(".")
        if entity_id not in kinds:
            raise ValueError(
                f"constraint {constraint['kind']!r} refers to {reference!r}, and there is no entity {entity_id!r}"
            )
        if point and point not in _POINTS[kinds[entity_id]]:
            raise ValueError(
                f"constraint {constraint['kind']!r} refers to {reference!r}, but a {kinds[entity_id]} has no point {point!r}"
            )

    constraint_ids = set()
    for constraint in feature.get("constraints", []):
        constraint_id = constraint.get("id")
        if constraint_id is not None:
            if constraint_id in constraint_ids:
                raise ValueError(
                    f"sketch {feature['id']!r} has two constraints with id {constraint_id!r}"
                )
            constraint_ids.add(constraint_id)
        wanted = CONSTRAINT_FIELDS[constraint["kind"]]
        missing = [name for name in wanted if name not in constraint]
        if missing:
            raise ValueError(
                f"constraint {constraint['kind']!r} needs {', '.join(missing)}"
            )
        # horizontal and vertical act on one line, or on two points.
        if constraint["kind"] in ("horizontal", "vertical") and not (
            "e" in constraint or ("a" in constraint and "b" in constraint)
        ):
            raise ValueError(
                f"constraint {constraint['kind']!r} needs 'e', or 'a' and 'b'"
            )
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
            for region in find_regions(sketch_entities(feature))
        ]
    if "polygon" in feature:
        outline = _counter_clockwise([tuple(point) for point in feature["polygon"]])
        area = _polygon_area(outline)
    elif "circle" in feature:
        (cx, cy), radius = (
            feature["circle"].get("center", [0, 0]),
            feature["circle"]["radius"],
        )
        outline = [
            (
                cx + radius * math.cos(step * math.tau / 48),
                cy + radius * math.sin(step * math.tau / 48),
            )
            for step in range(48)
        ]
        area = math.pi * radius**2
    else:
        rect = feature["rect"]
        (cx, cy), half_w, half_h = (
            rect.get("center", [0, 0]),
            rect["width"] / 2,
            rect["height"] / 2,
        )
        outline = [
            (cx - half_w, cy - half_h),
            (cx + half_w, cy - half_h),
            (cx + half_w, cy + half_h),
            (cx - half_w, cy + half_h),
        ]
        area = rect["width"] * rect["height"]
    # The mean of the outline is inside every shape a legacy sketch can be
    # except a concave polygon, which is why `extrude` ignores `inside` here.
    inside = [sum(point[axis] for point in outline) / len(outline) for axis in (0, 1)]
    return [
        {
            "bounded_by": [],
            "inside": _round([inside])[0],
            "outline": _round(outline),
            "holes": [],
            "area": round(area, 6),
        }
    ]


def _empty_sketch() -> Sketch:
    """A sketch with nothing closed in it yet, such as a lone construction line.
    build123d's own empty Sketch has no shape underneath, so the BREP cache
    cannot write it."""
    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    return Sketch(compound)


@register("sketch", SCHEMA)
def apply(feature: dict, ctx):
    plane = resolve_plane(feature["plane"], ctx)
    if "entities" in feature:
        check_entities(feature)
        regions = find_regions(sketch_entities(feature))
        # Every closed region, side by side: an extrude of the whole sketch
        # then gives the union of what was drawn, and a profile pick gives one.
        return (
            Sketch(plane * Compound([region.face for region in regions]))
            if regions
            else _empty_sketch()
        )
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
