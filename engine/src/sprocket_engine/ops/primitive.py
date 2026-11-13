from build123d import Align, Box, Cone, Cylinder, Pos, Sphere, Torus

from .extrude import combine
from .registry import register
from .sketch import PLANE_SCHEMA, POINT2, resolve_plane

LENGTH = {"type": "number", "exclusiveMinimum": 0, "description": "mm"}
ON_PLANE = (Align.CENTER, Align.CENTER, Align.MIN)

# The sizes each shape needs, and the ones it may take.
SIZES = {
    "box": (("width", "depth", "height"), ()),
    "cylinder": (("radius", "height"), ()),
    "cone": (("radius", "height"), ("top_radius",)),
    "sphere": (("radius",), ()),
    "torus": (("major_radius", "minor_radius"), ()),
}

SCHEMA = {
    "type": "object",
    "description": (
        "A solid of a standard shape on a sketch plane, centred at `at` ([u, v] on the plane, default [0, 0]). "
        "A box, cylinder or cone stands on the plane and rises along its normal: box width runs along the plane's u, "
        "depth along v. A sphere or torus is centred on the plane, the torus ring lying in it. Without a base it makes "
        "a new body; with one, `mode` joins (default), cuts or intersects."
    ),
    "required": ["id", "type", "shape"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "primitive"},
        "shape": {"enum": list(SIZES)},
        "plane": {**PLANE_SCHEMA, "description": "any sketch plane form, default XY"},
        "at": {**POINT2, "description": "[u, v] mm on the plane, default [0, 0]"},
        "width": LENGTH,
        "depth": LENGTH,
        "height": LENGTH,
        "radius": LENGTH,
        "top_radius": {
            "type": "number",
            "minimum": 0,
            "description": "mm, a cone's top. Default 0, a point",
        },
        "major_radius": {
            **LENGTH,
            "description": "mm, a torus's ring, centre to tube centre",
        },
        "minor_radius": {**LENGTH, "description": "mm, a torus's tube"},
        "base": {
            "type": "string",
            "description": "id of a solid feature to combine with, if any",
        },
        "mode": {
            "enum": ["join", "cut", "intersect"],
            "description": "How it combines with `base`. Default join.",
        },
    },
}


def solid_of(feature: dict):
    shape = feature["shape"]
    required, _ = SIZES[shape]
    missing = [name for name in required if name not in feature]
    if missing:
        raise ValueError(f"a {shape} needs {', '.join(missing)}")
    if shape == "box":
        return Box(
            feature["width"], feature["depth"], feature["height"], align=ON_PLANE
        )
    if shape == "cylinder":
        return Cylinder(feature["radius"], feature["height"], align=ON_PLANE)
    if shape == "cone":
        return Cone(
            feature["radius"],
            feature.get("top_radius", 0),
            feature["height"],
            align=ON_PLANE,
        )
    if shape == "sphere":
        return Sphere(feature["radius"])
    if feature["minor_radius"] >= feature["major_radius"]:
        raise ValueError("a torus's minor_radius must be smaller than its major_radius")
    return Torus(feature["major_radius"], feature["minor_radius"])


@register("primitive", SCHEMA)
def apply(feature: dict, ctx):
    plane = resolve_plane(feature.get("plane", "XY"), ctx)
    u, v = feature.get("at", [0, 0])
    tool = plane.location * Pos(u, v, 0) * solid_of(feature)
    return combine(feature, tool, ctx)
