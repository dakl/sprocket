from build123d import Align, Cylinder, Face, GeomType, Part, Plane, Vector
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools

from ..selectors import SELECTOR_DESCRIPTION, SelectorError, resolve_selector
from .registry import register

# ISO 261 coarse pitches, mm.
ISO_COARSE = {
    "M1.6": 0.35, "M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M4": 0.7, "M5": 0.8, "M6": 1.0,
    "M8": 1.25, "M10": 1.5, "M12": 1.75, "M14": 2.0, "M16": 2.0, "M20": 2.5, "M24": 3.0,
}

SCHEMA = {
    "type": "object",
    "description": (
        "Threads a cylindrical face: a hole gets an internal thread, a shaft an external one, over the face's whole "
        "length. Cosmetic by default, which leaves the solid as it is and records the thread, so the viewer draws it "
        "and the part stays light. `modeled: true` cuts the real ISO thread form, which needs the `threads` extra. "
        "The face must fit the size: a shaft about the nominal diameter, a hole between the tap drill and the nominal."
    ),
    "required": ["id", "type", "base", "face", "designation"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "thread"},
        "base": {"type": "string", "description": "id of the feature whose face is threaded"},
        "face": {"type": "object", "description": SELECTOR_DESCRIPTION},
        "designation": {"enum": list(ISO_COARSE), "description": "ISO metric size"},
        "pitch": {"type": "number", "exclusiveMinimum": 0, "description": "mm. Default the ISO coarse pitch, so give it only for a fine thread."},
        "modeled": {"type": "boolean", "description": "cut the real thread form. Default false (cosmetic)."},
    },
}


def thread_geometry(feature: dict, ctx) -> dict:
    """Where the thread is: the face's axis, ends and radius, plus its size.
    Read from the geometry each time, so a cached replay that skips `apply`
    still gives the viewer the same answer."""
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["face"], ctx)
    if len(faces) != 1 or not isinstance(faces[0], Face):
        raise SelectorError(f"thread {feature['id']} needs a selector that matches one face, and it matched {len(faces)}")
    face = faces[0]
    if face.geom_type != GeomType.CYLINDER:
        raise ValueError(f"thread {feature['id']} needs a cylindrical face, and the selector found a {face.geom_type.name.lower()} face")
    cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
    axis = cylinder.Axis()
    origin = Vector(axis.Location())
    direction = Vector(axis.Direction()).normalized()
    radius = cylinder.Radius()
    # The cylinder's v parameter runs along its axis, so the face's v range is where it starts and ends.
    _, _, v_min, v_max = BRepTools.UVBounds_s(face.wrapped)
    start = origin + direction * v_min
    # A hole's outward normal points at the axis.
    centre = face.center()
    along = (centre - origin).dot(direction)
    radial = centre - (origin + direction * along)
    internal = face.normal_at(centre).dot(radial) < 0

    major = float(feature["designation"][1:])
    pitch = feature.get("pitch", ISO_COARSE[feature["designation"]])
    diameter = 2 * radius
    low, high = (major - 1.25 * pitch, major + 0.05) if internal else (major - 0.5 * pitch, major + 0.05)
    if not low <= diameter <= high:
        kind = "hole" if internal else "shaft"
        raise ValueError(
            f"thread {feature['id']}: a {feature['designation']} {kind} is {low:g} to {high:g} mm across, and this face is {diameter:g} mm"
        )
    return {
        "start": [round(value, 9) for value in start],
        "direction": [round(value, 9) for value in direction],
        "length": round(v_max - v_min, 9),
        "radius": round(radius, 9),
        "major_diameter": major,
        "pitch": pitch,
        "internal": bool(internal),
        "designation": feature["designation"],
    }


def _modeled(base, geometry: dict):
    try:
        from bd_warehouse.thread import IsoThread
    except ImportError:
        raise ValueError("a modeled thread needs the `threads` extra: uv sync --extra threads") from None
    internal, length = geometry["internal"], geometry["length"]
    # Square ends cut a valid internal thread. On a shaft they left an invalid
    # solid, and fading out at both ends did not, so a shaft gets a runout.
    finishes = ("square", "square") if internal else ("fade", "fade")
    thread = IsoThread(geometry["major_diameter"], geometry["pitch"], length, external=not internal, end_finishes=finishes)
    place = Plane(origin=tuple(geometry["start"]), z_dir=tuple(geometry["direction"]))
    bottom = (Align.CENTER, Align.CENTER, Align.MIN)
    if internal:
        clearance = Cylinder(thread.root_radius, length, align=bottom)
    else:
        clearance = Cylinder(geometry["radius"] + 1, length, align=bottom) - Cylinder(thread.root_radius, length, align=bottom)
    return base.cut(place * clearance).fuse(place * thread)


@register("thread", SCHEMA)
def apply(feature: dict, ctx):
    base = ctx.results[feature["base"]]
    geometry = thread_geometry(feature, ctx)
    if not feature.get("modeled"):
        return base
    result = _modeled(base, geometry)
    if not result.is_valid or len(result.solids()) != len(base.solids()):
        raise ValueError(f"thread {feature['id']}: the modeled thread did not fuse into one valid solid; leave it cosmetic")
    return Part(result.solids())
