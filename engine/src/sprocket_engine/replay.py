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


def _tagged(error, feature, index):
    """Records which feature failed, so the caller can name it to the user and
    still export everything built before it."""
    error.feature_id = feature.get("id")
    error.feature_index = index
    return error


@dataclass
class Context:
    """Carries each feature's result forward so later features can reference it."""

    results: dict = field(default_factory=dict)


def replay(document: dict, cache_dir: Path | None = None):
    """Returns the shape after replaying features[:rollback_index], or None
    when that prefix is empty. rollback_index=0 is a valid state, the same
    as Fusion 360's timeline rolled back before the first feature - it means
    an empty document, not an error."""
    features = document["features"]
    rollback_index = document.get("rollback_index", len(features))
    cache = BrepCache(cache_dir) if cache_dir else None

    ctx = Context()
    upstream_key = "root"
    last_result = None

    for index, feature in enumerate(features[:rollback_index]):
        op_type = feature.get("type")
        op = REGISTRY.get(op_type)
        if op is None:
            raise _tagged(ReplayError(f"unknown op type {op_type!r} in feature {feature.get('id')!r}"), feature, index)

        try:
            jsonschema.validate(feature, op.schema)
        except jsonschema.ValidationError as exc:
            error = ReplayError(f"feature {feature.get('id')!r} failed validation: {exc.message}")
            raise _tagged(error, feature, index) from exc

        key = feature_cache_key(feature, upstream_key)
        result = cache.get(key) if cache else None
        if result is None:
            try:
                result = op.apply(feature, ctx)
            except Exception as exc:
                # Editing a sketch can leave a later feature unbuildable: a
                # face selector may match nothing, or a cut may miss the part.
                # The caller needs to know which feature broke, so tag the
                # original exception instead of wrapping it in a new type.
                raise _tagged(exc, feature, index) from None
            if cache:
                cache.put(key, result)

        ctx.results[feature["id"]] = result
        upstream_key = key
        last_result = result

    return last_result


def last_solid_index(features: list[dict], rollback_index: int) -> int:
    """A sketch is a flat profile, not a solid (see ops/sketch.py) - it stays
    inert until something extrudes it. replay() only returns its last
    feature's result, so a bare sketch left at the tip would become the part
    and blank out whatever solid came before it. Walk back past any trailing
    sketch features to the last one that actually produced a solid."""
    index = rollback_index
    while index > 0 and features[index - 1].get("type") == "sketch":
        index -= 1
    return index


def build_shape(
    features: list[dict], rollback_index: int, cache_dir: Path | None
) -> tuple[object, str | None]:
    """Replays as much of the document as still builds.

    Returns (shape, failed_feature_id). A feature that no longer builds is not
    a failed build: editing a sketch can break a later op, and the caller still
    needs the part up to that point. So this falls back to the prefix before
    the broken feature and names it, rather than raising.

    This is the one copy of that policy. The CLI and the daemon both come
    through here, because a daemon that tolerates a broken tip differently
    from `sprocket build` would show the app a part the exporter never writes.
    """
    export_index = last_solid_index(features, rollback_index)
    try:
        return replay({"features": features, "rollback_index": export_index}, cache_dir=cache_dir), None
    except Exception as exc:
        failed_index = getattr(exc, "feature_index", None)
        failed_id = getattr(exc, "feature_id", None)
        if failed_index is None:
            raise
        good_index = last_solid_index(features, failed_index)
        return replay({"features": features, "rollback_index": good_index}, cache_dir=cache_dir), failed_id
