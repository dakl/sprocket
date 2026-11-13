"""Pick -> selector: the shortest selector chain that selects exactly what was picked.

A viewer click names a face or edge by position in the built shape, and OCCT
renumbers those whenever anything upstream changes. A selector is a query that
re-resolves on every rebuild, so a fillet saved with one survives a change of
dimension (SPEC.md, "References and topological naming").

The search is small on purpose. It tries, in order of cost, the chains a person
would write by hand: everything a feature made (`new_since`), narrowed by an
axis, and by taking what is level with one end of an axis (`at_end`). A chain
that slices a sorted list by count is left out on purpose: it always returns
the same number of items, so `expected_count` cannot notice it picking the
wrong ones.

Each candidate is run, and only one that selects exactly the picked set is
kept. If none does, it falls back to the nearest item to each picked point,
which works but moves with the geometry. `strategy` says which kind was found.
"""

from .selectors import AXES, SelectorError, resolve_selector

STARTER_OF = {"f": "faces", "e": "edges"}


def parse_handles(handles: list[str], shape) -> tuple[str, list]:
    """The starter ("faces" or "edges") and the items for `handles`, all of one kind."""
    if not handles:
        raise SelectorError("resolve_selection needs at least one handle")
    kinds = {handle[0] for handle in handles}
    if not kinds <= set(STARTER_OF) or len(kinds) != 1:
        raise SelectorError(
            "resolve_selection takes faces or edges, not a mix: ['f0', 'f2'] or ['e1', 'e5']"
        )
    starter = STARTER_OF[kinds.pop()]
    universe = getattr(shape, starter)()
    for handle in handles:
        if not 0 <= int(handle[1:]) < len(universe):
            raise SelectorError(
                f"handle {handle!r} is out of range, this shape has {len(universe)}"
            )
    return starter, universe


def _index_of(item, universe) -> int | None:
    for index, other in enumerate(universe):
        if other.is_same(item):
            return index
    return None


def _indices(items, universe) -> set[int]:
    return {index for item in items if (index := _index_of(item, universe)) is not None}


def synthesize(shape, ctx, handles: list[str], feature_ids: list[str]) -> dict:
    """Returns {selector, strategy, steps, count}. `feature_ids` are the
    features `new_since` may name, and `ctx` is the replay context they are in.
    """
    starter, universe = parse_handles(handles, shape)
    target = {int(handle[1:]) for handle in handles}

    def resolved(steps):
        try:
            return resolve_selector(shape, {"select": steps}, ctx)
        except Exception:
            return None

    def exact(steps) -> bool:
        items = resolved(steps)
        return (
            items is not None
            and _indices(items, universe) == target
            and len(items) == len(target)
        )

    found = []  # (rank, strategy, steps)

    def keep(steps, strategy):
        # A chain that starts from what a feature made follows that feature
        # when its dimensions change, so it is preferred, and then the shortest.
        found.append(
            ((0 if strategy == "new_since" else 1, len(steps)), strategy, steps)
        )

    prefixes = [([], "structure")] + [
        ([{"op": "new_since", "feature": feature_id}], "new_since")
        for feature_id in feature_ids
    ]
    for prefix, strategy in prefixes:
        for narrowing in [[]] + [[{"op": "filter_by", "axis": axis}] for axis in AXES]:
            steps = [{"op": starter}, *prefix, *narrowing]
            current = resolved(steps)
            if not current:
                continue
            if exact(steps):
                keep(steps, strategy)
            # Everything level with the highest or lowest item. It follows the
            # geometry: a part that gains an edge up there changes the count,
            # which `expected_count` then catches. A slice by count never would.
            for axis in AXES:
                for end in ("max", "min"):
                    candidate = [*steps, {"op": "at_end", "axis": axis, "end": end}]
                    if exact(candidate):
                        keep(candidate, strategy)

    if found:
        _, strategy, steps = min(found, key=lambda entry: entry[0])
        return _answer(steps, strategy, len(target))

    # Nothing structural picks exactly this set. Name it by where it is.
    centres = [
        [
            round(coordinate, 6)
            for coordinate in (
                universe[index].center().X,
                universe[index].center().Y,
                universe[index].center().Z,
            )
        ]
        for index in sorted(target)
    ]
    steps = [{"op": starter}, {"op": "nearest_each", "points": centres}]
    if len(target) == 1 and starter == "faces":
        # One flat face keeps the normal tie-break nearest_to has, so a pick next
        # to a thin wall cannot land on the other side of it.
        face = universe[next(iter(target))]
        if face.geom_type.name == "PLANE":
            normal = face.normal_at(face.center())
            steps = [
                {"op": starter},
                {
                    "op": "nearest_to",
                    "point": centres[0],
                    "normal": [
                        round(normal.X, 6),
                        round(normal.Y, 6),
                        round(normal.Z, 6),
                    ],
                },
            ]
    if not exact(steps):
        raise SelectorError(
            "no selector picks exactly those, because some of them are the same distance from each other"
        )
    return _answer(steps, "position", len(target))


def _answer(steps: list[dict], strategy: str, count: int) -> dict:
    return {
        "selector": {"from": "part", "select": steps, "expected_count": count},
        "strategy": strategy,
        "steps": len(steps),
        "count": count,
    }
