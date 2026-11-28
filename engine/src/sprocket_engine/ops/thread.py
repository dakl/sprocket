"""The thread op: an ISO metric thread on a cylindrical face, cosmetic or modeled."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from build123d import Align, Cone, Cylinder, Face, GeomType, Part, Plane, Pos, Shape, Vector
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools

from sprocket_engine.selectors import (
    SELECTOR_DESCRIPTION,
    SelectorError,
    resolve_selector,
)

from .registry import register

if TYPE_CHECKING:
    from sprocket_engine.replay import Context

# ISO 261 coarse pitches, mm: the first and second choice sizes of ISO 262.
ISO_COARSE = {
    "M1.6": 0.35,
    "M1.8": 0.35,
    "M2": 0.4,
    "M2.5": 0.45,
    "M3": 0.5,
    "M3.5": 0.6,
    "M4": 0.7,
    "M5": 0.8,
    "M6": 1.0,
    "M7": 1.0,
    "M8": 1.25,
    "M10": 1.5,
    "M12": 1.75,
    "M14": 2.0,
    "M16": 2.0,
    "M18": 2.5,
    "M20": 2.5,
    "M22": 2.5,
    "M24": 3.0,
    "M27": 3.0,
    "M30": 3.5,
    "M33": 3.5,
    "M36": 4.0,
}

# The ISO internal thread's minor diameter is the nominal less this many pitches.
INTERNAL_MINOR = 1.0825
# How far, in pitches, a modeled thread's core stands proud of the ISO minor
# radius, so the boolean never meets the thread's root faces (see `_bolt`).
CORE_PROUD = 0.05

Hand = Literal["right", "left"]
Ends = Literal["square", "fade", "chamfer"]
BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)

# The fields a thread op and a tapped hole share.
THREAD_PROPERTIES = {
    "designation": {"enum": list(ISO_COARSE), "description": "ISO metric size"},
    "pitch": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": (
            "mm. Default the ISO coarse pitch, so give it only for a fine thread, "
            "such as 1 for M8x1."
        ),
    },
    "length": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "mm of thread. Default the rest of the face after `offset`.",
    },
    "hand": {"enum": ["right", "left"], "description": "default right"},
    "modeled": {
        "type": "boolean",
        "description": "cut the real thread form. Default false (cosmetic).",
    },
    "clearance": {
        "type": "number",
        "minimum": 0,
        "description": (
            "mm on the diameter, modeled only. A hole's thread is cut this much "
            "larger and a shaft's this much smaller, so printed parts fit. Default 0 "
            "(ISO nominal). About 0.2 to 0.4 suits FDM."
        ),
    },
    "ends": {
        "enum": ["square", "fade", "chamfer"],
        "description": (
            "modeled only. square stops the thread at a plane, fade runs it out over "
            "a quarter turn, chamfer adds a 90 degree lead-in. Default square for a "
            "hole, fade for a shaft."
        ),
    },
}

SCHEMA = {
    "type": "object",
    "description": (
        "Threads a cylindrical face: a hole gets an internal thread, a shaft an "
        "external one. The thread runs over the whole face, or `length` mm of it "
        "after `offset`, from the face's start or, with `from_end`, its other end. "
        "Cosmetic by default, which leaves the solid as it is and records the "
        "thread, so the viewer draws it and the part stays light. `modeled: true` "
        "cuts the real ISO thread form, which needs the `threads` extra. "
        "The face must fit the size: a shaft about the nominal diameter, a hole "
        "between the tap drill and the nominal."
    ),
    "required": ["id", "type", "base", "face", "designation"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "thread"},
        "base": {
            "type": "string",
            "description": "id of the feature whose face is threaded",
        },
        "face": {"type": "object", "description": SELECTOR_DESCRIPTION},
        **THREAD_PROPERTIES,
        "offset": {
            "type": "number",
            "minimum": 0,
            "description": "mm of plain face before the thread starts. Default 0.",
        },
        "from_end": {
            "type": "boolean",
            "description": "measure `offset` and `length` from the face's other end",
        },
    },
}


class ThreadJson(TypedDict):
    """Where a thread is, as the viewer reads it from features.json."""

    start: list[float]
    direction: list[float]
    length: float
    radius: float
    major_diameter: float
    pitch: float
    internal: bool
    hand: Hand
    designation: str


@dataclass(frozen=True)
class ThreadSpec:
    """What a thread op or a tapped hole asks for, parsed from its JSON."""

    label: str
    designation: str
    pitch: float
    length: float | None
    offset: float
    from_end: bool
    hand: Hand
    modeled: bool
    clearance: float
    ends: Ends | None

    @property
    def major(self) -> float:
        """The nominal diameter, mm."""
        return float(self.designation[1:])

    @classmethod
    def parse(cls, label: str, spec: dict[str, object]) -> "ThreadSpec":
        """Read the thread fields of a thread op or a hole's `thread`."""
        designation = cast("str", spec["designation"])
        length = spec.get("length")
        return cls(
            label=label,
            designation=designation,
            pitch=float(cast("float", spec.get("pitch", ISO_COARSE[designation]))),
            length=None if length is None else float(cast("float", length)),
            offset=float(cast("float", spec.get("offset", 0))),
            from_end=bool(spec.get("from_end", False)),
            hand=cast("Hand", spec.get("hand", "right")),
            modeled=bool(spec.get("modeled", False)),
            clearance=float(cast("float", spec.get("clearance", 0))),
            ends=cast("Ends | None", spec.get("ends")),
        )


