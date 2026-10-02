"""Solves a sketch's constraints with planegcs, FreeCAD's 2D solver.

This is the engine's copy of what viewer/sketch-solver.js does in the browser.
The viewer runs the same solver compiled to WebAssembly, so a drag stays fast
there, and the engine needs its own so a constraint added by an agent moves the
geometry without a viewer open. tests/test_solver_parity.py runs both on the
same sketches and fails if their answers drift apart.

A sketch is the document's own shape: {entities, constraints}. An entity is a
line (p0, p1), an arc (c, r, a0, a1) or a circle (c, r). A constraint names
entities or points as "e1" or "e1.p0". The names and the rules for each
constraint kind follow sketch-solver.js, so read the two side by side.

planegcs is an optional install (the `solver` extra): it has no macOS wheel,
so a Mac builds it from source. See the Makefile's test-solver target.
"""

import copy
import math

from planegcs import Sketch, SolveStatus

TWO_PI = 2 * math.pi
POINTS = {"line": ("p0", "p1"), "arc": ("c", "p0", "p1"), "circle": ("c",)}
ORIGIN = "origin"
# The sketch's own axes, as lines through the origin that a point can lie on.
AXES = {"axis_u": (1.0, 0.0), "axis_v": (0.0, 1.0)}
SOLVED = (SolveStatus.Success, SolveStatus.Converged)


def _entity(sketch: dict, entity_id: str) -> dict:
    for entity in sketch["entities"]:
        if entity["id"] == entity_id:
            return entity
    raise ValueError(f"no entity '{entity_id}'")


def point_of(sketch: dict, reference: str) -> list[float]:
    if reference == ORIGIN:
        return [0.0, 0.0]
    entity_id, name = reference.split(".")
    entity = _entity(sketch, entity_id)
    if entity["kind"] == "arc" and name != "c":
        angle = entity["a0"] if name == "p0" else entity["a1"]
        return [entity["c"][0] + entity["r"] * math.cos(angle), entity["c"][1] + entity["r"] * math.sin(angle)]
    return list(entity[name])


def _require(sketch: dict, entity_id: str, *kinds: str) -> dict:
    entity = _entity(sketch, entity_id)
    if entity["kind"] not in kinds:
        raise ValueError(f"expected {' or '.join(kinds)} but {entity_id} is a {entity['kind']}")
    return entity


def _dragged(sketch: dict, drag: dict) -> tuple[dict, list[str]]:
    """Moves a point, or a whole entity, to where the pointer is before solving.

    The solver then finds the nearest arrangement that still obeys the
    constraints, so a dragged corner of a dimensioned rectangle stays put in
    the direction a dimension forbids instead of tearing the sketch.
    """
    moved = copy.deepcopy(sketch)
    entity_id, _, name = drag["ref"].partition(".")
    entity = _entity(moved, entity_id)
    dx, dy = drag["delta"]

    def shift(uv):
        return [uv[0] + dx, uv[1] + dy]

    if not name:
        if entity["kind"] == "line":
            entity["p0"], entity["p1"] = shift(entity["p0"]), shift(entity["p1"])
            return moved, [f"{entity_id}.p0", f"{entity_id}.p1"]
        entity["c"] = shift(entity["c"])
        return moved, [f"{entity_id}.c"]
    if entity["kind"] == "line" or name == "c":
        entity[name] = shift(entity[name])
        return moved, [drag["ref"]]
    # Dragging an arc end swings it round the centre.
    x, y = shift(point_of(sketch, drag["ref"]))
    entity["a0" if name == "p0" else "a1"] = math.atan2(y - entity["c"][1], x - entity["c"][0])
    return moved, [drag["ref"]]


