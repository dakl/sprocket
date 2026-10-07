from build123d import revolve as b3d_revolve

from .construct import AXIS_SCHEMA, resolve_axis
from .registry import register

SCHEMA = {
    "type": "object",
    "description": "Sweeps a sketch around an axis. Always makes a new body, never a cut.",
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


@register("revolve", SCHEMA)
def apply(feature: dict, ctx):
    profile = ctx.results[feature["profile"]]
    axis = resolve_axis(feature["axis"], ctx)
    return b3d_revolve(profile, axis=axis, revolution_arc=feature.get("angle", 360))
