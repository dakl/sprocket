from build123d import Compound, Face
from build123d import loft as b3d_loft

from sprocket_engine.context import Context
from sprocket_engine.model import JSONObject, LoftFeature
from sprocket_engine.ops.extrude import combine
from sprocket_engine.ops.registry import register
from sprocket_engine.regions import ProfileError

SCHEMA: JSONObject = {
    "type": "object",
    "description": (
        "Joins two or more sketch profiles, in order, into one solid. Each section is a sketch of one closed region. "
        "Without a base it makes a new body; with one, `mode` joins (default), cuts or intersects."
    ),
    "required": ["id", "type", "sections"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "loft"},
        "sections": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "description": "ids of the sketch features to pass through, in order",
        },
        "ruled": {
            "type": "boolean",
            "description": "straight sides between sections instead of a smooth skin. Default false.",
        },
        "base": {
            "type": "string",
            "description": "id of a solid feature to combine with, if any",
        },
        "mode": {
            "enum": ["join", "cut", "intersect"],
            "description": "How the loft combines with `base`. Default join.",
        },
    },
}


@register("loft", SCHEMA)
def apply(feature: LoftFeature, ctx: Context) -> Compound:
    """Skin the sections, in order, and combine the solid with the base."""
    faces: list[Face] = []
    for sketch_id in feature["sections"]:
        if ctx.sketch(sketch_id) is None:
            raise ProfileError(
                f"loft section {sketch_id!r} is not a sketch earlier in the document"
            )
        found = ctx.results[sketch_id].faces()
        if len(found) != 1:
            raise ProfileError(
                f"loft section {sketch_id!r} has {len(found)} closed regions; a section needs exactly one"
            )
        faces.append(found[0])
    tool = b3d_loft(faces, ruled=feature.get("ruled", False))
    return combine(feature, tool, ctx)
