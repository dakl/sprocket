import copy

from build123d import Keep, Part

from .registry import register
from .sketch import PLANE_SCHEMA, resolve_plane

KEEP = {"above": Keep.TOP, "below": Keep.BOTTOM, "both": Keep.BOTH}

SCHEMA = {
    "type": "object",
    "description": (
        "Cuts a body in two along a plane. `keep` chooses the side above the plane (the way its normal points), "
        "below it, or both. Both sides stay in the same body as separate solids, and 3MF writes them as separate objects."
    ),
    "required": ["id", "type", "base", "plane"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "split"},
        "base": {
            "type": "string",
            "description": "id of the feature whose solid is split",
        },
        "plane": PLANE_SCHEMA,
        "keep": {"enum": list(KEEP), "description": "default both"},
    },
}


@register("split", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    result = base.split(
        resolve_plane(feature["plane"], ctx), keep=KEEP[feature.get("keep", "both")]
    )
    # Keep.BOTH gives (above, below), and a side with nothing on it is None.
    sides = result if isinstance(result, tuple) else (result,)
    solids = [solid for side in sides if side is not None for solid in side.solids()]
    if not solids:
        raise ValueError(
            f"split {feature['id']} kept nothing: the plane misses the side you asked for"
        )
    if len(solids) == len(base.solids()) and abs(
        sum(solid.volume for solid in solids) - base.volume
    ) < 1e-9 * max(base.volume, 1):
        raise ValueError(
            f"split {feature['id']}: the plane does not cross {feature['base']!r}"
        )
    # The two sides share the cut face. A deep copy gives each solid its own.
    return Part([copy.deepcopy(solid) for solid in solids])
