"""Resolve the JSON selector chains from SPEC.md's "References and
topological naming" section into build123d shape lists.

Never reference geometry by index - OCCT renumbers faces/edges whenever
anything upstream changes. A selector is a query that re-resolves on every
replay instead of a stored position.
"""

from build123d import Axis, Edge

AXES = {"X": Axis.X, "Y": Axis.Y, "Z": Axis.Z}

STARTERS = {"faces", "edges", "vertices"}


class SelectorError(Exception):
    pass


def _nearest(candidates, point, normal=None):
    """Pick the item whose centre is closest to `point`. `normal` is a
    tie-breaker: an item whose normal points away from the given one is
    penalised, so a face pick near a shared edge doesn't jump to the wrong
    side of a thin part."""

    def score(item):
        center = item.center()
        dist = ((center.X - point[0]) ** 2 + (center.Y - point[1]) ** 2 + (center.Z - point[2]) ** 2) ** 0.5
        if normal is not None:
            n = item.normal_at(center)
            dot = n.X * normal[0] + n.Y * normal[1] + n.Z * normal[2]
            if dot < 0.9:
                dist += 1e6
        return dist

    return min(candidates, key=score)


def resolve_selector(shape, selector):
    """selector: {"from": "part", "select": [{"op": ..., ...}, ...]}"""
    steps = selector["select"]
    if not steps or steps[0]["op"] not in STARTERS:
        raise SelectorError(f"selector must start with one of {STARTERS}")

    current = getattr(shape, steps[0]["op"])()
    for step in steps[1:]:
        op = step["op"]
        if op == "filter_by":
            current = current.filter_by(AXES[step["axis"]])
        elif op == "sort_by":
            current = current.sort_by(AXES[step["axis"]])
        elif op == "slice":
            current = current[step.get("start"):step.get("stop")]
        elif op == "first":
            current = [current[0]]
        elif op == "last":
            current = [current[-1]]
        elif op == "nearest_to":
            current = [_nearest(current, step["point"], step.get("normal"))]
        else:
            raise SelectorError(f"unknown selector op: {op}")

    return list(current)


def resolve_edges(shape, selector):
    """Resolve a selector that has to land on edges, for fillet and chamfer.

    A selector that matches nothing is the topological-naming failure SPEC.md
    warns about: the op would quietly do nothing, and the part on screen looks
    like the edit was never made rather than like it broke. Fail here so the
    timeline can show the feature as failed.
    """
    matched = resolve_selector(shape, selector)
    if not matched:
        raise SelectorError("edge selector matched no edges")
    other_kinds = {type(item).__name__ for item in matched if not isinstance(item, Edge)}
    if other_kinds:
        raise SelectorError(f"edge selector matched {sorted(other_kinds)}, expected edges")
    return matched
