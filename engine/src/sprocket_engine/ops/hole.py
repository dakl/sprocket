import math

from build123d import Align, Compound, Cone, Cylinder, Part, Plane, Vector, Wire

from sprocket_engine.b3d import cut, to_world
from sprocket_engine.context import Context
from sprocket_engine.model import HoleFeature, JSONObject, entity_point
from sprocket_engine.ops.registry import register
from sprocket_engine.ops.sketch import resolve_plane, sketch_entities

VECTOR3: JSONObject = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}
DOWN = (Align.CENTER, Align.CENTER, Align.MAX)

SCHEMA: JSONObject = {
    "type": "object",
    "description": (
        "Drills holes into a solid: simple, counterbore or countersink. Place them at `points` along "
        "`direction`, or at the centres of the circles in a `sketch`, drilled into the face the sketch sits "
        "on (against the sketch normal). Give `depth`, or `through: true` to cut through the whole part."
    ),
    "required": ["id", "type", "base", "diameter"],
    "oneOf": [{"required": ["points"]}, {"required": ["sketch"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "hole"},
        "base": {"type": "string", "description": "id of the feature being drilled"},
        "sketch": {
            "type": "string",
            "description": "id of a sketch feature whose circle centres place the holes",
        },
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only the circles with these ids. Default: every circle in the sketch that is not construction.",
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
            "description": "mm, the drilled bore",
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
    },
}


def _vector(values: list[float]) -> Vector:
    return Vector(values[0], values[1], values[2])


def _centres(feature: HoleFeature, ctx: Context) -> list[tuple[Vector, Vector]]:
    """Each hole as (centre, drilling direction) in world space."""
    if "points" in feature:
        direction = _vector(feature.get("direction", [0, 0, -1])).normalized()
        return [(_vector(point), direction) for point in feature["points"]]
    sketch_id = feature.get("sketch", "")
    sketch = ctx.sketch(sketch_id)
    if sketch is None:
        raise ValueError(
            f"hole sketch {sketch_id!r} is not a sketch earlier in the document"
        )
    plane = resolve_plane(sketch["plane"], ctx)
    if "entities" in sketch:
        wanted = feature.get("entities")
        uvs = [
            entity_point(entity, "c")
            for entity in sketch_entities(sketch)
            if entity["kind"] == "circle"
            and (
                entity["id"] in wanted
                if wanted is not None
                else not entity.get("construction")
            )
        ]
    elif "circle" in sketch:
        uvs = [sketch["circle"].get("center", [0, 0])]
    else:
        uvs = []
    if not uvs:
        raise ValueError(f"sketch {sketch_id!r} has no circles to place holes at")
    direction = -plane.z_dir
    return [(to_world(plane, u, v), direction) for u, v in uvs]


def _solid(shape: Compound | Wire) -> Compound:
    # A fuse of two solids is a solid; build123d's `+` is typed for wires too.
    if not isinstance(shape, Compound):
        msg = "a hole tool came out as a wire"
        raise TypeError(msg)
    return shape


def _tool(feature: HoleFeature, depth: float) -> Compound:
    """One hole in its own frame: the surface at z = 0, drilled toward -Z."""
    radius = feature["diameter"] / 2
    tool = Cylinder(radius, depth, align=DOWN)
    kind = feature.get("kind", "simple")
    if kind == "simple":
        return tool
    if "head_diameter" not in feature:
        msg = f"a {kind} hole needs head_diameter"
        raise ValueError(msg)
    head_radius = feature["head_diameter"] / 2
    if head_radius <= radius:
        raise ValueError("head_diameter must be larger than diameter")
    if kind == "counterbore":
        if "head_depth" not in feature:
            raise ValueError("a counterbore hole needs head_depth")
        return _solid(tool + Cylinder(head_radius, feature["head_depth"], align=DOWN))
    half_angle = math.radians(feature.get("head_angle", 90)) / 2
    height = (head_radius - radius) / math.tan(half_angle)
    return _solid(tool + Cone(radius, head_radius, height, align=DOWN))


@register("hole", SCHEMA)
def apply(feature: HoleFeature, ctx: Context) -> Compound:
    """Drill the holes into the base."""
    base = ctx.results[feature["base"]]
    if feature.get("through"):
        depth = 2 * base.bounding_box().diagonal
    elif "depth" in feature:
        depth = feature["depth"]
    else:
        raise ValueError("a hole needs depth, or through: true")
    shape = _tool(feature, depth)
    tools = [
        Plane(origin=centre, z_dir=-direction) * shape
        for centre, direction in _centres(feature, ctx)
    ]
    result = cut(base, *tools)
    return Part(result.solids())
