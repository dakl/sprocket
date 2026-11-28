"""The hole op: simple, counterbore, countersink and tapped holes."""

import math
from typing import TYPE_CHECKING

from build123d import Align, Cone, Cylinder, Part, Plane, Shape, Vector

from .registry import register
from .sketch import resolve_plane, sketch_entities
from .thread import THREAD_PROPERTIES, ThreadJson, ThreadSpec, bore, checked, face_thread, modeled

if TYPE_CHECKING:
    from sprocket_engine.replay import Context

# How close, in mm and as a cosine, a bore must sit to the hole it came from.
ON_AXIS = 1e-6

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
DOWN = (Align.CENTER, Align.CENTER, Align.MAX)

SCHEMA = {
    "type": "object",
    "description": (
        "Drills holes into a solid: simple, counterbore or countersink. Place them at "
        "`points` along "
        "`direction`, or at the centres of the circles in a `sketch`, drilled into "
        "the face the sketch sits "
        "on (against the sketch normal). Give `depth`, or `through: true` to cut "
        "through the whole part. `thread` taps each hole: its bore gets the same "
        "thread a thread op would give it, starting at the hole's mouth."
    ),
    "required": ["id", "type", "base"],
    "oneOf": [{"required": ["points"]}, {"required": ["sketch"]}],
    "anyOf": [{"required": ["diameter"]}, {"required": ["thread"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "hole"},
        "base": {"type": "string", "description": "id of the feature being drilled"},
        "sketch": {
            "type": "string",
            "description": ("id of a sketch feature whose circle centres place the holes"),
        },
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Only the circles with these ids. Default: every circle in the sketch "
                "that is not construction."
            ),
        },
        "points": {
            "type": "array",
            "items": VECTOR3,
            "minItems": 1,
            "description": "hole centres on the surface, world mm",
        },
        "direction": {
            **VECTOR3,
            "description": "drilling direction for `points`, default [0, 0, -1] (down)",
        },
        "kind": {
            "enum": ["simple", "counterbore", "countersink"],
            "description": "default simple",
        },
        "diameter": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, the drilled bore. A tapped hole defaults to the tap drill.",
        },
        "depth": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, from the surface to the bottom of the bore",
        },
        "through": {
            "type": "boolean",
            "description": "cut through the whole part, ignoring depth",
        },
        "head_diameter": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, counterbore or countersink diameter",
        },
        "head_depth": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, counterbore depth",
        },
        "head_angle": {
            "type": "number",
            "exclusiveMinimum": 0,
            "exclusiveMaximum": 180,
            "description": "degrees, countersink included angle, default 90",
        },
        "thread": {
            "type": "object",
            "description": (
                "Taps the holes. The bore defaults to the tap drill, the nominal less "
                "one pitch, and `length` defaults to the whole bore."
            ),
            "required": ["designation"],
            "additionalProperties": False,
            "properties": THREAD_PROPERTIES,
        },
    },
}


def _centres(feature: dict, ctx) -> list[tuple[Vector, Vector]]:
    """Each hole as (centre, drilling direction) in world space."""
    if "points" in feature:
        direction = Vector(*feature.get("direction", (0, 0, -1))).normalized()
        return [(Vector(*point), direction) for point in feature["points"]]
    sketch = ctx.features.get(feature["sketch"])
    if sketch is None or sketch.get("type") != "sketch":
        raise ValueError(
            f"hole sketch {feature['sketch']!r} is not a sketch earlier in the document"
        )
    plane = resolve_plane(sketch["plane"], ctx)
    if "entities" in sketch:
        wanted = feature.get("entities")
        uvs = [
            entity["c"]
            for entity in sketch_entities(sketch)
            if entity["kind"] == "circle"
            and (entity["id"] in wanted if wanted is not None else not entity.get("construction"))
        ]
    elif "circle" in sketch:
        uvs = [sketch["circle"].get("center", [0, 0])]
    else:
        uvs = []
    if not uvs:
        raise ValueError(f"sketch {feature['sketch']!r} has no circles to place holes at")
    direction = -plane.z_dir
    return [(plane.from_local_coords((u, v)), direction) for u, v in uvs]


