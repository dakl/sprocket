"""The document's JSON, as types.

A document is JSON on disk and on the wire, and the engine keeps it as JSON:
expressions such as `"=width * 2"` sit in any number field until replay, and
the BREP cache key is a hash of the feature's JSON. So a feature stays a dict,
and these TypedDicts say what each op's dict holds once its JSON Schema has
checked it. The schemas in `ops/` are the authority; a field added there is
added here too.

Every TypedDict is `@final`, so a type checker can tell the forms of a union
apart by a key: `"name" in plane` picks the named plane.
"""

from typing import Literal, NotRequired, Required, TypedDict, cast, final

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]
type JSONObject = dict[str, JSON]

# ---- selectors ------------------------------------------------------------


@final
class SelectorStep(TypedDict, total=False):
    """One step of a selector chain. Which fields it uses depends on `op`."""

    op: Required[str]
    axis: str
    start: int
    stop: int
    point: list[float]
    normal: list[float]
    end: str
    feature: str
    box: list[float]
    points: list[list[float]]


@final
class Selector(TypedDict):
    """A query for faces, edges or vertices, re-resolved on every rebuild."""

    select: list[SelectorStep]
    expected_count: NotRequired[int]


# ---- planes and axes ------------------------------------------------------

type OriginPlaneName = Literal["XY", "XZ", "YZ"]


@final
class ExplicitPlane(TypedDict):
    """A plane given by its origin, normal and in-plane x direction."""

    origin: list[float]
    normal: list[float]
    x_dir: list[float]


@final
class NamedPlane(TypedDict):
    """An origin plane, moved along its normal."""

    name: OriginPlaneName
    offset: NotRequired[float]


@final
class ConstructPlaneRef(TypedDict):
    """A plane feature, moved along its normal."""

    construct: str
    offset: NotRequired[float]


@final
class FacePlane(TypedDict):
    """A planar face of an earlier feature, found again on every rebuild."""

    feature: str
    face: Selector
    offset: NotRequired[float]


type PlaneRef = (
    OriginPlaneName | ExplicitPlane | NamedPlane | ConstructPlaneRef | FacePlane
)


@final
class ExplicitAxis(TypedDict):
    """An axis given by a point on it and its direction."""

    origin: list[float]
    direction: list[float]


@final
class ConstructAxisRef(TypedDict):
    """An axis feature."""

    construct: str


@final
class EdgeAxis(TypedDict):
    """The axis of one edge of `base`: a line's own, or a circle's centre line."""

    base: str
    edge: Selector


@final
class FaceAxis(TypedDict):
    """The axis of a cylindrical, conical or revolved face of `base`."""

    base: str
    face: Selector


type AxisRef = (
    Literal["X", "Y", "Z"] | ExplicitAxis | ConstructAxisRef | EdgeAxis | FaceAxis
)

# ---- sketches -------------------------------------------------------------

type EntityKind = Literal["line", "arc", "circle"]


@final
class EntityReference(TypedDict, total=False):
    """Where a projected entity comes from: an earlier sketch's entity, or a body's edge."""

    sketch: str
    entity: str
    feature: str
    edge: Selector


@final
class Entity(TypedDict, total=False):
    """A line, arc or circle of a sketch. Which points it has depends on `kind`."""

    id: Required[str]
    kind: Required[EntityKind]
    p0: list[float]
    p1: list[float]
    c: list[float]
    r: float
    a0: float
    a1: float
    construction: bool
    reference: EntityReference


@final
class Constraint(TypedDict, total=False):
    """A sketch constraint. Which references it uses depends on `kind`."""

    id: str
    kind: Required[str]
    e: str
    a: str
    b: str
    value: float


@final
class SketchLinear(TypedDict):
    """Copies in a row, `step` apart."""

    count: int
    step: list[float]


@final
class SketchCircular(TypedDict):
    """Copies around a centre."""

    center: list[float]
    count: int
    angle: NotRequired[float]


@final
class SketchPattern(TypedDict):
    """Copies of some of a sketch's entities, made before its regions are found."""

    entities: list[str]
    linear: NotRequired[SketchLinear]
    linear2: NotRequired[SketchLinear]
    circular: NotRequired[SketchCircular]


