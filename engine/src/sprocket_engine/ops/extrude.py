from build123d import Compound, Part, Sketch
from build123d import extrude as b3d_extrude

from ..regions import ProfileError, find_regions, pick_region
from .registry import register
from .sketch import resolve_plane

SCHEMA = {
    "type": "object",
    "description": "Pushes a sketch into a solid. With no base it makes a new body. With a base, a positive distance fuses and a negative one cuts.",
    "required": ["id", "type", "distance"],
    "oneOf": [{"required": ["profile"]}, {"required": ["profiles"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "extrude"},
        "profile": {"type": "string", "description": "id of a sketch feature. Extrudes everything drawn in it."},
        "profiles": {
            "type": "array",
            "minItems": 1,
            "description": "The regions to extrude, picked from one or more sketches.",
            "items": {
                "type": "object",
                "required": ["sketch", "bounded_by"],
                "properties": {
                    "sketch": {"type": "string", "description": "id of a sketch feature"},
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
        "base": {"type": "string", "description": "id of a solid feature to fuse/cut into, if any"},
        "distance": {"type": "number", "not": {"const": 0}, "description": "mm, sign controls direction"},
    },
}


def _profile_faces(feature: dict, ctx):
    """The shape to extrude: the whole sketch, or the regions picked from it."""
    if "profiles" not in feature:
        whole = ctx.results[feature["profile"]]
        if not whole.faces():
            raise ProfileError(f"sketch {feature['profile']!r} has no closed region to extrude")
        return whole
    picked = []
    for reference in feature["profiles"]:
        sketch = ctx.features.get(reference["sketch"])
        if sketch is None or sketch["type"] != "sketch":
            raise ProfileError(f"profile refers to {reference['sketch']!r}, which is not a sketch earlier in the document")
        if "entities" not in sketch:
            # A sketch of one shape has no regions to choose between.
            picked.extend(ctx.results[reference["sketch"]].faces())
            continue
        region = pick_region(find_regions(sketch["entities"]), reference)
        picked.append(resolve_plane(sketch["plane"]) * region.face)
    return Sketch(Compound(picked))


@register("extrude", SCHEMA)
def apply(feature: dict, ctx):
    tool = b3d_extrude(_profile_faces(feature, ctx), amount=feature["distance"])

    base_id = feature.get("base")
    if base_id is None:
        return tool

    base = ctx.results[base_id]
    if feature["distance"] < 0:
        return Part([base.cut(tool)])
    return Part([base.fuse(tool)])
