import copy

from build123d import Part

from .construct import AXIS_SCHEMA, resolve_axis
from .pattern import VECTOR3
from .registry import register

SCHEMA = {
    "type": "object",
    "description": (
        "Moves a body: turns it `rotate.angle` degrees about `rotate.axis`, then "
        "shifts it by `translate`. "
        "With `base` the body itself moves and later features see it there. With "
        "`tool` a moved copy becomes "
        "a new body, and the original stays."
    ),
    "required": ["id", "type"],
    "oneOf": [{"required": ["base"]}, {"required": ["tool"]}],
    "anyOf": [{"required": ["translate"]}, {"required": ["rotate"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "move"},
        "base": {"type": "string", "description": "id of the feature whose body moves"},
        "tool": {
            "type": "string",
            "description": "id of the feature whose solid is copied to a new body",
        },
        "translate": {**VECTOR3, "description": "[x, y, z] mm"},
        "rotate": {
            "type": "object",
            "required": ["axis", "angle"],
            "properties": {
                "axis": AXIS_SCHEMA,
                "angle": {
                    "type": "number",
                    "description": "degrees, right-handed about the axis",
                },
            },
        },
    },
}


@register("move", SCHEMA)
def apply(feature: dict, ctx):
    source = feature.get("base") or feature.get("tool")
    shape = ctx.results.get(source)
    if shape is None or not shape.solids():
        raise ValueError(f"move {feature['id']}: {source!r} is not a solid built before it")
    moved = copy.deepcopy(shape)
    if "rotate" in feature:
        moved = moved.rotate(
            resolve_axis(feature["rotate"]["axis"], ctx), feature["rotate"]["angle"]
        )
    if "translate" in feature:
        moved = moved.translate(tuple(feature["translate"]))
    return Part(moved.solids())
