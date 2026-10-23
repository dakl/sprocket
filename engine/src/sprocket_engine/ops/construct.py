"""Construct features: a plane, an axis and a point that later features name.

They make no solid. Each result is a one-edge marker, so the cache can store it,
and the geometry is read from the feature itself (`construct_plane`,
`resolve_axis`). A sketch, a mirror, a pattern or a revolve names one as
`{"construct": "f3"}` and follows it when it moves.
"""

from typing import Literal, TypedDict, cast, final

from build123d import Axis, Compound, Edge, Face, GeomType, Vector

from sprocket_engine.context import Context
from sprocket_engine.model import (
    AxisFeature,
    AxisRef,
    JSONObject,
    PlaneFeature,
    PointFeature,
    Selector,
)
from sprocket_engine.ops.registry import register
from sprocket_engine.ops.sketch import (
    OFFSET,
    PLANE_SCHEMA,
    VECTOR3,
    not_built,
    plane_of,
)
from sprocket_engine.selectors import AXES, Item, SelectorError, resolve_selector

# Feature types that hold a reference, not a body. Replay skips them when it
# looks for what the document makes.
CONSTRUCT_TYPES = ("plane", "axis", "point")

AXIS_SCHEMA: JSONObject = {
    "oneOf": [
        {"enum": ["X", "Y", "Z"]},
        {
            "type": "object",
            "required": ["origin", "direction"],
            "properties": {"origin": VECTOR3, "direction": VECTOR3},
        },
        {
            "type": "object",
            "required": ["construct"],
            "description": 'An axis feature (type `axis`): {"construct": "f4"}.',
            "properties": {
                "construct": {"type": "string", "description": "id of an axis feature"}
            },
        },
        {
            "type": "object",
            "required": ["base", "edge"],
            "description": (
                "Found on every rebuild from one edge of the feature `base`: a straight edge gives its own line, "
                "and a circle or arc gives the line through its centre, square to its plane."
            ),
            "properties": {
                "base": {"type": "string"},
                "edge": {
                    "type": "object",
                    "description": "a selector that matches one edge",
                },
            },
        },
        {
            "type": "object",
            "required": ["base", "face"],
            "description": "The axis of a cylindrical, conical or revolved face of the feature `base`, from a selector that matches one face.",
            "properties": {
                "base": {"type": "string"},
                "face": {
                    "type": "object",
                    "description": "a selector that matches one face",
                },
            },
        },
    ]
}

PLANE_SCHEMA_OF_FEATURE: JSONObject = {
    "type": "object",
    "description": (
        "A named plane that later features can use: a sketch plane, a mirror plane. `plane` is where it starts, "
        "any sketch plane form, and `offset` moves it along its normal."
    ),
    "required": ["id", "type", "plane"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "plane"},
        "plane": PLANE_SCHEMA,
        "offset": OFFSET,
    },
}

AXIS_SCHEMA_OF_FEATURE: JSONObject = {
    "type": "object",
    "description": "A named axis that a circular pattern, a linear pattern or a revolve can use. `axis` says where it is.",
    "required": ["id", "type", "axis"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "axis"},
        "axis": AXIS_SCHEMA,
    },
}

POINT_SCHEMA_OF_FEATURE: JSONObject = {
    "type": "object",
    "description": "A named point, [x, y, z] in mm, shown in the viewport.",
    "required": ["id", "type", "at"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "point"},
        "at": {**VECTOR3, "description": "mm"},
    },
}


def _one(
    shape: Compound, selector: Selector, kind: Literal["edge", "face"], ctx: Context
) -> Item:
    found = resolve_selector(shape, selector, ctx)
    if len(found) != 1:
        raise SelectorError(
            f"the axis selector matched {len(found)} {kind}s, expected exactly 1"
        )
    return found[0]


def _axis_of_edge(edge: Edge) -> Axis:
    if edge.geom_type == GeomType.LINE:
        return Axis(edge.position_at(0), edge.tangent_at(0))
    if edge.geom_type == GeomType.CIRCLE:
        return Axis(edge.arc_center, edge.normal())
    raise ValueError(
        f"an axis needs a straight or circular edge, and this one is a {edge.geom_type.name.lower()}"
    )


