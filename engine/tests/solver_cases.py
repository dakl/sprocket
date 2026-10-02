"""Sketches that both solvers must answer the same way (test_solver_parity.py).

Each case is {sketch, drag}. A sketch that leaves freedom has many valid answers
and each solver picks its own, so only a fully constrained answer is compared
number for number. The rest are compared by their degrees of freedom and by
which constraints conflict or repeat each other.
"""

import math


def rough_rectangle(extra=()):
    return {
        "entities": [
            {"id": "e1", "kind": "line", "p0": [0, 0], "p1": [38, 3]},
            {"id": "e2", "kind": "line", "p0": [38, 3], "p1": [41, 22]},
            {"id": "e3", "kind": "line", "p0": [41, 22], "p1": [-2, 19]},
            {"id": "e4", "kind": "line", "p0": [-2, 19], "p1": [0, 0]},
        ],
        "constraints": [
            {"kind": "coincident", "a": "e1.p1", "b": "e2.p0"},
            {"kind": "coincident", "a": "e2.p1", "b": "e3.p0"},
            {"kind": "coincident", "a": "e3.p1", "b": "e4.p0"},
            {"kind": "coincident", "a": "e4.p1", "b": "e1.p0"},
            {"kind": "horizontal", "e": "e1"},
            {"kind": "horizontal", "e": "e3"},
            {"kind": "vertical", "e": "e2"},
            {"kind": "vertical", "e": "e4"},
            *extra,
        ],
    }


def sprocket_plate(teeth=13, pitch=6.35, roller=3.3, tip=15.0, cap_radius=0.55):
    """The logo plate: arcs and lines joined end to end, every corner a
    coincident constraint and every arc a radius."""
    seat_radius = roller / 2 + 0.05
    pitch_radius = pitch / (2 * math.sin(math.pi / teeth))
    step = math.tau / teeth
    arcs = []
    for k in range(teeth):
        angle = k * step
        middle = angle + step / 2
        arcs.append({"id": f"seat{k}", "kind": "arc", "c": [pitch_radius * math.cos(angle), pitch_radius * math.sin(angle)],
                     "r": seat_radius, "a0": angle + math.pi / 2 + 0.0873, "a1": angle + 1.5 * math.pi - 0.0873})
        arcs.append({"id": f"cap{k}", "kind": "arc", "c": [(tip - cap_radius) * math.cos(middle), (tip - cap_radius) * math.sin(middle)],
                     "r": cap_radius, "a0": middle - math.pi / 2, "a1": middle + math.pi / 2})

    def end(arc, which):
        angle = arc["a0"] if which == "p0" else arc["a1"]
        return [arc["c"][0] + arc["r"] * math.cos(angle), arc["c"][1] + arc["r"] * math.sin(angle)]

    lines, constraints = [], []
    for k in range(teeth):
        seat, cap, following = arcs[2 * k], arcs[2 * k + 1], arcs[2 * ((k + 1) % teeth)]
        lines.append({"id": f"up{k}", "kind": "line", "p0": end(seat, "p0"), "p1": end(cap, "p0")})
        lines.append({"id": f"dn{k}", "kind": "line", "p0": end(cap, "p1"), "p1": end(following, "p1")})
        constraints += [
            {"kind": "coincident", "a": f"seat{k}.p0", "b": f"up{k}.p0"},
            {"kind": "coincident", "a": f"up{k}.p1", "b": f"cap{k}.p0"},
            {"kind": "coincident", "a": f"cap{k}.p1", "b": f"dn{k}.p0"},
            {"kind": "coincident", "a": f"dn{k}.p1", "b": f"seat{(k + 1) % teeth}.p1"},
        ]
    constraints += [{"kind": "radius", "e": arc["id"], "value": arc["r"]} for arc in arcs]
    return {"entities": arcs + lines, "constraints": constraints}