@dataclass(frozen=True)
class Bore:
    """A cylindrical face's axis, its two ends along that axis, and its radius."""

    origin: Vector
    direction: Vector
    v_min: float
    v_max: float
    radius: float
    internal: bool


def bore(face: Face) -> Bore | None:
    """The face as a `Bore`, or None when it is not a cylinder."""
    if face.geom_type != GeomType.CYLINDER:
        return None
    cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
    axis = cylinder.Axis()
    origin = Vector(axis.Location())
    direction = Vector(axis.Direction()).normalized()
    # The cylinder's v parameter runs along its axis, so the face's v range is
    # where it starts and ends.
    _, _, v_min, v_max = BRepTools.UVBounds_s(face.wrapped)
    # A hole's outward normal points at the axis.
    centre = face.center()
    along = (centre - origin).dot(direction)
    radial = centre - (origin + direction * along)
    internal = face.normal_at(centre).dot(radial) < 0
    return Bore(origin, direction, v_min, v_max, cylinder.Radius(), bool(internal))


def face_thread(face: Face, spec: ThreadSpec, entry: Vector | None = None) -> ThreadJson:
    """Where the thread on `face` is, as the JSON the viewer and `modeled` read.

    `entry`, when given, is a point by the end the thread starts from (a tapped
    hole's mouth); otherwise `spec.from_end` picks the end.
    """
    found = bore(face)
    if found is None:
        raise ValueError(
            f"thread {spec.label} needs a cylindrical face, and the selector found "
            f"a {face.geom_type.name.lower()} face"
        )
    pitch, major, diameter = spec.pitch, spec.major, 2 * found.radius
    low, high = (
        (major - 1.25 * pitch, major + 0.05)
        if found.internal
        else (major - 0.5 * pitch, major + 0.05)
    )
    if not low <= diameter <= high:
        kind = "hole" if found.internal else "shaft"
        raise ValueError(
            f"thread {spec.label}: a {spec.designation} {kind} is {low:g} to "
            f"{high:g} mm across, and this face is {diameter:g} mm"
        )
    face_length = found.v_max - found.v_min
    length = spec.length if spec.length is not None else face_length - spec.offset
    if length <= 0 or spec.offset + length > face_length + 1e-6:
        raise ValueError(
            f"thread {spec.label}: offset {spec.offset:g} plus length {length:g} mm "
            f"does not fit on a face {face_length:g} mm long"
        )
    if entry is None:
        from_end = spec.from_end
    else:
        along = (entry - found.origin).dot(found.direction)
        from_end = abs(along - found.v_max) < abs(along - found.v_min)
    if from_end:
        direction = -found.direction
        start = found.origin + found.direction * (found.v_max - spec.offset)
    else:
        direction = found.direction
        start = found.origin + found.direction * (found.v_min + spec.offset)
    return {
        "start": [round(value, 9) for value in start],
        "direction": [round(value, 9) for value in direction],
        "length": round(length, 9),
        "radius": round(found.radius, 9),
        "major_diameter": major,
        "pitch": pitch,
        "internal": found.internal,
        "hand": spec.hand,
        "designation": spec.designation,
    }


def thread_geometry(feature: dict[str, object], ctx: "Context") -> ThreadJson:
    """The thread op's geometry, read from its base.

    Read each time, so a cached replay that skips `apply` still gives the viewer
    the same answer.
    """
    label = cast("str", feature["id"])
    base = ctx.results[feature["base"]]
    faces = resolve_selector(base, feature["face"], ctx)
    if len(faces) != 1 or not isinstance(faces[0], Face):
        raise SelectorError(
            f"thread {label} needs a selector that matches one face, and it matched {len(faces)}"
        )
    return face_thread(faces[0], ThreadSpec.parse(label, feature))


