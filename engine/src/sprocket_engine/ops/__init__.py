# Import op modules so their @register decorators run.
from . import (
    boolean,
    chamfer,
    construct,
    draft,
    extrude,
    extrude_face,
    fillet,
    hole,
    loft,
    mirror,
    pattern,
    reference,
    revolve,
    shell,
    sketch,
    split,
    sweep,
    thread,
)
from .registry import REGISTRY, Op, register

__all__ = ["REGISTRY", "Op", "register"]
