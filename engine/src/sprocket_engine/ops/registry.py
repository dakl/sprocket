"""Op registry: each op type registers a JSON Schema and an apply function."""

REGISTRY: dict[str, "Op"] = {}


class Op:
    def __init__(self, name: str, schema: dict, apply):
        self.name = name
        self.schema = schema
        self.apply = apply


def register(name: str, schema: dict):
    def decorator(apply_fn):
        REGISTRY[name] = Op(name, schema, apply_fn)
        return apply_fn

    return decorator