def _bolt(major: float, pitch: float, length: float, hand: Hand, ends: Ends) -> Shape:
    """A threaded rod along +Z from z = 0: bd_warehouse's ISO thread on a core.

    bd_warehouse builds the thread as one solid per turn, rooted at the ISO
    internal minor diameter. Three things kept it whole in the 108 cases tried
    (M1.6 to M16, clearance 0 and 0.3, each end, 5.3 and 8.71 turns):
    - A core 0.05 pitch proud of that root. A core at the root, or a fixed
      0.2 mm `interference`, left turns loose from M1.6 to M4.
    - `interference` scaled to the pitch.
    - Square and chamfer ends cut from a thread a pitch longer each way.
      bd_warehouse's own square and chamfer ends gave invalid solids whenever
      the length was not a whole number of turns.
    The tap uses the same core, so a modeled bolt and nut still meet with no
    gap at clearance 0.
    """
    try:
        # The `threads` extra is optional; cosmetic threads need nothing.
        from bd_warehouse.thread import IsoThread  # noqa: PLC0415
    except ImportError:
        raise ValueError(
            "a modeled thread needs the `threads` extra: uv sync --extra threads"
        ) from None
    core_radius = major / 2 - (INTERNAL_MINOR / 2 - CORE_PROUD) * pitch
    if ends == "fade":
        thread = IsoThread(
            major,
            pitch,
            length,
            external=True,
            hand=hand,
            end_finishes=("fade", "fade"),
            interference=0.1 * pitch,
        )
        return Cylinder(core_radius, length, align=BOTTOM).fuse(thread)
    thread = IsoThread(
        major,
        pitch,
        length + 2 * pitch,
        external=True,
        hand=hand,
        end_finishes=("raw", "raw"),
        interference=0.1 * pitch,
    )
    # Raw ends reach about a pitch past the thread's own length.
    core = Pos(0, 0, -pitch) * Cylinder(core_radius, length + 4 * pitch, align=BOTTOM)
    long = Pos(0, 0, -pitch) * core.fuse(thread)
    outer = major / 2 + 0.1
    keep = Cylinder(outer, length, align=BOTTOM)
    if ends == "chamfer":
        height = outer - core_radius
        cone = Cone(core_radius, outer, height, align=BOTTOM)
        far_end = Plane(origin=(0, 0, length), z_dir=(0, 0, -1))
        middle = Pos(0, 0, height) * Cylinder(outer, length - 2 * height, align=BOTTOM)
        keep = middle.fuse(cone, far_end * cone)
    trimmed = long.intersect(keep)
    pieces = trimmed if isinstance(trimmed, list) else [trimmed]
    if len(pieces) != 1 or pieces[0] is None:
        raise ValueError(f"the thread did not trim to one solid ({len(pieces)} pieces)")
    return pieces[0]


def _tap(length: float, spec: ThreadSpec) -> Shape:
    """The space an internal thread leaves, in the thread's own frame.

    That is a bolt of the same size, plus a 90 degree lead-in at each end for
    `chamfer`.

    bd_warehouse's own internal thread fused into a hole lost turns or most of its
    teeth above M3 x 4; cutting with an external form came out whole every time.
    """
    major = spec.major + spec.clearance
    ends = spec.ends or "square"
    tap = _bolt(major, spec.pitch, length, spec.hand, "square" if ends == "chamfer" else ends)
    if ends == "chamfer":
        core = major / 2 - (INTERNAL_MINOR / 2 - CORE_PROUD) * spec.pitch
        lead_in = Cone(major / 2, core, major / 2 - core, align=BOTTOM)
        far_end = Plane(origin=(0, 0, length), z_dir=(0, 0, -1))
        tap = tap.fuse(lead_in, far_end * lead_in)
    return tap


def modeled(base: Shape, geometry: ThreadJson, spec: ThreadSpec) -> Shape:
    """`base` with the thread `face_thread` found cut into it.

    A hole loses a tap's shape, a shaft everything around a bolt's shape.
    """
    length = geometry["length"]
    place = Plane(origin=Vector(*geometry["start"]), z_dir=Vector(*geometry["direction"]))
    if geometry["internal"]:
        return base.cut(place * _tap(length, spec))
    # Square ends on a full-length shaft once left an invalid solid, so a shaft
    # fades unless asked otherwise.
    bolt = _bolt(spec.major - spec.clearance, spec.pitch, length, spec.hand, spec.ends or "fade")
    die = Cylinder(geometry["radius"] + 1, length, align=BOTTOM).cut(bolt)
    return base.cut(place * die)


def checked(result: Shape, base: Shape, label: str) -> Part:
    """`result` as a Part, or an error when the thread split or broke the solid."""
    if not result.is_valid or len(result.solids()) != len(base.solids()):
        raise ValueError(
            f"{label}: the modeled thread did not fuse into one valid solid; leave it cosmetic"
        )
    return Part(result.solids())


@register("thread", SCHEMA)
def apply(feature: dict[str, object], ctx: "Context") -> Shape:
    """Thread the face: record it when cosmetic, cut it when modeled."""
    base = ctx.results[feature["base"]]
    geometry = thread_geometry(feature, ctx)
    spec = ThreadSpec.parse(cast("str", feature["id"]), feature)
    if not spec.modeled:
        return base
    return checked(modeled(base, geometry, spec), base, f"thread {spec.label}")
