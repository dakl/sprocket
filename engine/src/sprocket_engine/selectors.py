"""Resolve the JSON selector chains from SPEC.md's "References and
topological naming" section into build123d shape lists.

Never reference geometry by index - OCCT renumbers faces/edges whenever
anything upstream changes. A selector is a query that re-resolves on every
replay instead of a stored position.
"""

from build123d import Axis, Edge, Face, ShapeList

AXES = {"X": Axis.X, "Y": Axis.Y, "Z": Axis.Z}

STARTERS = {"faces", "edges", "vertices"}

# The grammar, in one string, because both the op schemas and the agent-facing
# `get_schemas` reply need it and "see selectors.py" is no help to a caller
# that cannot read the file.
SELECTOR_DESCRIPTION = (
    "A query re-resolved on every rebuild, so it survives edits that renumber faces. "
    'Form: {"select": [<step>, ...]}. The first step starts the set and is one of '
    '{"op": "faces"}, {"op": "edges"} or {"op": "vertices"}. Later steps narrow it: '
    '{"op": "filter_by", "axis": "Z"} keeps items aligned with that axis, '
    '{"op": "sort_by", "axis": "Z"} orders them low to high, '
    '{"op": "slice", "start": 0, "stop": 4} takes a range, '
    '{"op": "first"} and {"op": "last"} take one end, and '
    '{"op": "nearest_to", "point": [x, y, z]} takes the one closest to a point, with an '
    'optional "normal" to break a tie across a thin wall, '
    '{"op": "at_end", "axis": "Z", "end": "max"} keeps everything level with the highest item along that '
    'axis ("min" for the lowest), which is how to say "the top edges" so a part that gains an edge there '
    "is noticed, "
    '{"op": "new_since", "feature": "f2"} keeps only what feature f2 made or changed, which is the most '
    'robust way to say "the edges of that extrude", and '
    '{"op": "nearest_each", "points": [[x, y, z], ...]} takes the item nearest each point, and '
    '{"op": "within", "box": [x0, y0, z0, x1, y1, z1]} keeps the items whose centre lies inside the box. '
    'Add "expected_count": n beside "select" and a rebuild that finds a different number fails '
    "the feature instead of rounding the wrong edges. "
    'Every edge is {"select": [{"op": "edges"}]}. '
    "resolve_selection turns picked handles into a selector."
)


class SelectorError(Exception):
    pass


def _nearest(candidates, point, normal=None):
    """Pick the item whose centre is closest to `point`. `normal` is a
    tie-breaker: an item whose normal points away from the given one is
    penalised, so a face pick near a shared edge doesn't jump to the wrong
    side of a thin part.
    """

    def score(item):
        center = item.center()
        dist = (
            (center.X - point[0]) ** 2
            + (center.Y - point[1]) ** 2
            + (center.Z - point[2]) ** 2
        ) ** 0.5
        if normal is not None:
            n = item.normal_at(center)
            dot = n.X * normal[0] + n.Y * normal[1] + n.Z * normal[2]
            if dot < 0.9:
                dist += 1e6
        return dist

    return min(candidates, key=score)


# How close two shapes' centres and sizes must be to count as the same one.
SAME_GEOMETRY = 1e-5
# How level two items must be along an axis for `at_end` to keep both.
LEVEL = 1e-6


def _size(item) -> float:
    if isinstance(item, Face):
        return item.area
    return getattr(item, "length", 0.0)


def _signature(item) -> tuple:
    centre = item.center()
    return (getattr(item, "geom_type", None), centre.X, centre.Y, centre.Z, _size(item))


def _unchanged(item, old_signatures) -> bool:
    """Whether `old_signatures` (from `_signature`) holds the same face or edge as `item`.

    By geometry, not identity. Each feature's result is cached on its own, so a
    shape that came from the cache shares no OCCT identity with one built in
    the same run, and an identity test would call everything new. The old
    side is measured once per `new_since`, not once per comparison: on the
    Orange Pi case that was 90,000 centre calls and most of a 4 s selector search.
    """
    kind, x, y, z, size = _signature(item)
    for old_kind, ox, oy, oz, old_size in old_signatures:
        if old_kind != kind or abs(old_size - size) > SAME_GEOMETRY:
            continue
        if ((ox - x) ** 2 + (oy - y) ** 2 + (oz - z) ** 2) ** 0.5 > SAME_GEOMETRY:
            continue
        return True
    return False


