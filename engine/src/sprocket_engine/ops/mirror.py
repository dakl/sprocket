from build123d import Part, ShapeList

from .registry import register
from .sketch import PLANE_SCHEMA, resolve_plane

SCHEMA = {
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
        "tool": {"type": "string", "description": "id of the feature whose solid is mirrored"},
        "plane": PLANE_SCHEMA,
        "base": {"type": "string", "description": "id of the feature the original and the mirror combine with. Without it, they are a new body."},
        "mode": {"enum": ["join", "cut"], "description": "default join. cut removes the original and the mirror from base"},
    },
}


@register("mirror", SCHEMA)
def apply(feature: dict, ctx):
    tool = ctx.results[feature["tool"]]
    if not tool.solids():
        raise ValueError(f"mirror tool {feature['tool']!r} is not a solid")
    mirrored = tool.mirror(resolve_plane(feature["plane"], ctx))
    base_id = feature.get("base")
    if base_id is None:
        result = tool.fuse(mirrored)
    elif feature.get("mode") == "cut":
        result = ctx.results[base_id].cut(tool, mirrored)
    else:
        result = ctx.results[base_id].fuse(tool, mirrored)
    solids = list(result) if isinstance(result, ShapeList) else result.solids()
    if not solids:
        raise ValueError(f"mirror {feature['id']} left an empty solid")
    return Part(solids)
