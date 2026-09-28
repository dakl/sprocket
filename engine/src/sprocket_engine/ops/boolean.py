from build123d import Part, ShapeList

from .registry import register

SCHEMA = {
    "type": "object",
    "required": ["id", "type", "operation", "base", "tool"],
    "properties": {
        "id": {"type": "string"},
        "type": {"const": "boolean"},
        "operation": {"enum": ["union", "subtract", "intersect"]},
        "base": {"type": "string", "description": "id of the feature kept as the body"},
        "tool": {"type": "string", "description": "id of the feature acting on it"},
    },
}

OPERATIONS = {
    "union": lambda base, tool: base.fuse(tool),
    "subtract": lambda base, tool: base.cut(tool),
    "intersect": lambda base, tool: base.intersect(tool),
}


@register("boolean", SCHEMA)
def apply(feature: dict, ctx):
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
        solids = []
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
