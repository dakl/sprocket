from build123d import Plane, Pos, Rectangle

from .registry import register

ORIGIN_PLANES = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "plane", "rect"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "sketch"},
        "plane": {"enum": ["XY", "XZ", "YZ"]},
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
    },
}


@register("sketch", SCHEMA)
def apply(feature: dict, ctx):
    plane = ORIGIN_PLANES[feature["plane"]]
    rect = feature["rect"]
    cx, cy = rect.get("center", [0, 0])
    return plane * Pos(cx, cy) * Rectangle(rect["width"], rect["height"])
