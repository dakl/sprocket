from build123d import extrude as b3d_extrude

from .registry import register

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "profile", "distance"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude"},
        "profile": {"type": "string", "description": "id of a sketch feature"},
        "distance": {"type": "number", "exclusiveMinimum": 0, "description": "mm"},
    },
}


@register("extrude", SCHEMA)
def apply(feature: dict, ctx):
    profile = ctx.results[feature["profile"]]
    return b3d_extrude(profile, amount=feature["distance"])