# Two straight sides and two round ends. Every tangent, equal and distance kind
# the viewer offers for curves is used at least once across these cases.
SLOT = {
    "entities": [
        {"id": "l1", "kind": "line", "p0": [0, 0], "p1": [30, 0.5]},
        {"id": "l2", "kind": "line", "p0": [0, 10], "p1": [30, 9.5]},
        {"id": "a1", "kind": "arc", "c": [0, 5], "r": 5, "a0": math.pi / 2, "a1": 1.5 * math.pi},
        {"id": "a2", "kind": "arc", "c": [30, 5], "r": 5, "a0": -math.pi / 2, "a1": math.pi / 2},
    ],
    "constraints": [
        {"kind": "coincident", "a": "a1.p1", "b": "l1.p0"},
        {"kind": "coincident", "a": "a1.p0", "b": "l2.p0"},
        {"kind": "coincident", "a": "a2.p0", "b": "l1.p1"},
        {"kind": "coincident", "a": "a2.p1", "b": "l2.p1"},
        {"kind": "tangent", "a": "l1", "b": "a1"},
        {"kind": "tangent", "a": "l1", "b": "a2"},
        {"kind": "tangent", "a": "l2", "b": "a1"},
        {"kind": "tangent", "a": "l2", "b": "a2"},
        {"kind": "horizontal", "e": "l1"},
        {"kind": "radius", "e": "a1", "value": 6},
        {"kind": "equal", "a": "a1", "b": "a2"},
        {"kind": "distance", "a": "a1.c", "b": "a2.c", "value": 28},
        {"kind": "fix", "e": "a1.c"},
    ],
}

# Angles, axes, circles and every point-on and fix kind.
ANGLES_AND_CIRCLES = {
    "entities": [
        {"id": "l1", "kind": "line", "p0": [0, 0], "p1": [10, 1]},
        {"id": "l2", "kind": "line", "p0": [0, 0], "p1": [8, 9]},
        {"id": "c1", "kind": "circle", "c": [20, 4], "r": 3},
        {"id": "c2", "kind": "circle", "c": [30, 2], "r": 2},
        {"id": "c3", "kind": "circle", "c": [20, -9], "r": 4},
    ],
    "constraints": [
        {"kind": "coincident", "a": "l1.p0", "b": "l2.p0"},
        {"kind": "coincident", "a": "l1.p0", "b": "origin"},
        {"kind": "horizontal", "e": "l1"},
        {"kind": "angle", "a": "l1", "b": "l2", "value": 60},
        {"kind": "equal", "a": "l1", "b": "l2"},
        {"kind": "distance", "a": "l1.p0", "b": "l1.p1", "value": 10},
        {"kind": "diameter", "e": "c1", "value": 8},
        {"kind": "point_on", "a": "c1.c", "b": "axis_u"},
        {"kind": "distance", "a": "origin", "b": "c1.c", "value": 20},
        {"kind": "tangent", "a": "c1", "b": "c2"},
        {"kind": "radius", "e": "c2", "value": 2},
        {"kind": "point_on", "a": "c2.c", "b": "axis_u"},
        {"kind": "equal", "a": "c1", "b": "c3"},
        {"kind": "fix", "e": "c3"},
    ],
}

CASES = {
    "rectangle_dimensioned": {
        "sketch": rough_rectangle([
            {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40},
            {"kind": "distance", "a": "e2.p0", "b": "e2.p1", "value": 20},
            {"kind": "coincident", "a": "e1.p0", "b": "origin"},
        ]),
        "drag": {"ref": "e3.p0", "delta": [4, 3]},
    },
    "rectangle_over_constrained": {
        "sketch": rough_rectangle([
            {"kind": "distance", "a": "e1.p0", "b": "e1.p1", "value": 40},
            {"kind": "distance", "a": "e3.p0", "b": "e3.p1", "value": 30},
        ]),
        "drag": None,
    },
    "line_tangent_to_circle": {
        "sketch": {
            "entities": [{"id": "e1", "kind": "line", "p0": [-10, 6], "p1": [10, 5]}, {"id": "e2", "kind": "circle", "c": [0, 0], "r": 5}],
            "constraints": [{"kind": "tangent", "a": "e1", "b": "e2"}, {"kind": "radius", "e": "e2", "value": 5}, {"kind": "horizontal", "e": "e1"}],
        },
        "drag": {"ref": "e2.c", "delta": [2, -1]},
    },
    "slot": {"sketch": SLOT, "drag": {"ref": "l2.p1", "delta": [0, -0.5]}},
    "angles_and_circles": {"sketch": ANGLES_AND_CIRCLES, "drag": {"ref": "c2.c", "delta": [1, 0]}},
    "sprocket_plate_52_entities": {"sketch": sprocket_plate(), "drag": {"ref": "up3.p1", "delta": [0.4, 0.2]}},
}
