from build123d import Compound, Part

from sprocket_engine.b3d import cut, fuse
from sprocket_engine.context import Context
from sprocket_engine.model import JSONObject, MirrorFeature
from sprocket_engine.ops.registry import register
from sprocket_engine.ops.sketch import PLANE_SCHEMA, resolve_plane

SCHEMA: JSONObject = {
    "type": "object",
    "description": (
        "Mirrors the solid an earlier feature made about a plane, and combines the original and its mirror "
        "with `base`. To mirror a whole part, use its last feature as both `tool` and `base`. The plane is any "
        "sketch plane: XY, XZ or YZ with an optional offset, a face of an earlier feature, or an explicit plane."
    ),
    "required": ["id", "type", "tool", "plane"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "mirror"},
        "tool": {
            "type": "string",
            "description": "id of the feature whose solid is mirrored",
        },
        "plane": PLANE_SCHEMA,
        "base": {
            "type": "string",
            "description": "id of the feature the original and the mirror combine with. Without it, they are a new body.",
        },
        "mode": {
            "enum": ["join", "cut"],
            "description": "default join. cut removes the original and the mirror from base",
        },
    },
}


@register("mirror", SCHEMA)
def apply(feature: MirrorFeature, ctx: Context) -> Compound:
    """Mirror the tool about the plane, and combine both with the base."""
    tool = ctx.results[feature["tool"]]
    if not tool.solids():
        raise ValueError(f"mirror tool {feature['tool']!r} is not a solid")
    mirrored = tool.mirror(resolve_plane(feature["plane"], ctx))
    base_id = feature.get("base")
    if base_id is None:
        result = fuse(tool, mirrored)
    elif feature.get("mode") == "cut":
        result = cut(ctx.results[base_id], tool, mirrored)
    else:
        result = fuse(ctx.results[base_id], tool, mirrored)
    solids = result.solids()
    if not solids:
        raise ValueError(f"mirror {feature['id']} left an empty solid")
    return Part(solids)