def resolve_axis(axis_ref: AxisRef, ctx: Context | None = None) -> Axis:
    """The build123d Axis for any axis form. `ctx` is needed for the forms that name a feature."""
    if isinstance(axis_ref, str):
        return AXES[axis_ref]
    if "construct" in axis_ref:
        feature = ctx.axis_feature(axis_ref["construct"]) if ctx is not None else None
        if feature is None:
            raise not_built(axis_ref["construct"], "axis")
        return resolve_axis(feature["axis"], ctx)
    if "base" in axis_ref:
        shape = ctx.results.get(axis_ref["base"]) if ctx is not None else None
        if shape is None or ctx is None:
            raise ValueError(
                f"the axis names {axis_ref['base']!r}, which is not built before this one"
            )
        if "edge" in axis_ref:
            edge = _one(shape, axis_ref["edge"], "edge", ctx)
            if not isinstance(edge, Edge):
                msg = "the axis selector must match an edge"
                raise SelectorError(msg)
            return _axis_of_edge(edge)
        face = _one(shape, axis_ref["face"], "face", ctx)
        if not isinstance(face, Face):
            msg = "the axis selector must match a face"
            raise SelectorError(msg)
        axis = face.axis_of_rotation
        if axis is None:
            raise ValueError(
                f"an axis needs a cylindrical, conical or revolved face, and this one is {face.geom_type.name.lower()}"
            )
        return axis
    origin, direction = axis_ref["origin"], axis_ref["direction"]
    return Axis(
        Vector(origin[0], origin[1], origin[2]),
        Vector(direction[0], direction[1], direction[2]),
    )


@final
class ConstructGeometry(TypedDict, total=False):
    """What the viewer draws for a construct feature: a plane, an axis or a point."""

    kind: Literal["plane", "axis", "point"]
    origin: list[float]
    normal: list[float]
    x_dir: list[float]
    direction: list[float]
    at: list[float]


def construct_geometry(
    feature: PlaneFeature | AxisFeature | PointFeature, ctx: Context | None
) -> ConstructGeometry:
    """What the viewer draws for a construct feature, in world coordinates."""
    if feature["type"] == "plane":
        plane = plane_of(feature, ctx)
        return {
            "kind": "plane",
            "origin": [round(v, 9) for v in plane.origin],
            "normal": [round(v, 9) for v in plane.z_dir],
            "x_dir": [round(v, 9) for v in plane.x_dir],
        }
    if feature["type"] == "axis":
        axis = resolve_axis(feature["axis"], ctx)
        return {
            "kind": "axis",
            "origin": [round(v, 9) for v in axis.position],
            "direction": [round(v, 9) for v in axis.direction],
        }
    return {"kind": "point", "at": list(feature["at"])}


def _marker() -> Compound:
    return Compound([Edge.make_line((0, 0, 0), (1, 0, 0))])


@register("plane", PLANE_SCHEMA_OF_FEATURE)
def apply_plane(feature: PlaneFeature, ctx: Context) -> Compound:
    """Check that the plane resolves, and leave a marker."""
    _ = plane_of(feature, ctx)
    return _marker()


@register("axis", AXIS_SCHEMA_OF_FEATURE)
def apply_axis(feature: AxisFeature, ctx: Context) -> Compound:
    """Check that the axis resolves, and leave a marker."""
    _ = resolve_axis(feature["axis"], ctx)
    return _marker()


@register("point", POINT_SCHEMA_OF_FEATURE)
def apply_point(_feature: PointFeature, _ctx: Context) -> Compound:
    """Leave a marker: a point needs nothing resolved."""
    return _marker()


def construct_feature(
    feature: JSONObject,
) -> PlaneFeature | AxisFeature | PointFeature | None:
    """The feature as a construct feature, or None if it is another op's.

    Only for a feature its op's schema has checked.
    """
    kind = feature.get("type")
    if kind == "plane":
        return cast("PlaneFeature", cast("object", feature))
    if kind == "axis":
        return cast("AxisFeature", cast("object", feature))
    if kind == "point":
        return cast("PointFeature", cast("object", feature))
    return None
