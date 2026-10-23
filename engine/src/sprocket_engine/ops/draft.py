from build123d import Compound, DraftAngleError, Face, Part
from build123d import draft as b3d_draft

from sprocket_engine.b3d import draft_failure_face
from sprocket_engine.context import Context
from sprocket_engine.model import DraftFeature, JSONObject
from sprocket_engine.ops.registry import register
from sprocket_engine.ops.sketch import PLANE_SCHEMA, resolve_plane
from sprocket_engine.selectors import (
    SELECTOR_DESCRIPTION,
    SelectorError,
    resolve_selector,
)

SCHEMA: JSONObject = {
    "type": "object",
    "description": (
        "Tilts faces of a solid by an angle so it comes out of a mould. The faces turn about where they cross the "
        "`neutral` plane, and the pull direction is that plane's normal: a positive angle leans them inward as they "
        "go along the normal, so the part narrows away from the plane. Usually the faces are the side walls and the "
        "neutral plane is the base or the parting line."
    ),
    "required": ["id", "type", "base", "faces", "angle", "neutral"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "draft"},
        "base": {"type": "string", "description": "id of the feature being drafted"},
        "faces": {"type": "object", "description": SELECTOR_DESCRIPTION},
        "angle": {
            "type": "number",
            "exclusiveMinimum": -45,
            "exclusiveMaximum": 45,
            "not": {"const": 0},
            "description": "degrees",
        },
        "neutral": PLANE_SCHEMA,
    },
}


@register("draft", SCHEMA)
def apply(feature: DraftFeature, ctx: Context) -> Compound:
    """Tilt the selected faces about the neutral plane."""
    base = ctx.results[feature["base"]]
    found = resolve_selector(base, feature["faces"], ctx)
    faces = [face for face in found if isinstance(face, Face)]
    if not faces or len(faces) != len(found):
        raise SelectorError(
            f"draft {feature['id']} needs a selector that matches faces"
        )
    try:
        return Part(
            b3d_draft(faces, resolve_plane(feature["neutral"], ctx), feature["angle"])
        )
    except DraftAngleError as exc:
        face = draft_failure_face(exc)
        where = (
            ", ".join(f"{round(value, 2) + 0:g}" for value in face.center())
            if face is not None
            else "unknown"
        )
        raise ValueError(
            f"draft {feature['id']} failed on the face centred at ({where}). A drafted face runs along the pull "
            "direction, like a side wall; a face across it, like a top, cannot be drafted."
        ) from None
