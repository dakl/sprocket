from build123d import fillet as b3d_fillet

from ..selectors import SELECTOR_DESCRIPTION, resolve_edges
from .registry import register

SCHEMA = {
    "type": "object",
    "description": "Rounds the selected edges of a solid.",
    "required": ["id", "type", "base", "edges", "radius"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "fillet"},
        "base": {"type": "string", "description": "id of the feature being rounded"},
        "edges": {"type": "object", "description": SELECTOR_DESCRIPTION},
        "radius": {"type": "number", "exclusiveMinimum": 0, "description": "mm"},
    },
}


@register("fillet", SCHEMA)
def apply(feature: dict, ctx):
    """Unlike extrude_face, this takes however many edges the selector matches.
    Rounding every top edge in one feature is the normal case, not an
    exception, so only an empty match is an error.
    """
    base = ctx.results[feature["base"]]
    return b3d_fillet(
        resolve_edges(base, feature["edges"], ctx), radius=feature["radius"]
    )
