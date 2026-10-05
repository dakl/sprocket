from build123d import Part, Pos, ShapeList, Vector

from .registry import register
from .revolve import AXIS_SCHEMA, _resolve_axis

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
COUNT = {"type": "integer", "minimum": 1, "description": "copies along this direction, the original included"}

LINEAR = {
    "type": "object",
    "required": ["direction", "count", "spacing"],
    "properties": {
        "direction": {**VECTOR3, "description": "world direction, normalised by the engine"},
        "count": COUNT,
        "spacing": {"type": "number", "description": "mm between neighbouring copies"},
    },
}

SCHEMA = {
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
        "tool": {"type": "string", "description": "id of the feature whose solid is copied"},
        "base": {"type": "string", "description": "id of the feature the copies combine with. Without it, the copies are a new body."},
        "mode": {"enum": ["join", "cut"], "description": "default join"},
        "linear": LINEAR,
        "linear2": {**LINEAR, "description": "a second direction, which makes a grid"},
        "circular": {
            "type": "object",
            "required": ["axis", "count"],
            "properties": {
                "axis": AXIS_SCHEMA,
                "count": COUNT,
                "angle": {"type": "number", "description": "degrees spanned, default 360 (copies spread evenly)"},
            },
        },
    },
}


def _linear_offsets(spec):
    if spec is None:
        return [Vector(0, 0, 0)]
    step = Vector(*spec["direction"]).normalized() * spec["spacing"]
    return [step * index for index in range(spec["count"])]


def pattern_copies(feature: dict, tool) -> list:
    """The tool's solid at every place in the pattern, the original first."""
    if "linear" in feature:
        return [
            tool.moved(Pos(*(first + second)))
            for first in _linear_offsets(feature["linear"])
            for second in _linear_offsets(feature.get("linear2"))
        ]
    circular = feature["circular"]
    axis = _resolve_axis(circular["axis"])
    angle = circular.get("angle", 360)
    count = circular["count"]
    # A full turn would put the last copy on the first, so 360 divides by count
    # and a part turn spreads the copies end to end.
    step = angle / count if angle % 360 == 0 else angle / max(count - 1, 1)
    return [tool.rotate(axis, step * index) for index in range(count)]


@register("pattern", SCHEMA)
def apply(feature: dict, ctx):
    tool = ctx.results[feature["tool"]]
    if not tool.solids():
        raise ValueError(f"pattern tool {feature['tool']!r} is not a solid")
    placed = pattern_copies(feature, tool)
    base_id = feature.get("base")
    if base_id is None:
        result = placed[0].fuse(*placed[1:]) if len(placed) > 1 else placed[0]
        return Part(result.solids())
    base = ctx.results[base_id]
    result = base.cut(*placed) if feature.get("mode") == "cut" else base.fuse(*placed)
    solids = list(result) if isinstance(result, ShapeList) else result.solids()
    if not solids:
        raise ValueError(f"pattern {feature['id']} left an empty solid")
    return Part(solids)
