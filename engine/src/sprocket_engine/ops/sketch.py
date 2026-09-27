from build123d import Plane, Polygon, Pos, Rectangle

from .registry import register

ORIGIN_PLANES = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}

VECTOR3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
POINT2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}

PLANE_SCHEMA = {
    "oneOf": [
        {"enum": ["XY", "XZ", "YZ"]},
        {
            "type": "object",
            "required": ["origin", "normal", "x_dir"],
            "properties": {"origin": VECTOR3, "normal": VECTOR3, "x_dir": VECTOR3},
        },
    ]
}

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "plane"],
    "oneOf": [{"required": ["rect"]}, {"required": ["polygon"]}],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "sketch"},
        # A principal plane by name, or an arbitrary plane (e.g. picked off a
        # part face) given as an origin point plus a normal and an in-plane
        # x-axis - the same three vectors build123d's Plane() takes.
        "plane": PLANE_SCHEMA,
        "rect": {
            "type": "object",
            "required": ["width", "height"],
            "properties": {
                "width": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "default": [0, 0],
                },
            },
        },
        # Closed polygon, as [u, v] points local to `plane`. The sketcher UI
        # emits this for a freeform or rectangle draw instead of `rect`.
        "polygon": {"type": "array", "items": POINT2, "minItems": 3},
    },
}


def _resolve_plane(plane_ref):
    if isinstance(plane_ref, str):
        return ORIGIN_PLANES[plane_ref]
    return Plane(origin=tuple(plane_ref["origin"]), z_dir=tuple(plane_ref["normal"]), x_dir=tuple(plane_ref["x_dir"]))


def _counter_clockwise(points):
    """Returns `points` wound counter-clockwise in the plane's own uv axes.

    A face's normal follows its winding, so a clockwise polygon builds a face
    pointing away from the sketch plane's normal, and `extrude` with a positive
    distance then runs backwards into the part instead of out of it. The
    sketcher UI emits whichever winding the user happened to draw, so normalise
    it here and the sign of `distance` means the same thing for every sketch.
    """
    signed_area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
    return points if signed_area >= 0 else points[::-1]


@register("sketch", SCHEMA)
def apply(feature: dict, ctx):
    plane = _resolve_plane(feature["plane"])
    if "polygon" in feature:
        points = _counter_clockwise([tuple(point) for point in feature["polygon"]])
        return plane * Polygon(*points)
    rect = feature["rect"]
    cx, cy = rect.get("center", [0, 0])
    return plane * Pos(cx, cy) * Rectangle(rect["width"], rect["height"])
