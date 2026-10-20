from build123d import Compound, Part, ShapeList, Sketch
from build123d import extrude as b3d_extrude

from ..regions import ProfileError, find_regions, pick_region
from .registry import register
from .sketch import resolve_plane, sketch_entities

SCHEMA = {
    "type": "object",
    "description": (
        "Pushes a sketch into a solid. With no base it makes a new body. With a base and no mode, a positive "
        "distance fuses and a negative one cuts. Give `mode` to choose the combine yourself: then the sign of "
        "`distance` only picks the direction, so `mode: join` with a negative distance grows the part the other "
        "way from the sketch plane."
    ),
    "required": ["id", "type", "distance"],
    "oneOf": [{"required": ["profile"]}, {"required": ["profiles"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude"},
        "profile": {
            "type": "string",
            "description": "id of a sketch feature. Extrudes everything drawn in it.",
        },
        "profiles": {
            "type": "array",
            "minItems": 1,
            "description": "The regions to extrude, picked from one or more sketches.",
            "items": {
                "type": "object",
                "required": ["sketch", "bounded_by"],
                "properties": {
                    "sketch": {
                        "type": "string",
                        "description": "id of a sketch feature",
                    },
                    "bounded_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ids of the entities that enclose the region. Empty for a sketch that is one shape.",
                    },
                    "inside": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": "A point [u, v] inside the region. Only needed when two regions share the same bounded_by.",
                    },
                },
            },
        },
        "base": {
            "type": "string",
            "description": "id of a solid feature to fuse/cut into, if any",
        },
        "distance": {
            "type": "number",
            "not": {"const": 0},
            "description": "mm, sign controls direction",
        },
        "mode": {
            "enum": ["join", "cut", "intersect", "new"],
            "description": "How the extrude combines with `base`. Needs a base, except `new`, which takes none.",
        },
        "symmetric": {
            "type": "boolean",
            "description": "Extrude `distance` to both sides of the sketch plane, so the solid is twice as thick.",
        },
    },
}


def profile_faces(feature: dict, ctx):
    """The shape to extrude: the whole sketch, or the regions picked from it."""
    if "profiles" not in feature:
        whole = ctx.results[feature["profile"]]
        if not whole.faces():
            raise ProfileError(
                f"sketch {feature['profile']!r} has no closed region to extrude"
            )
        return whole
    picked = []
    # Region detection splits the whole sketch, so it runs once per sketch, not
    # once per picked region: 40 regions of one 160-line sketch took 23 s.
    regions_of = {}
    for reference in feature["profiles"]:
        sketch = ctx.features.get(reference["sketch"])
        if sketch is None or sketch["type"] != "sketch":
            raise ProfileError(
                f"profile refers to {reference['sketch']!r}, which is not a sketch earlier in the document"
            )
        if "entities" not in sketch:
            # A sketch of one shape has no regions to choose between.
            picked.extend(ctx.results[reference["sketch"]].faces())
            continue
        if reference["sketch"] not in regions_of:
            regions_of[reference["sketch"]] = find_regions(sketch_entities(sketch))
        region = pick_region(regions_of[reference["sketch"]], reference)
        picked.append(resolve_plane(sketch["plane"], ctx) * region.face)
    return Sketch(Compound(picked))


COMBINE = {
    "join": lambda base, tool: base.fuse(tool),
    "cut": lambda base, tool: base.cut(tool),
    "intersect": lambda base, tool: base.intersect(tool),
}


def combine(feature: dict, tool, ctx, mode: str | None = None):
    """`tool` combined with the feature's `base` by `mode` (default join), or
    `tool` alone as a new body when there is no base.
    """
    base_id = feature.get("base")
    mode = mode or feature.get("mode")
    if mode == "new" and base_id is not None:
        raise ValueError("mode 'new' makes a new body, so it takes no base")
    if base_id is None:
        if mode not in (None, "new"):
            raise ValueError(f"mode {mode!r} needs a base to combine with")
        return tool
    mode = mode or "join"
    result = COMBINE[mode](ctx.results[base_id], tool)
    solids = list(result) if isinstance(result, ShapeList) else [result]
    part = Part(solids)
    if not solids or part.volume == 0:
        raise ValueError(
            f"{mode} of {feature['id']} with {base_id} left an empty solid"
        )
    return part


@register("extrude", SCHEMA)
def apply(feature: dict, ctx):
    tool = b3d_extrude(
        profile_faces(feature, ctx),
        amount=feature["distance"],
        both=feature.get("symmetric", False),
    )
    mode = feature.get("mode")
    # Documents from before `mode` existed carry the combine in the sign.
    if mode is None and feature.get("base") is not None:
        mode = "cut" if feature["distance"] < 0 else "join"
    return combine(feature, tool, ctx, mode)
