"""Resolve the JSON selector chains from SPEC.md's "References and
topological naming" section into build123d shape lists.

Never reference geometry by index - OCCT renumbers faces/edges whenever
anything upstream changes. A selector is a query that re-resolves on every
replay instead of a stored position.
"""

from build123d import Axis

AXES = {"X": Axis.X, "Y": Axis.Y, "Z": Axis.Z}

STARTERS = {"faces", "edges", "vertices"}


class SelectorError(Exception):
    pass


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
        else:
            raise SelectorError(f"unknown selector op: {op}")

    return list(current)
