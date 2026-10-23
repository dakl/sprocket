from build123d import Compound, Part, Pos, Vector

from sprocket_engine.b3d import cut, fuse
from sprocket_engine.context import Context
from sprocket_engine.model import (
    AxisRef,
    JSONObject,
    PatternCircular,
    PatternFeature,
    PatternLinear,
)
from sprocket_engine.ops.construct import AXIS_SCHEMA, resolve_axis
from sprocket_engine.ops.registry import register

VECTOR3: JSONObject = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}
COUNT: JSONObject = {
    "type": "integer",
    "minimum": 1,
    "description": "copies along this direction, the original included",
}

LINEAR: JSONObject = {
    "type": "object",
    "required": ["direction", "count", "spacing"],
    "properties": {
        "direction": {
            "oneOf": [VECTOR3, AXIS_SCHEMA],
            "description": "a world direction [x, y, z], normalised by the engine, or an axis (X, Y, Z, an axis feature) whose direction it takes",
        },
        "count": COUNT,
        "spacing": {"type": "number", "description": "mm between neighbouring copies"},
    },
}

SCHEMA: JSONObject = {
    "type": "object",
    "description": (
        "Copies the solid an earlier feature made, in a row, a grid or around an axis, and combines the copies "
        "with `base`. The tool feature is usually a standalone extrude (no base) of one pin, boss or hole. "
        "`linear` gives a row, `linear` plus `linear2` a grid, `circular` a ring. Counts include the original."
    ),
    "required": ["id", "type", "tool"],
    "oneOf": [{"required": ["linear"]}, {"required": ["circular"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "pattern"},
        "tool": {
            "type": "string",
            "description": "id of the feature whose solid is copied",
        },
        "base": {
            "type": "string",
            "description": "id of the feature the copies combine with. Without it, the copies are a new body.",
        },
        "mode": {"enum": ["join", "cut"], "description": "default join"},
        "linear": LINEAR,
        "linear2": {**LINEAR, "description": "a second direction, which makes a grid"},
        "circular": {
            "type": "object",
            "required": ["axis", "count"],
            "properties": {
                "axis": AXIS_SCHEMA,
                "count": COUNT,
                "angle": {
                    "type": "number",
                    "description": "degrees spanned, default 360 (copies spread evenly)",
                },
            },
        },
    },
}


def _direction(direction: list[float] | AxisRef, ctx: Context | None) -> Vector:
    if isinstance(direction, list):
        return Vector(direction[0], direction[1], direction[2]).normalized()
    return resolve_axis(direction, ctx).direction.normalized()


def _linear_offsets(spec: PatternLinear | None, ctx: Context | None) -> list[Vector]:
    if spec is None:
        return [Vector(0, 0, 0)]
    step = _direction(spec["direction"], ctx) * spec["spacing"]
    return [step * index for index in range(spec["count"])]


def pattern_copies(
    feature: PatternFeature, tool: Compound, ctx: Context | None = None
) -> list[Compound]:
    """The tool's solid at every place in the pattern, the original first."""
    if "linear" in feature:
        return [
            tool.moved(Pos(first + second))
            for first in _linear_offsets(feature["linear"], ctx)
            for second in _linear_offsets(feature.get("linear2"), ctx)
        ]
    circular: PatternCircular | None = feature.get("circular")
    if circular is None:
        msg = "a pattern needs linear or circular"
        raise ValueError(msg)
    axis = resolve_axis(circular["axis"], ctx)
    angle = circular.get("angle", 360)
    count = circular["count"]
    # A full turn would put the last copy on the first, so 360 divides by count
    # and a part turn spreads the copies end to end.
    step = angle / count if angle % 360 == 0 else angle / max(count - 1, 1)
    return [tool.rotate(axis, step * index) for index in range(count)]


@register("pattern", SCHEMA)
def apply(feature: PatternFeature, ctx: Context) -> Compound:
    """Copy the tool and combine the copies with the base."""
    tool = ctx.results[feature["tool"]]
    if not tool.solids():
        raise ValueError(f"pattern tool {feature['tool']!r} is not a solid")
    placed = pattern_copies(feature, tool, ctx)
    base_id = feature.get("base")
    if base_id is None:
        result = fuse(placed[0], *placed[1:]) if len(placed) > 1 else placed[0]
        return Part(result.solids())
    base = ctx.results[base_id]
    result = cut(base, *placed) if feature.get("mode") == "cut" else fuse(base, *placed)
    solids = result.solids()
    if not solids:
        raise ValueError(f"pattern {feature['id']} left an empty solid")
    return Part(solids)