@final
class SketchRect(TypedDict):
    """A rectangle sketch's one shape."""

    width: float
    height: float
    center: NotRequired[list[float]]


@final
class SketchCircle(TypedDict):
    """A circle sketch's one shape."""

    radius: float
    center: NotRequired[list[float]]


@final
class SketchFeature(TypedDict):
    """A flat drawing on a plane: entities, or a single rect, polygon or circle."""

    id: str
    type: Literal["sketch"]
    plane: PlaneRef
    rect: NotRequired[SketchRect]
    polygon: NotRequired[list[list[float]]]
    circle: NotRequired[SketchCircle]
    entities: NotRequired[list[Entity]]
    constraints: NotRequired[list[Constraint]]
    patterns: NotRequired[list[SketchPattern]]


@final
class ProfileRef(TypedDict):
    """One region of a sketch, named by the entities around it."""

    sketch: str
    bounded_by: list[str]
    inside: NotRequired[list[float]]


@final
class PathRef(TypedDict):
    """Entities of a sketch that join end to end into one path."""

    sketch: str
    entities: NotRequired[list[str]]


# ---- solid ops ------------------------------------------------------------

type CombineMode = Literal["join", "cut", "intersect", "new"]


@final
class ExtrudeFeature(TypedDict):
    """A sketch pushed into a solid."""

    id: str
    type: Literal["extrude"]
    profile: NotRequired[str]
    profiles: NotRequired[list[ProfileRef]]
    base: NotRequired[str]
    distance: float
    mode: NotRequired[CombineMode]
    symmetric: NotRequired[bool]


@final
class ExtrudeFaceFeature(TypedDict):
    """One face of a solid pushed or pulled."""

    id: str
    type: Literal["extrude_face"]
    base: str
    face: Selector
    distance: float


@final
class RevolveFeature(TypedDict):
    """A sketch swept around an axis."""

    id: str
    type: Literal["revolve"]
    profile: str
    axis: AxisRef
    angle: NotRequired[float]


@final
class BooleanFeature(TypedDict):
    """Two earlier features combined."""

    id: str
    type: Literal["boolean"]
    operation: Literal["union", "subtract", "intersect"]
    base: str
    tool: str


@final
class FilletFeature(TypedDict):
    """Selected edges rounded."""

    id: str
    type: Literal["fillet"]
    base: str
    edges: Selector
    radius: float


@final
class ChamferFeature(TypedDict):
    """Selected edges cut back to a bevel."""

    id: str
    type: Literal["chamfer"]
    base: str
    edges: Selector
    length: float


@final
class ShellFeature(TypedDict):
    """A solid hollowed into walls of one thickness."""

    id: str
    type: Literal["shell"]
    base: str
    thickness: float
    open: NotRequired[Selector]
    outward: NotRequired[bool]


type HoleKind = Literal["simple", "counterbore", "countersink"]


@final
class HoleFeature(TypedDict):
    """Holes drilled at points, or at a sketch's circle centres."""

    id: str
    type: Literal["hole"]
    base: str
    sketch: NotRequired[str]
    entities: NotRequired[list[str]]
    points: NotRequired[list[list[float]]]
    direction: NotRequired[list[float]]
    kind: NotRequired[HoleKind]
    diameter: float
    depth: NotRequired[float]
    through: NotRequired[bool]
    head_diameter: NotRequired[float]
    head_depth: NotRequired[float]
    head_angle: NotRequired[float]


@final
class PatternLinear(TypedDict):
    """Copies in a row. `direction` is a world vector or an axis."""

    direction: list[float] | AxisRef
    count: int
    spacing: float


@final
class PatternCircular(TypedDict):
    """Copies around an axis."""

    axis: AxisRef
    count: int
    angle: NotRequired[float]


@final
class PatternFeature(TypedDict):
    """Copies of a solid, combined with `base`."""

    id: str
    type: Literal["pattern"]
    tool: str
    base: NotRequired[str]
    mode: NotRequired[Literal["join", "cut"]]
    linear: NotRequired[PatternLinear]
    linear2: NotRequired[PatternLinear]
    circular: NotRequired[PatternCircular]


