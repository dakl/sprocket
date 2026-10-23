from collections.abc import Callable

from build123d import Compound, Part, ShapeList
from build123d.topology import Shape
from OCP.TopoDS import TopoDS_Shape

from sprocket_engine.b3d import cut, fuse, intersect
from sprocket_engine.context import Context
from sprocket_engine.model import BooleanFeature, JSONObject
from sprocket_engine.ops.registry import register

SCHEMA: JSONObject = {
    "type": "object",
    "description": "Combines two earlier features, by union, subtract or intersect.",
    "required": ["id", "type", "operation", "base", "tool"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "boolean"},
        "operation": {"enum": ["union", "subtract", "intersect"]},
        "base": {"type": "string", "description": "id of the feature kept as the body"},
        "tool": {"type": "string", "description": "id of the feature acting on it"},
    },
}

type Combined = Compound | ShapeList[Shape[TopoDS_Shape]] | None

OPERATIONS: dict[str, Callable[[Compound, Compound], Combined]] = {
    "union": fuse,
    "subtract": cut,
    "intersect": intersect,
}


@register("boolean", SCHEMA)
def apply(feature: BooleanFeature, ctx: Context) -> Compound:
    """Operands are feature ids, because every earlier feature's shape is
    already in `ctx.results` whether or not anything consumed it. An extrude
    nothing else refers to is a second body in all but name, so a boolean can
    reach two independent extrudes with no multi-body machinery.
    """
    base = ctx.results[feature["base"]]
    tool = ctx.results[feature["tool"]]
    result = OPERATIONS[feature["operation"]](base, tool)

    # fuse and cut hand back one shape, intersect a ShapeList. Every op has to
    # return one Part, because that is what replay stores and the tessellator
    # reads.
    if result is None:
        solids: list[Compound] | list[Shape[TopoDS_Shape]] = []
    elif isinstance(result, ShapeList):
        solids = list(result)
    else:
        solids = [result]

    # A subtract whose tool swallows the base, or an intersect of two bodies
    # that never touch, leaves nothing. Replay would carry the empty shape
    # forward and fail several features later, pointing at the wrong one.
    part = Part(solids)
    if not solids or part.volume == 0:
        raise ValueError(
            f"{feature['operation']} of {feature['base']} and {feature['tool']} left an empty solid"
        )
    return part
