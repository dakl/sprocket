"""Deterministic replay: feature list -> final shape.

A document is an ordered feature list plus a rollback index. Replaying
features[0:rollback_index] in order produces the model. Each feature's
result is cached by content hash (see cache.py), so scrubbing the rollback
bar is a cache lookup, not a rebuild.
"""

from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
from build123d import Compound

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
    # The features replayed so far, by id. An extrude that picks a region needs
    # the sketch's entities, which the sketch's shape no longer carries.
    features: dict = field(default_factory=dict)
    # The document's bodies after the replay, see find_bodies.
    bodies: list = field(default_factory=list)


# Ops whose `tool` ends the tool's body: its solid went into another body.
CONSUMES_TOOL = {"boolean", "pattern"}


def find_bodies(features: list[dict], ctx: Context) -> list[dict]:
    """The bodies the replayed features make, in the order they started.

    A solid feature with no base starts a body, whose id is that feature's id.
    A feature with a base continues the base's body, and the body's shape is
    the result of its latest feature. A boolean or pattern ends its tool's
    body. A body started by a `reference` op is a reference body. See SPEC.md,
    "M5, bodies".
    """
    root_of, tip_of, order, consumed = {}, {}, [], set()
    for feature in features:
        feature_id = feature["id"]
        if feature.get("type") == "sketch" or feature_id not in ctx.results:
            continue
        base = feature.get("base")
        root = root_of.get(base, feature_id) if base else feature_id
        root_of[feature_id] = root
        if root not in tip_of:
            order.append(root)
        tip_of[root] = feature_id
        tool = feature.get("tool")
        if feature.get("type") in CONSUMES_TOOL and tool in root_of and root_of[tool] != root:
            consumed.add(root_of[tool])
    # A reference body is never consumed: cutting with it keeps it on screen.
    reference = {root for root in order if ctx.features[root].get("type") == "reference"}
    return [
        {"id": root, "tip": tip_of[root], "shape": ctx.results[tip_of[root]], "reference": root in reference}
        for root in order
        if root not in consumed or root in reference
    ]


def with_settings(bodies: list[dict], settings: dict) -> list[dict]:
    """Each body with the document's settings for it: name, colour, visibility."""
    merged = []
    for body in bodies:
        own = settings.get(body["id"], {})
        merged.append({**body, "name": own.get("name", body["id"]), "color": own.get("color"), "visible": own.get("visible", True)})
    return merged


def combine(bodies: list[dict]):
    """One shape for the part: the only body itself, so a one-body document
    exports exactly as before, or a compound of the bodies in order. Reference
    bodies are left out, because they are not part of what is made."""
    shapes = [body["shape"] for body in bodies if not body["reference"]]
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    return Compound(children=shapes)


def replay(document: dict, cache_dir: Path | None = None, return_context: bool = False):
    """Returns the shape after replaying features[:rollback_index], or None
    when that prefix is empty. rollback_index=0 is a valid state, the same
    as Fusion 360's timeline rolled back before the first feature - it means
    an empty document, not an error.

    With `return_context`, returns (shape, context): every feature's result,
    which is what selector synthesis needs to see how the part was built."""
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
        ctx.features[feature["id"]] = feature
        upstream_key = key
        last_result = result

    ctx.bodies = find_bodies(features[:rollback_index], ctx)
    # A document of sketches only has no body, and replay hands back the last sketch.
    part = combine(ctx.bodies) if ctx.bodies else last_result
    return (part, ctx) if return_context else part


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
) -> tuple[object, str | None, Context]:
    """Replays as much of the document as still builds.

    Returns (shape, failed_feature_id, context). The context holds every
    feature's result, which a sketch on a face needs to find its plane. A feature that no longer builds is not
    a failed build: editing a sketch can break a later op, and the caller still
    needs the part up to that point. So this falls back to the prefix before
    the broken feature and names it, rather than raising.

    This is the one copy of that policy. The CLI and the daemon both come
    through here, because a daemon that tolerates a broken tip differently
    from `sprocket build` would show the app a part the exporter never writes.
    """
    export_index = last_solid_index(features, rollback_index)
    try:
        shape, ctx = replay({"features": features, "rollback_index": export_index}, cache_dir=cache_dir, return_context=True)
        return shape, None, ctx
    except Exception as exc:
        failed_index = getattr(exc, "feature_index", None)
        failed_id = getattr(exc, "feature_id", None)
        if failed_index is None:
            raise
        good_index = last_solid_index(features, failed_index)
        shape, ctx = replay({"features": features, "rollback_index": good_index}, cache_dir=cache_dir, return_context=True)
        return shape, failed_id, ctx
