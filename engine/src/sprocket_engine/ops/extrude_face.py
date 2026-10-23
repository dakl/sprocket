from build123d import Compound, Face, Part
from build123d import extrude as b3d_extrude

from sprocket_engine.b3d import cut, fuse
from sprocket_engine.context import Context
from sprocket_engine.model import ExtrudeFaceFeature, JSONObject
from sprocket_engine.ops.registry import register
from sprocket_engine.selectors import (
    SELECTOR_DESCRIPTION,
    SelectorError,
    resolve_selector,
)

SCHEMA: JSONObject = {
    "type": "object",
    "description": "Push/pull one face of an existing solid. Positive adds material, negative cuts into it.",
    "required": ["id", "type", "base", "face", "distance"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude_face"},
        "base": {
            "type": "string",
            "description": "id of the feature this pushes off of",
        },
        "face": {"type": "object", "description": SELECTOR_DESCRIPTION},
        "distance": {
            "type": "number",
            "not": {"const": 0},
            "description": "mm, along the face normal; negative pushes inward and cuts",
        },
    },
}


@register("extrude_face", SCHEMA)
def apply(feature: ExtrudeFaceFeature, ctx: Context) -> Compound:
    """Push or pull one face of the base."""
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["face"], ctx)
    if len(faces) != 1:
        raise SelectorError(
            f"face selector matched {len(faces)} faces, expected exactly 1"
        )
    face = faces[0]
    if not isinstance(face, Face):
        msg = "the face selector must match a face"
        raise SelectorError(msg)

    distance = feature["distance"]
    tool = b3d_extrude(face, amount=distance)
    if base.volume == 0:
        # base is a bare sketch, not a solid yet (e.g. push/pull straight off
        # the first sketch feature) - there's nothing to fuse or cut against,
        # so the extrude just becomes the new body, same as a plain extrude.
        return tool
    if distance < 0:
        return Part([cut(base, tool)])
    return Part([fuse(base, tool)])