def _tap_spec(feature: dict) -> ThreadSpec | None:
    if "thread" not in feature:
        return None
    return ThreadSpec.parse(f"hole {feature['id']}", feature["thread"])


def _diameter(feature: dict) -> float:
    spec = _tap_spec(feature)
    if "diameter" in feature or spec is None:
        return feature["diameter"]
    return round(spec.major - spec.pitch, 6)


def _tool(feature: dict, depth: float):
    """One hole in its own frame: the surface at z = 0, drilled toward -Z."""
    radius = _diameter(feature) / 2
    tool = Cylinder(radius, depth, align=DOWN)
    kind = feature.get("kind", "simple")
    if kind == "simple":
        return tool
    if "head_diameter" not in feature:
        raise ValueError(f"a {kind} hole needs head_diameter")
    head_radius = feature["head_diameter"] / 2
    if head_radius <= radius:
        raise ValueError("head_diameter must be larger than diameter")
    if kind == "counterbore":
        if "head_depth" not in feature:
            raise ValueError("a counterbore hole needs head_depth")
        return tool + Cylinder(head_radius, feature["head_depth"], align=DOWN)
    half_angle = math.radians(feature.get("head_angle", 90)) / 2
    height = (head_radius - radius) / math.tan(half_angle)
    return tool + Cone(radius, head_radius, height, align=DOWN)


@register("hole", SCHEMA)
def apply(feature: dict, ctx: "Context") -> Part:
    """Drill the holes, and tap them when the feature has a `thread`."""
    base = ctx.results[feature["base"]]
    if feature.get("through"):
        depth = 2 * base.bounding_box().diagonal
    elif "depth" in feature:
        depth = feature["depth"]
    else:
        raise ValueError("a hole needs depth, or through: true")
    shape = _tool(feature, depth)
    centres = _centres(feature, ctx)
    tools = [Plane(origin=centre, z_dir=-direction) * shape for centre, direction in centres]
    result = Part(base.cut(*tools).solids())
    spec = _tap_spec(feature)
    if spec is None:
        return result
    # Found even for a cosmetic thread, so a bore the wrong size fails here.
    bores = _bore_threads(feature, result, centres, spec)
    if not spec.modeled:
        return result
    threaded = result
    for geometry in bores:
        threaded = modeled(threaded, geometry, spec)
    return checked(threaded, result, spec.label)


def _bore_threads(
    feature: dict,
    drilled: Shape,
    centres: list[tuple[Vector, Vector]],
    spec: ThreadSpec,
) -> list[ThreadJson]:
    """The thread geometry of each hole's bore in the drilled part."""
    radius = _diameter(feature) / 2
    found = []
    for centre, direction in centres:
        for face in drilled.faces():
            axis = bore(face)
            if axis is None or not axis.internal or abs(axis.radius - radius) > ON_AXIS:
                continue
            offset = centre - axis.origin
            off_axis = offset - axis.direction * offset.dot(axis.direction)
            if abs(abs(axis.direction.dot(direction)) - 1) > ON_AXIS or off_axis.length > ON_AXIS:
                continue
            found.append(face_thread(face, spec, entry=centre))
    if len(found) < len(centres):
        raise ValueError(f"{spec.label}: found {len(found)} bores to tap for {len(centres)} holes")
    return found


def hole_threads(feature: dict, ctx: "Context") -> list[ThreadJson]:
    """The cosmetic threads of a tapped hole, for the viewer, read from its result."""
    spec = _tap_spec(feature)
    if spec is None or spec.modeled:
        return []
    centres = _centres(feature, ctx)
    return _bore_threads(feature, ctx.results[feature["id"]], centres, spec)
