import copy
from build123d import Compound, Keep, Part

from sprocket_engine.context import Context
from sprocket_engine.model import JSONObject, SplitFeature
from sprocket_engine.ops.registry import register
from sprocket_engine.ops.sketch import PLANE_SCHEMA, resolve_plane

KEEP = ("above", "below", "both")

SCHEMA: JSONObject = {
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
        "keep": {"enum": [*KEEP], "description": "default both"},
    },
}


def _pieces(side: Compound | list[Compound] | None) -> list[Compound]:
    if side is None:
        return []
    return side if isinstance(side, list) else [side]


@register("split", SCHEMA)
def apply(feature: SplitFeature, ctx: Context) -> Compound:
    """Cut the base along the plane, keeping one side or both."""
    base = ctx.results[feature["base"]]
    plane = resolve_plane(feature["plane"], ctx)
    keep = feature.get("keep", "both")
    # Keep.BOTH gives (above, below). A side with nothing on it is None, and
    # a side in several pieces can be a list.
    if keep == "both":
        sides = list(base.split(plane, keep=Keep.BOTH))
    elif keep == "above":
        sides = [base.split(plane, keep=Keep.TOP)]
    else:
        sides = [base.split(plane, keep=Keep.BOTTOM)]
    solids = [
        solid for side in sides for piece in _pieces(side) for solid in piece.solids()
    ]
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
