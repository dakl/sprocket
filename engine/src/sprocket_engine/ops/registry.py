"""Op registry: each op type registers a JSON Schema and an apply function."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from build123d import Compound

from ..context import Context
from ..model import JSONObject

type Apply = Callable[[JSONObject, Context], Compound]


@dataclass
class Op:
    """One op type: the schema its features must meet, and how to build one."""

    name: str
    schema: JSONObject
    apply: Apply


REGISTRY: dict[str, Op] = {}


def register[F](
    name: str, schema: JSONObject
) -> Callable[[Callable[[F, Context], Compound]], Callable[[F, Context], Compound]]:
    """Registers the decorated function as op `name`'s apply.

    The function takes its op's own feature type from `model.py`. The registry
    hands it the feature as JSON, and only after replay has checked that JSON
    against `schema`, which is what makes the cast below true.
    """

    def decorator(
        apply_fn: Callable[[F, Context], Compound],
    ) -> Callable[[F, Context], Compound]:
        def apply_json(feature: JSONObject, ctx: Context) -> Compound:
            return apply_fn(cast("F", cast("object", feature)), ctx)

        REGISTRY[name] = Op(name, schema, apply_json)
        return apply_fn

    return decorator