def _at_end(current, axis: str, end: str):
    """Everything level with the highest ("max") or lowest ("min") item along `axis`, by centre."""
    if end not in ("max", "min"):
        raise SelectorError(f"at_end 'end' must be 'max' or 'min', not {end!r}")
    if not current:
        return ShapeList(current)
    values = [getattr(item.center(), axis) for item in current]
    edge = max(values) if end == "max" else min(values)
    return ShapeList(
        [item for item, value in zip(current, values) if abs(value - edge) <= LEVEL]
    )


def _within(current, box):
    """Items whose centre lies in the axis-aligned box [x0, y0, z0, x1, y1, z1].

    The edges are padded by LEVEL, so a face centred exactly on a box wall
    counts as inside. A face on the boundary between two boxes can match both.
    """
    if len(box) != 6:
        raise SelectorError("within needs a box of six numbers: x0, y0, z0, x1, y1, z1")
    low, high = box[:3], box[3:]

    def inside(item):
        centre = item.center()
        return all(
            low[i] - LEVEL <= value <= high[i] + LEVEL
            for i, value in enumerate((centre.X, centre.Y, centre.Z))
        )

    return ShapeList([item for item in current if inside(item)])


def _new_since(current, starter: str, feature_id: str, ctx):
    """What feature `feature_id` made or changed: items of `current` that were
    not in the shape it was applied to. A feature with no base starts from
    nothing, so all of it is new.
    """
    feature = ctx.features.get(feature_id) if ctx is not None else None
    if feature is None:
        raise SelectorError(
            f"new_since names {feature_id!r}, which is not a feature before this one"
        )
    base_id = feature.get("base")
    if not base_id:
        return ShapeList(current)
    before = ctx.results.get(base_id)
    if before is None:
        raise SelectorError(
            f"new_since {feature_id!r}: its base {base_id!r} has no result"
        )
    olds = [_signature(old) for old in getattr(before, starter)()]
    # A ShapeList, so the steps after this one can still sort and filter.
    return ShapeList([item for item in current if not _unchanged(item, olds)])


def resolve_selector(shape, selector, ctx=None):
    """selector: {"from": "part", "select": [{"op": ..., ...}, ...], "expected_count": n}

    `ctx` is the replay context, which `new_since` needs to see earlier features.
    """
    steps = selector["select"]
    if not steps or steps[0]["op"] not in STARTERS:
        raise SelectorError(f"selector must start with one of {STARTERS}")

    starter = steps[0]["op"]
    current = getattr(shape, starter)()
    for step in steps[1:]:
        op = step["op"]
        if op == "filter_by":
            current = current.filter_by(AXES[step["axis"]])
        elif op == "sort_by":
            current = current.sort_by(AXES[step["axis"]])
        elif op == "slice":
            current = current[step.get("start") : step.get("stop")]
        elif op == "first":
            current = [current[0]]
        elif op == "last":
            current = [current[-1]]
        elif op == "nearest_to":
            current = [_nearest(current, step["point"], step.get("normal"))]
        elif op == "at_end":
            current = _at_end(current, step["axis"], step["end"])
        elif op == "new_since":
            current = _new_since(current, starter, step["feature"], ctx)
        elif op == "within":
            current = _within(current, step["box"])
        elif op == "nearest_each":
            picked = []
            for point in step["points"]:
                found = _nearest(current, point)
                if not any(found.is_same(other) for other in picked):
                    picked.append(found)
            current = ShapeList(picked)
        else:
            raise SelectorError(f"unknown selector op: {op}")

    expected = selector.get("expected_count")
    if expected is not None and len(current) != expected:
        raise SelectorError(
            f"selector matched {len(current)}, expected {expected}: the geometry it named has changed"
        )
    return list(current)


def resolve_edges(shape, selector, ctx=None):
    """Resolve a selector that has to land on edges, for fillet and chamfer.

    A selector that matches nothing is the topological-naming failure SPEC.md
    warns about: the op would quietly do nothing, and the part on screen looks
    like the edit was never made rather than like it broke. Fail here so the
    timeline can show the feature as failed.
    """
    matched = resolve_selector(shape, selector, ctx)
    if not matched:
        raise SelectorError("edge selector matched no edges")
    other_kinds = {
        type(item).__name__ for item in matched if not isinstance(item, Edge)
    }
    if other_kinds:
        raise SelectorError(
            f"edge selector matched {sorted(other_kinds)}, expected edges"
        )
    return matched
