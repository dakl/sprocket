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
        "distance": {
            "type": "number",
            "not": {"const": 0},
            "description": "mm, along the face normal; negative pushes inward and cuts",
        },
    },
}


@register("extrude_face", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["face"])
    if len(faces) != 1:
        raise SelectorError(f"face selector matched {len(faces)} faces, expected exactly 1")

    distance = feature["distance"]
    tool = b3d_extrude(faces[0], amount=distance)
    if base.volume == 0:
        # base is a bare sketch, not a solid yet (e.g. push/pull straight off
        # the first sketch feature) - there's nothing to fuse or cut against,
        # so the extrude just becomes the new body, same as a plain extrude.
        return tool
    if distance < 0:
        return Part([base.cut(tool)])
    return Part([base.fuse(tool)])
