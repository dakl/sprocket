from build123d import Compound
from build123d import chamfer as b3d_chamfer

from sprocket_engine.context import Context
from sprocket_engine.model import ChamferFeature, JSONObject
from sprocket_engine.ops.registry import register
from sprocket_engine.selectors import SELECTOR_DESCRIPTION, resolve_edges

SCHEMA: JSONObject = {
    "type": "object",
    "description": "Cuts the selected edges of a solid back to a flat bevel.",
    "required": ["id", "type", "base", "edges", "length"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "chamfer"},
        "base": {"type": "string", "description": "id of the feature being cut back"},
        "edges": {"type": "object", "description": SELECTOR_DESCRIPTION},
        # `length` rather than `distance`, matching build123d and the usual
        # drawing callout. An asymmetric chamfer needs a second length and an
        # angle, which SPEC.md does not ask for yet.
        "length": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, measured along each face",
        },
    },
}


@register("chamfer", SCHEMA)
def apply(feature: ChamferFeature, ctx: Context) -> Compound:
    base = ctx.results[feature["base"]]
    return b3d_chamfer(
        resolve_edges(base, feature["edges"], ctx), length=feature["length"]
    )
