from build123d import Part
from build123d import extrude as b3d_extrude

from .registry import register

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "profile", "distance"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude"},
        "profile": {"type": "string", "description": "id of a sketch feature"},
        "base": {"type": "string", "description": "id of a solid feature to fuse/cut into, if any"},
        "distance": {"type": "number", "not": {"const": 0}, "description": "mm, sign controls direction"},
    },
}


@register("extrude", SCHEMA)
def apply(feature: dict, ctx):
    profile = ctx.results[feature["profile"]]
    tool = b3d_extrude(profile, amount=feature["distance"])

    base_id = feature.get("base")
    if base_id is None:
        return tool

    base = ctx.results[base_id]
    if feature["distance"] < 0:
        return Part([base.cut(tool)])
    return Part([base.fuse(tool)])
