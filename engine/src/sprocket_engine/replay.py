"""Deterministic replay: feature list -> final shape.

A document is an ordered feature list plus a rollback index. Replaying
features[0:rollback_index] in order produces the model. Each feature's
result is cached by content hash (see cache.py), so scrubbing the rollback
bar is a cache lookup, not a rebuild.
"""

from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from .cache import BrepCache, feature_cache_key
from .ops import REGISTRY


class ReplayError(Exception):
    pass


@dataclass
class Context:
    """Carries each feature's result forward so later features can reference it."""

    results: dict = field(default_factory=dict)


def replay(document: dict, cache_dir: Path | None = None):
    features = document["features"]
    rollback_index = document.get("rollback_index", len(features))
    cache = BrepCache(cache_dir) if cache_dir else None

    ctx = Context()
    upstream_key = "root"
    last_result = None

    for feature in features[:rollback_index]:
        op_type = feature.get("type")
        op = REGISTRY.get(op_type)
        if op is None:
            raise ReplayError(f"unknown op type {op_type!r} in feature {feature.get('id')!r}")

        try:
            jsonschema.validate(feature, op.schema)
        except jsonschema.ValidationError as exc:
            raise ReplayError(f"feature {feature.get('id')!r} failed validation: {exc.message}") from exc

        key = feature_cache_key(feature, upstream_key)
        result = cache.get(key, op_type) if cache else None
        if result is None:
            result = op.apply(feature, ctx)
            if cache:
                cache.put(key, result)

        ctx.results[feature["id"]] = result
        upstream_key = key
        last_result = result

    if last_result is None:
        raise ReplayError("document has no features to replay")

    return last_result
