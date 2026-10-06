from build123d import DraftAngleError, Face, Part
from build123d import draft as b3d_draft

from ..selectors import SELECTOR_DESCRIPTION, SelectorError, resolve_selector
from .registry import register
from .sketch import PLANE_SCHEMA, resolve_plane

SCHEMA = {
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
        "angle": {"type": "number", "exclusiveMinimum": -45, "exclusiveMaximum": 45, "not": {"const": 0}, "description": "degrees"},
        "neutral": PLANE_SCHEMA,
    },
}


@register("draft", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["faces"], ctx)
    if not faces or not all(isinstance(face, Face) for face in faces):
        raise SelectorError(f"draft {feature['id']} needs a selector that matches faces")
    try:
        return Part(b3d_draft(faces, resolve_plane(feature["neutral"], ctx), feature["angle"]))
    except DraftAngleError as exc:
        where = ", ".join(f"{round(value, 2) + 0:g}" for value in exc.face.center()) if exc.face is not None else "unknown"
        raise ValueError(
            f"draft {feature['id']} failed on the face centred at ({where}). A drafted face runs along the pull "
            "direction, like a side wall; a face across it, like a top, cannot be drafted."
        ) from None
