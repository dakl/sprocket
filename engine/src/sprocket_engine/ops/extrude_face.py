from build123d import Part
from build123d import extrude as b3d_extrude

from ..selectors import SelectorError, resolve_selector
from .registry import register

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "base", "face", "distance"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude_face"},
        "base": {"type": "string", "description": "id of the feature this pushes off of"},
        "face": {"type": "object", "description": "selector chain, see selectors.py"},
        "distance": {"type": "number", "exclusiveMinimum": 0, "description": "mm, along the face normal"},
    },
}


@register("extrude_face", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["face"])
    if len(faces) != 1:
        raise SelectorError(f"face selector matched {len(faces)} faces, expected exactly 1")

    addition = b3d_extrude(faces[0], amount=feature["distance"])
    return Part([base.fuse(addition)])
