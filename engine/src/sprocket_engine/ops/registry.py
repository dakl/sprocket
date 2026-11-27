"""Op registry: each op type registers a JSON Schema and an apply function."""

from functools import cached_property

from jsonschema import exceptions, validators

REGISTRY: dict[str, "Op"] = {}


class Op:
    def __init__(self, name: str, schema: dict, apply):
        self.name = name
        self.schema = schema
        self.apply = apply

    @cached_property
    def _validator(self):
        cls = validators.validator_for(self.schema)
        cls.check_schema(self.schema)
        return cls(self.schema)

    def validate(self, feature: dict) -> None:
        """`jsonschema.validate(feature, self.schema)`, with the validator built once.

        `jsonschema.validate` checks the schema against its meta-schema and
        builds a new validator on every call. Over a 931-feature document that
        was 7 of the 8 seconds a fully cached replay took.
        """
        error = exceptions.best_match(self._validator.iter_errors(feature))
        if error is not None:
            raise error


def register(name: str, schema: dict):
    def decorator(apply_fn):
        REGISTRY[name] = Op(name, schema, apply_fn)
        return apply_fn

    return decorator
