from build123d import Face, Kind, Part, offset

from ..selectors import SELECTOR_DESCRIPTION, SelectorError, resolve_selector
from .registry import register

SCHEMA = {
    "type": "object",
    "description": (
        "Hollows a solid into walls of one thickness. The faces in `open` are removed, which is how a box "
        "becomes a tray. With no `open` the part becomes a closed hollow solid."
    ),
    "required": ["id", "type", "base", "thickness"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "shell"},
        "base": {"type": "string", "description": "id of the feature being hollowed"},
        "thickness": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "mm, wall thickness",
        },
        "open": {
            "type": "object",
            "description": "The faces to remove. " + SELECTOR_DESCRIPTION,
        },
        "outward": {
            "type": "boolean",
            "description": "Grow the walls outside the original surface instead of inside, so the inside keeps its size.",
        },
    },
}


@register("shell", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    openings = []
    if "open" in feature:
        openings = resolve_selector(base, feature["open"], ctx)
        if not openings:
            raise SelectorError("the shell's `open` selector matched no faces")
        if not all(isinstance(item, Face) for item in openings):
            raise SelectorError("the shell's `open` selector must match faces")
    amount = feature["thickness"] if feature.get("outward") else -feature["thickness"]
    # INTERSECTION keeps corners sharp. The default, ARC, rounds the outside
    # corners of an outward shell.
    result = offset(base, amount=amount, openings=openings, kind=Kind.INTERSECTION)
    if not openings:
        # With nothing to open, offset returns the offset solid itself, not a
        # wall. The hollow is the space between it and the original.
        result = result.cut(base) if feature.get("outward") else base.cut(result)
    part = Part(result.solids())
    if not part.solids() or part.volume <= 0:
        raise ValueError(
            f"shell of {feature['base']} by {feature['thickness']} mm left no solid: the walls may be thicker than the part"
        )
    return part