class _Build:
    """One planegcs system for one sketch, with a map from each of its
    constraint tags back to the index of the document constraint."""

    def __init__(self, sketch: dict, pinned: list[str] | None):
        self.sketch = sketch
        self.system = Sketch()
        self.points = {ORIGIN: self.system.add_fixed_point(0, 0)}
        self.shapes: dict[str, int] = {}
        self.radii: dict[str, int] = {}
        self.owner: dict[int, int] = {}
        self._add_geometry(pinned or [])
        for index, constraint in enumerate(sketch["constraints"]):
            for tag in self._constraint_tags(constraint):
                self.owner[tag] = index

    def _add_geometry(self, pinned: list[str]) -> None:
        system = self.system
        axis_ends = {name: system.add_fixed_point(*along) for name, along in AXES.items()}
        for entity in self.sketch["entities"]:
            for name in POINTS[entity["kind"]]:
                reference = f"{entity['id']}.{name}"
                x, y = point_of(self.sketch, reference)
                self.points[reference] = system.add_point(x, y)
                if reference in pinned:
                    system.fix_point(self.points[reference], x, y)
        for name, end in axis_ends.items():
            self.shapes[name] = system.add_line(self.points[ORIGIN], end)
        for entity in self.sketch["entities"]:
            entity_id = entity["id"]
            def at(name, entity_id=entity_id):
                return self.points[f"{entity_id}.{name}"]

            if entity["kind"] == "line":
                self.shapes[entity_id] = system.add_line(at("p0"), at("p1"))
                continue
            self.radii[entity_id] = system.add_param(entity["r"])
            if entity["kind"] == "circle":
                self.shapes[entity_id] = system.add_circle(at("c"), self.radii[entity_id])
                continue
            # planegcs runs an arc counter-clockwise from start to end and
            # wants the end angle above the start angle.
            end = entity["a1"]
            while end <= entity["a0"]:
                end += TWO_PI
            arc = system.add_arc(
                at("c"), at("p0"), at("p1"), self.radii[entity_id],
                system.add_param(entity["a0"]), system.add_param(end),
            )
            system.arc_rules(arc)
            self.shapes[entity_id] = arc

    def _curve_pair(self, constraint: dict, table: dict):
        a = _entity(self.sketch, constraint["a"])
        b = _entity(self.sketch, constraint["b"])
        key, flipped = (a["kind"], b["kind"]), (b["kind"], a["kind"])
        if key in table:
            return table[key](self.shapes[a["id"]], self.shapes[b["id"]])
        if flipped in table:
            return table[flipped](self.shapes[b["id"]], self.shapes[a["id"]])
        raise ValueError(f"{constraint['kind']} cannot join a {a['kind']} and a {b['kind']}")

    def _constraint_tags(self, constraint: dict) -> list[int]:
        s, kind = self.system, constraint["kind"]
        sketch, shapes, points = self.sketch, self.shapes, self.points
        if kind == "coincident":
            return [s.coincident(points[constraint["a"]], points[constraint["b"]])]
        if kind == "point_on":
            if constraint["b"] in AXES:
                return [s.point_on_line(points[constraint["a"]], shapes[constraint["b"]])]
            curve = _require(sketch, constraint["b"], "line", "circle", "arc")
            on = {"line": s.point_on_line, "circle": s.point_on_circle, "arc": s.point_on_arc}[curve["kind"]]
            return [on(points[constraint["a"]], shapes[curve["id"]])]
        if kind in ("horizontal", "vertical"):
            if "e" in constraint:
                _require(sketch, constraint["e"], "line")
                return [getattr(s, kind)(shapes[constraint["e"]])]
            return [getattr(s, kind + "_points")(points[constraint["a"]], points[constraint["b"]])]
        if kind in ("parallel", "perpendicular"):
            _require(sketch, constraint["a"], "line")
            _require(sketch, constraint["b"], "line")
            return [getattr(s, kind)(shapes[constraint["a"]], shapes[constraint["b"]])]
        if kind == "tangent":
            return [self._curve_pair(constraint, {
                ("line", "circle"): s.tangent_line_circle,
                ("line", "arc"): s.tangent_line_arc,
                ("circle", "circle"): s.tangent_circle_circle,
                ("circle", "arc"): s.tangent_circle_arc,
                ("arc", "arc"): s.tangent_arc_arc,
            })]
        if kind == "equal":
            return [self._curve_pair(constraint, {
                ("line", "line"): s.equal_length,
                ("circle", "circle"): s.equal_radius_cc,
                ("circle", "arc"): s.equal_radius_ca,
                ("arc", "arc"): s.equal_radius_aa,
            })]
        if kind == "fix":
            return self._fix(constraint)
        if kind == "distance":
            return [s.set_p2p_distance(points[constraint["a"]], points[constraint["b"]], constraint["value"])]
        if kind in ("radius", "diameter"):
            entity = _require(sketch, constraint["e"], "circle", "arc")
            setter = getattr(s, f"set_{entity['kind']}_{kind}")
            return [setter(shapes[entity["id"]], constraint["value"])]
        if kind == "angle":
            _require(sketch, constraint["a"], "line")
            _require(sketch, constraint["b"], "line")
            return [s.set_l2l_angle(shapes[constraint["a"]], shapes[constraint["b"]], math.radians(constraint["value"]))]
        raise ValueError(f"unknown constraint kind '{kind}'")

    def _fix(self, constraint: dict) -> list[int]:
        """A point is fixed where it is. A whole entity fixes all its points,
        and a whole circle holds its size too, as Fusion does."""
        entity_id = constraint["e"]
        if "." in entity_id:
            references, whole = [entity_id], None
        else:
            whole = _entity(self.sketch, entity_id)
            references = [f"{entity_id}.{name}" for name in POINTS[whole["kind"]]]
        tags = []
        for reference in references:
            x, y = point_of(self.sketch, reference)
            tags.extend(self.system.fix_point(self.points[reference], x, y))
        if whole and whole["kind"] == "circle":
            tags.append(self.system.set_circle_radius(self.shapes[entity_id], whole["r"]))
        return tags

    def indices(self, tags) -> list[int]:
        return sorted({self.owner[tag] for tag in tags if tag in self.owner})

    def moved(self) -> dict:
        """The sketch with every number the solver moved read back."""
        system, result = self.system, copy.deepcopy(self.sketch)
        for entity in result["entities"]:
            entity_id = entity["id"]
            if entity["kind"] == "line":
                entity["p0"] = list(system.get_point(self.points[entity_id + ".p0"]))
                entity["p1"] = list(system.get_point(self.points[entity_id + ".p1"]))
            elif entity["kind"] == "circle":
                circle = system.get_circle(self.shapes[entity_id])
                entity["c"], entity["r"] = list(circle.center), circle.radius
            else:
                arc = system.get_arc(self.shapes[entity_id])
                entity["c"], entity["r"] = list(arc.center), arc.radius
                entity["a0"], entity["a1"] = arc.start_angle, arc.end_angle
                while entity["a1"] <= entity["a0"]:
                    entity["a1"] += TWO_PI
        return result


def solve(sketch: dict, drag: dict | None = None) -> dict:
    """Solves `sketch`. Returns {ok, sketch, dof, conflicting, redundant}.

    `sketch` in the result is the moved copy when ok, and the input untouched
    when not. `conflicting` and `redundant` are indices into the sketch's
    constraints. `drag` is {"ref": "e1.p0", "delta": [du, dv]}: the point is
    held where the pointer is, and if that makes the sketch unsolvable it was
    dragged somewhere a dimension forbids, so the solve is retried with it free.
    """
    start, held = _dragged(sketch, drag) if drag else (sketch, None)
    for pinned in ([held, None] if held else [None]):
        build = _Build(start, pinned)
        status = build.system.solve()
        diagnosis = build.system.diagnose()
        conflicting = build.indices(diagnosis.conflicting)
        redundant = build.indices(diagnosis.redundant)
        if status in SOLVED and not conflicting:
            return {"ok": True, "sketch": build.moved(), "dof": diagnosis.dof, "conflicting": conflicting, "redundant": redundant}
        if not pinned:
            return {"ok": False, "sketch": sketch, "dof": None, "conflicting": conflicting, "redundant": redundant}
    raise AssertionError("unreachable")
