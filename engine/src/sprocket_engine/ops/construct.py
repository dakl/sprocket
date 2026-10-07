"""Construct features: a plane, an axis and a point that later features name.

They make no solid. Each result is a one-edge marker, so the cache can store it,
and the geometry is read from the feature itself (`construct_plane`,
`resolve_axis`). A sketch, a mirror, a pattern or a revolve names one as
`{"construct": "f3"}` and follows it when it moves.
"""

from build123d import Axis, Compound, Edge, GeomType, Plane

from ..selectors import AXES, SelectorError, resolve_selector
from .registry import register
from .sketch import OFFSET, PLANE_SCHEMA, VECTOR3, resolve_plane

# Feature types that hold a reference, not a body. Replay skips them when it
# looks for what the document makes.
CONSTRUCT_TYPES = ("plane", "axis", "point")

AXIS_SCHEMA = {
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
            "description": "An axis feature (type `axis`): {\"construct\": \"f4\"}.",
            "properties": {"construct": {"type": "string", "description": "id of an axis feature"}},
        },
        {
            "type": "object",
            "required": ["base", "edge"],
            "description": (
                "Found on every rebuild from one edge of the feature `base`: a straight edge gives its own line, "
                "and a circle or arc gives the line through its centre, square to its plane."
            ),
            "properties": {"base": {"type": "string"}, "edge": {"type": "object", "description": "a selector that matches one edge"}},
        },
        {
            "type": "object",
            "required": ["base", "face"],
            "description": "The axis of a cylindrical, conical or revolved face of the feature `base`, from a selector that matches one face.",
            "properties": {"base": {"type": "string"}, "face": {"type": "object", "description": "a selector that matches one face"}},
        },
    ]
}

PLANE_SCHEMA_OF_FEATURE = {
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

AXIS_SCHEMA_OF_FEATURE = {
    "type": "object",
    "description": "A named axis that a circular pattern, a linear pattern or a revolve can use. `axis` says where it is.",
    "required": ["id", "type", "axis"],
    "properties": {"id": {"type": "string"}, "type": {"const": "axis"}, "axis": AXIS_SCHEMA},
}

POINT_SCHEMA_OF_FEATURE = {
    "type": "object",
    "description": "A named point, [x, y, z] in mm, shown in the viewport.",
    "required": ["id", "type", "at"],
    "properties": {"id": {"type": "string"}, "type": {"const": "point"}, "at": {**VECTOR3, "description": "mm"}},
}


def _feature(construct_id, kind, ctx):
    feature = ctx.features.get(construct_id) if ctx is not None else None
    if feature is None or feature.get("type") != kind:
        raise ValueError(f"{construct_id!r} is not a {kind} feature built before this one")
    return feature


def plane_of(feature: dict, ctx) -> Plane:
    """The plane a plane feature makes: its base plane, moved along its normal."""
    return resolve_plane(feature["plane"], ctx).offset(feature.get("offset", 0))


def construct_plane(construct_id, ctx) -> Plane:
    return plane_of(_feature(construct_id, "plane", ctx), ctx)


def _one(shape, selector, kind, ctx):
    found = resolve_selector(shape, selector, ctx)
    if len(found) != 1:
        raise SelectorError(f"the axis selector matched {len(found)} {kind}s, expected exactly 1")
    return found[0]


def _axis_of_edge(edge) -> Axis:
    if edge.geom_type == GeomType.LINE:
        return Axis(edge.position_at(0), edge.tangent_at(0))
    if edge.geom_type == GeomType.CIRCLE:
        return Axis(edge.arc_center, edge.normal())
    raise ValueError(f"an axis needs a straight or circular edge, and this one is a {edge.geom_type.name.lower()}")


def resolve_axis(axis_ref, ctx=None) -> Axis:
    """The build123d Axis for any axis form. `ctx` is needed for the forms that name a feature."""
    if isinstance(axis_ref, str):
        return AXES[axis_ref]
    if "construct" in axis_ref:
        return resolve_axis(_feature(axis_ref["construct"], "axis", ctx)["axis"], ctx)
    if "base" in axis_ref:
        shape = ctx.results.get(axis_ref["base"]) if ctx is not None else None
        if shape is None:
            raise ValueError(f"the axis names {axis_ref['base']!r}, which is not built before this one")
        if "edge" in axis_ref:
            return _axis_of_edge(_one(shape, axis_ref["edge"], "edge", ctx))
        face = _one(shape, axis_ref["face"], "face", ctx)
        axis = face.axis_of_rotation
        if axis is None:
            raise ValueError(f"an axis needs a cylindrical, conical or revolved face, and this one is {face.geom_type.name.lower()}")
        return axis
    return Axis(tuple(axis_ref["origin"]), tuple(axis_ref["direction"]))


def construct_geometry(feature: dict, ctx) -> dict:
    """What the viewer draws for a construct feature, in world coordinates."""
    kind = feature["type"]
    if kind == "plane":
        plane = plane_of(feature, ctx)
        return {"kind": "plane", "origin": [round(v, 9) for v in plane.origin], "normal": [round(v, 9) for v in plane.z_dir], "x_dir": [round(v, 9) for v in plane.x_dir]}
    if kind == "axis":
        axis = resolve_axis(feature["axis"], ctx)
        return {"kind": "axis", "origin": [round(v, 9) for v in axis.position], "direction": [round(v, 9) for v in axis.direction]}
    return {"kind": "point", "at": list(feature["at"])}


def _marker() -> Compound:
    return Compound([Edge.make_line((0, 0, 0), (1, 0, 0))])


@register("plane", PLANE_SCHEMA_OF_FEATURE)
def apply_plane(feature: dict, ctx):
    plane_of(feature, ctx)
    return _marker()


@register("axis", AXIS_SCHEMA_OF_FEATURE)
def apply_axis(feature: dict, ctx):
    resolve_axis(feature["axis"], ctx)
    return _marker()


@register("point", POINT_SCHEMA_OF_FEATURE)
def apply_point(feature: dict, ctx):
    return _marker()
