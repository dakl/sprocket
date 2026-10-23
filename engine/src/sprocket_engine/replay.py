"""Deterministic replay: feature list -> final shape.

A document is an ordered feature list plus a rollback index. Replaying
features[0:rollback_index] in order produces the model. Each feature's
result is cached by content hash (see cache.py), so scrubbing the rollback
bar is a cache lookup, not a rebuild.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

import jsonschema
from build123d import Compound

from sprocket_engine.cache import BrepCache, feature_cache_key
from sprocket_engine.context import Body, Context
from sprocket_engine.model import JSONObject, ReplayDocument
from sprocket_engine.ops import REGISTRY
from sprocket_engine.ops.construct import CONSTRUCT_TYPES
from sprocket_engine.sketch_reference import has_references, refresh_references

__all__ = [
    "CONSUMES_TOOL",
    "NOT_SOLIDS",
    "Body",
    "Context",
    "FailedAt",
    "ReplayError",
    "build_shape",
    "combine",
    "failed_feature",
    "find_bodies",
    "last_solid_index",
    "replay",
    "with_settings",
]


class ReplayError(Exception):
    """A feature replay cannot apply: an unknown op, or one that fails its schema."""


@dataclass(frozen=True)
class FailedAt:
    """Which feature an exception out of replay came from."""

    feature_id: str | None
    index: int


_FAILED_AT = "sprocket_failed_at"


def _tagged(error: Exception, feature: JSONObject, index: int) -> Exception:
    """Records which feature failed, so the caller can name it to the user and
    still export everything built before it. The exception keeps its own type,
    because callers catch the op's own errors.
    """
    feature_id = feature.get("id")
    vars(error)[_FAILED_AT] = FailedAt(
        feature_id if isinstance(feature_id, str) else None, index
    )
    return error


def failed_feature(error: BaseException) -> FailedAt | None:
    """The feature that raised `error` during replay, or None if replay did not tag it."""
    found: object = getattr(error, _FAILED_AT, None)
    return found if isinstance(found, FailedAt) else None


# A sketch is a flat profile and a construct feature a reference: neither makes a solid.
NOT_SOLIDS = ("sketch", *CONSTRUCT_TYPES)

# Ops whose `tool` ends the tool's body: its solid went into another body.
CONSUMES_TOOL = {"boolean", "pattern", "mirror"}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def find_bodies(features: list[JSONObject], ctx: Context) -> list[Body]:
    """The bodies the replayed features make, in the order they started.

    A solid feature with no base starts a body, whose id is that feature's id.
    A feature with a base continues the base's body, and the body's shape is
    the result of its latest feature. A boolean or pattern ends its tool's
    body. A body started by a `reference` op is a reference body. See SPEC.md,
    "M5, bodies".
    """
    root_of: dict[str, str] = {}
    tip_of: dict[str, str] = {}
    order: list[str] = []
    consumed: set[str] = set()
    for feature in features:
        feature_id = _text(feature.get("id"))
        if (
            feature_id is None
            or feature.get("type") in NOT_SOLIDS
            or feature_id not in ctx.results
        ):
            continue
        base = _text(feature.get("base"))
        root = root_of.get(base, feature_id) if base else feature_id
        root_of[feature_id] = root
        if root not in tip_of:
            order.append(root)
        tip_of[root] = feature_id
        tool = _text(feature.get("tool"))
        if (
            feature.get("type") in CONSUMES_TOOL
            and tool is not None
            and tool in root_of
            and root_of[tool] != root
        ):
            consumed.add(root_of[tool])
    # A reference body is never consumed: cutting with it keeps it on screen.
    reference = {
        root for root in order if ctx.features[root].get("type") == "reference"
    }
    return [
        Body(
            id=root,
            tip=tip_of[root],
            shape=ctx.results[tip_of[root]],
            reference=root in reference,
            name=root,
        )
        for root in order
        if root not in consumed or root in reference
    ]


def with_settings(bodies: list[Body], settings: JSONObject) -> list[Body]:
    """Each body with the document's settings for it: name, colour, finish, visibility."""
    merged: list[Body] = []
    for body in bodies:
        own = settings.get(body.id)
        merged.append(body.with_settings(own if isinstance(own, dict) else {}))
    return merged


def combine(bodies: list[Body]) -> Compound | None:
    """One shape for the part: the only body itself, so a one-body document
    exports exactly as before, or a compound of the bodies in order. Reference
    bodies are left out, because they are not part of what is made.
    """
    shapes = [body.shape for body in bodies if not body.reference]
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    return Compound(children=shapes)


@overload
def replay(
    document: ReplayDocument,
    cache_dir: Path | None = None,
    *,
    return_context: Literal[False] = False,
) -> Compound | None: ...


