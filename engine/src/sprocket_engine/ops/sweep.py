from build123d import Transition
from build123d import sweep as b3d_sweep

from .extrude import SCHEMA as EXTRUDE_SCHEMA
from .extrude import combine, profile_faces
from .registry import register
from .sketch import PATH_SCHEMA, sketch_path

SCHEMA = {
    "type": "object",
    "description": (
        "Moves a sketch profile along a path drawn in another sketch. Draw the profile across the start of the "
        "path. Without a base it makes a new body; with one, `mode` joins (default), cuts or intersects."
    ),
    "required": ["id", "type", "path"],
    "oneOf": [{"required": ["profile"]}, {"required": ["profiles"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "sweep"},
        "profile": EXTRUDE_SCHEMA["properties"]["profile"],
        "profiles": EXTRUDE_SCHEMA["properties"]["profiles"],
        "path": PATH_SCHEMA,
        "base": {
            "type": "string",
            "description": "id of a solid feature to combine with, if any",
        },
        "mode": {
            "enum": ["join", "cut", "intersect"],
            "description": "How the sweep combines with `base`. Default join.",
        },
    },
}


@register("sweep", SCHEMA)
def apply(feature: dict, ctx):
    # build123d's default transition keeps only the path up to the first sharp
    # corner, with no error. RIGHT mitres the corner, as Fusion does.
    tool = b3d_sweep(
        profile_faces(feature, ctx),
        path=sketch_path(feature["path"], ctx),
        transition=Transition.RIGHT,
    )
    if tool.volume <= 0:
        raise ValueError(
            f"sweep {feature['id']} made no solid: is the profile across the start of the path?"
        )
    return combine(feature, tool, ctx)
