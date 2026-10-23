"""Checked reads of the JSON the engine hands back, for tests.

A reply is JSON, so `reply["built"]["ok"]` is not a typed expression: the
first lookup could be a list or a number. These assert what the test expects
the value to be, which is also what the test is about.
"""

from typing import cast

from sprocket_engine.model import JSON, JSONObject, ReplayDocument, parse_json


def obj(value: object) -> JSONObject:
    """`value`, which must be a JSON object."""
    assert isinstance(value, dict), f"expected an object, got {value!r}"
    return cast("JSONObject", value)


def items(value: object) -> list[JSON]:
    """`value`, which must be a JSON array."""
    assert isinstance(value, list), f"expected a list, got {value!r}"
    return cast("list[JSON]", value)


def objects(value: object) -> list[JSONObject]:
    """`value`, which must be a JSON array of objects."""
    return [obj(item) for item in items(value)]


def number(value: object) -> float:
    """`value`, which must be a JSON number."""
    assert isinstance(value, int | float), f"expected a number, got {value!r}"
    assert not isinstance(value, bool), f"expected a number, got {value!r}"
    return value


def text(value: object) -> str:
    """`value`, which must be a JSON string."""
    assert isinstance(value, str), f"expected a string, got {value!r}"
    return value


def document(
    features: list[JSONObject], rollback_index: int | None = None
) -> ReplayDocument:
    """A replayable document of `features`, built to `rollback_index` or the end."""
    built: ReplayDocument = {"features": features}
    if rollback_index is not None:
        built["rollback_index"] = rollback_index
    return built


def loads(text: str | bytes) -> JSONObject:
    """JSON text that must hold an object."""
    return obj(parse_json(text))