@overload
def replay(
    document: ReplayDocument,
    cache_dir: Path | None = None,
    *,
    return_context: Literal[True],
) -> tuple[Compound | None, Context]: ...


def replay(
    document: ReplayDocument,
    cache_dir: Path | None = None,
    *,
    return_context: bool = False,
) -> Compound | None | tuple[Compound | None, Context]:
    """Returns the shape after replaying features[:rollback_index], or None
    when that prefix is empty. rollback_index=0 is a valid state, the same
    as Fusion 360's timeline rolled back before the first feature - it means
    an empty document, not an error.

    With `return_context`, returns (shape, context): every feature's result,
    which is what selector synthesis needs to see how the part was built.
    """
    features = document["features"]
    rollback_index = document.get("rollback_index", len(features))
    cache = BrepCache(cache_dir) if cache_dir else None

    ctx = Context()
    upstream_key = "root"
    last_result: Compound | None = None

    for index, raw in enumerate(features[:rollback_index]):
        feature = raw
        op_type = feature.get("type")
        op = REGISTRY.get(op_type) if isinstance(op_type, str) else None
        if op is None:
            raise _tagged(
                ReplayError(
                    f"unknown op type {op_type!r} in feature {feature.get('id')!r}"
                ),
                feature,
                index,
            )

        try:
            jsonschema.validate(feature, op.schema)
        except jsonschema.ValidationError as exc:
            error = ReplayError(
                f"feature {feature.get('id')!r} failed validation: {exc.message}"
            )
            raise _tagged(error, feature, index) from exc

        feature_id = _text(feature.get("id")) or ""
        key = feature_cache_key(feature, upstream_key)
        # A sketch with projected references is read as its sources leave it
        # now. The key above is of what is stored: with the sources' own
        # features in `upstream_key` it fixes what this refresh gives.
        if op_type == "sketch" and has_references(feature):
            try:
                feature = refresh_references(feature, ctx)
            except Exception as exc:
                raise _tagged(exc, feature, index) from None
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

        ctx.results[feature_id] = result
        ctx.features[feature_id] = feature
        upstream_key = key
        last_result = result

    ctx.bodies = find_bodies(features[:rollback_index], ctx)
    # A document of sketches only has no body, and replay hands back the last sketch.
    part = combine(ctx.bodies) if ctx.bodies else last_result
    return (part, ctx) if return_context else part


def last_solid_index(features: list[JSONObject], rollback_index: int) -> int:
    """A sketch is a flat profile, not a solid (see ops/sketch.py) - it stays
    inert until something extrudes it. replay() only returns its last
    feature's result, so a bare sketch left at the tip would become the part
    and blank out whatever solid came before it. Walk back past any trailing
    sketch features to the last one that actually produced a solid.
    """
    index = rollback_index
    while index > 0 and features[index - 1].get("type") in NOT_SOLIDS:
        index -= 1
    return index


def build_shape(
    features: list[JSONObject], rollback_index: int, cache_dir: Path | None
) -> tuple[Compound | None, str | None, Context]:
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
        shape, ctx = replay(
            {"features": features, "rollback_index": export_index},
            cache_dir=cache_dir,
            return_context=True,
        )
        return (
            shape,
            _trailing_sketches(features, export_index, rollback_index, ctx),
            ctx,
        )
    except Exception as exc:
        failed = failed_feature(exc)
        if failed is None:
            raise
        good_index = last_solid_index(features, failed.index)
        shape, ctx = replay(
            {"features": features, "rollback_index": good_index},
            cache_dir=cache_dir,
            return_context=True,
        )
        _ = _trailing_sketches(features, good_index, failed.index, ctx)
        return shape, failed.feature_id, ctx


def _trailing_sketches(
    features: list[JSONObject], start: int, stop: int, ctx: Context
) -> str | None:
    """Puts the sketches between the last solid and `stop` into `ctx.features`.

    Replay stops at the last solid, because a sketch makes no part. But a sketch
    that projects an edge of another one still has to be read as its sources
    leave it now, and the viewer draws it from here. Returns the id of the first
    one that cannot be, as a failed feature.
    """
    for raw in features[start:stop]:
        feature = raw
        feature_id = _text(feature.get("id")) or ""
        if feature.get("type") in CONSTRUCT_TYPES:
            ctx.features[feature_id] = feature
            continue
        if feature.get("type") != "sketch":
            continue
        try:
            feature = refresh_references(feature, ctx)
        except Exception as exc:
            ctx.features[feature_id] = feature
            failed = failed_feature(exc)
            return (failed.feature_id if failed else None) or feature_id
        ctx.features[feature_id] = feature
    return None