@final
class MirrorFeature(TypedDict):
    """A solid and its mirror image, combined with `base`."""

    id: str
    type: Literal["mirror"]
    tool: str
    plane: PlaneRef
    base: NotRequired[str]
    mode: NotRequired[Literal["join", "cut"]]


@final
class SweepFeature(TypedDict):
    """A sketch profile moved along a path."""

    id: str
    type: Literal["sweep"]
    profile: NotRequired[str]
    profiles: NotRequired[list[ProfileRef]]
    path: PathRef
    base: NotRequired[str]
    mode: NotRequired[CombineMode]


@final
class LoftFeature(TypedDict):
    """Sketch profiles joined, in order, into one solid."""

    id: str
    type: Literal["loft"]
    sections: list[str]
    ruled: NotRequired[bool]
    base: NotRequired[str]
    mode: NotRequired[CombineMode]


@final
class SplitFeature(TypedDict):
    """A body cut in two along a plane."""

    id: str
    type: Literal["split"]
    base: str
    plane: PlaneRef
    keep: NotRequired[Literal["above", "below", "both"]]


@final
class DraftFeature(TypedDict):
    """Faces tilted about a neutral plane."""

    id: str
    type: Literal["draft"]
    base: str
    faces: Selector
    angle: float
    neutral: PlaneRef


@final
class ThreadFeature(TypedDict):
    """A thread on a cylindrical face, cosmetic or modelled."""

    id: str
    type: Literal["thread"]
    base: str
    face: Selector
    designation: str
    pitch: NotRequired[float]
    modeled: NotRequired[bool]


@final
class ReferenceFeature(TypedDict):
    """Another document or a STEP file, brought in as a reference body.

    `Document.for_replay` makes `file` absolute and adds `source_sha256`.
    """

    id: str
    type: Literal["reference"]
    file: str
    rotate: NotRequired[list[float]]
    translate: NotRequired[list[float]]
    source_sha256: NotRequired[str | None]


# ---- construct features ---------------------------------------------------


@final
class PlaneFeature(TypedDict):
    """A named plane later features can use."""

    id: str
    type: Literal["plane"]
    plane: PlaneRef
    offset: NotRequired[float]


@final
class AxisFeature(TypedDict):
    """A named axis later features can use."""

    id: str
    type: Literal["axis"]
    axis: AxisRef


@final
class PointFeature(TypedDict):
    """A named point."""

    id: str
    type: Literal["point"]
    at: list[float]


# ---- documents ------------------------------------------------------------


@final
class ReplayDocument(TypedDict):
    """What `replay()` takes: features with every expression resolved, and the bar."""

    features: list[JSONObject]
    rollback_index: NotRequired[int]


def entity_point(entity: Entity, name: Literal["p0", "p1", "c"]) -> list[float]:
    """Return one of the entity's points, which its kind must have."""
    if name == "p0":
        value = entity.get("p0")
    elif name == "p1":
        value = entity.get("p1")
    else:
        value = entity.get("c")
    if value is None:
        msg = f"{entity['kind']} {entity['id']!r} needs {name}"
        raise ValueError(msg)
    return value


def entity_number(entity: Entity, name: Literal["r", "a0", "a1"]) -> float:
    """Return the entity's radius or one of its angles, which its kind must have."""
    if name == "r":
        value = entity.get("r")
    elif name == "a0":
        value = entity.get("a0")
    else:
        value = entity.get("a1")
    if value is None:
        msg = f"{entity['kind']} {entity['id']!r} needs {name}"
        raise ValueError(msg)
    return value


def sketch_feature(feature: JSONObject) -> SketchFeature | None:
    """Return the feature as a sketch, or None if it is another op's.

    Only for a feature its op's schema has checked: replay validates every
    feature before it runs, and a document validates every edit.
    """
    if feature.get("type") != "sketch":
        return None
    return cast("SketchFeature", cast("object", feature))


def to_json(feature: SketchFeature) -> JSONObject:
    """Return a sketch feature as the plain JSON object it is."""
    return cast("JSONObject", cast("object", feature))
