from build123d import Axis
from build123d import revolve as b3d_revolve

from ..selectors import AXES
from .registry import register

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}

# Same shape as sketch.py's PLANE_SCHEMA: a name for the three axes anyone
# actually revolves about, or an explicit line for the rest.
AXIS_SCHEMA = {
    "oneOf": [
        {"enum": ["X", "Y", "Z"]},
        {
            "type": "object",
            "required": ["origin", "direction"],
            "properties": {"origin": VECTOR3, "direction": VECTOR3},
        },
    ]
}

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "profile", "axis"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "revolve"},
        "profile": {"type": "string", "description": "id of the sketch feature to sweep"},
        "axis": AXIS_SCHEMA,
        "angle": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 360,
            "description": "degrees, default 360",
        },
    },
}

# This op only makes a body. `extrude` can cut by taking a `base` and a negative
# distance, but a revolve angle is always positive, so a cut cannot ride on the
# sign here. Cutting with a revolved body is what the `boolean` op is for.


def _resolve_axis(axis_ref):
    if isinstance(axis_ref, str):
        return AXES[axis_ref]
    return Axis(tuple(axis_ref["origin"]), tuple(axis_ref["direction"]))


@register("revolve", SCHEMA)
def apply(feature: dict, ctx):
    profile = ctx.results[feature["profile"]]
    axis = _resolve_axis(feature["axis"])
    return b3d_revolve(profile, axis=axis, revolution_arc=feature.get("angle", 360))
