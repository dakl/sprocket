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
from collections.abc import Callable
from typing import Literal, NotRequired, TypedDict, final

from planegcs import (
    ArcId,
    CircleId,
    ConstraintTag,
    LineId,
    ParamId,
    PointId,
    Sketch,
    SolveStatus,
)

from sprocket_engine.model import (
    Constraint,
    Entity,
    EntityKind,
    entity_number,
    entity_point,
)

type PointName = Literal["p0", "p1", "c"]

TWO_PI = 2 * math.pi
POINTS: dict[EntityKind, tuple[PointName, ...]] = {
    "line": ("p0", "p1"),
    "arc": ("c", "p0", "p1"),
    "circle": ("c",),
}
ORIGIN = "origin"
# The sketch's own axes, as lines through the origin that a point can lie on.
AXES = {"axis_u": (1.0, 0.0), "axis_v": (0.0, 1.0)}
SOLVED = (SolveStatus.Success, SolveStatus.Converged)


@final
class SolverSketch(TypedDict):
    """What the solver reads and writes: a sketch's entities and constraints."""

    entities: list[Entity]
    constraints: list[Constraint]


@final
class Drag(TypedDict):
    """A point, or a whole entity, moved by `delta` before the solve."""

    ref: str
    delta: list[float]


@final
class SolveResult(TypedDict):
    """What `solve` found. `conflicting` and `redundant` index the constraints."""

    ok: bool
    sketch: SolverSketch
    dof: int | None
    conflicting: list[int]
    redundant: list[int]
    diagnose_ms: NotRequired[float]


def _entity(sketch: SolverSketch, entity_id: str) -> Entity:
    for entity in sketch["entities"]:
        if entity["id"] == entity_id:
            return entity
    raise ValueError(f"no entity '{entity_id}'")


def _point_name(name: str) -> PointName:
    if name == "p0":
        return "p0"
    if name == "p1":
        return "p1"
    if name == "c":
        return "c"
    raise ValueError(f"no point '{name}'")


def _field(constraint: Constraint, name: Literal["a", "b", "e"]) -> str:
    """The constraint's `name` reference, which its kind needs."""
    if name == "a":
        value = constraint.get("a")
    elif name == "b":
        value = constraint.get("b")
    else:
        value = constraint.get("e")
    if value is None:
        raise ValueError(f"constraint {constraint['kind']!r} needs {name!r}")
    return value


def _value(constraint: Constraint) -> float:
    value = constraint.get("value")
    if value is None:
        raise ValueError(f"constraint {constraint['kind']!r} needs 'value'")
    return value


def point_of(sketch: SolverSketch, reference: str) -> list[float]:
    """Return the [u, v] of a point reference such as "e1.p0" or "origin"."""
    if reference == ORIGIN:
        return [0.0, 0.0]
    entity_id, name = reference.split(".")
    entity = _entity(sketch, entity_id)
    if entity["kind"] == "arc" and name != "c":
        angle = entity_number(entity, "a0" if name == "p0" else "a1")
        centre, radius = entity_point(entity, "c"), entity_number(entity, "r")
        return [
            centre[0] + radius * math.cos(angle),
            centre[1] + radius * math.sin(angle),
        ]
    return list(entity_point(entity, _point_name(name)))


def _require(sketch: SolverSketch, entity_id: str, *kinds: str) -> Entity:
    entity = _entity(sketch, entity_id)
    if entity["kind"] not in kinds:
        raise ValueError(
            f"expected {' or '.join(kinds)} but {entity_id} is a {entity['kind']}"
        )
    return entity


def _set_point(entity: Entity, name: PointName, value: list[float]) -> None:
    if name == "p0":
        entity["p0"] = value
    elif name == "p1":
        entity["p1"] = value
    else:
        entity["c"] = value


def _dragged(sketch: SolverSketch, drag: Drag) -> tuple[SolverSketch, list[str]]:
    """Moves a point, or a whole entity, to where the pointer is before solving.

    The solver then finds the nearest arrangement that still obeys the
    constraints, so a dragged corner of a dimensioned rectangle stays put in
    the direction a dimension forbids instead of tearing the sketch.
    """
    moved = copy.deepcopy(sketch)
    entity_id, _, name = drag["ref"].partition(".")
    entity = _entity(moved, entity_id)
    dx, dy = drag["delta"]

    def shift(uv: list[float]) -> list[float]:
        return [uv[0] + dx, uv[1] + dy]

    if not name:
        if entity["kind"] == "line":
            entity["p0"] = shift(entity_point(entity, "p0"))
            entity["p1"] = shift(entity_point(entity, "p1"))
            return moved, [f"{entity_id}.p0", f"{entity_id}.p1"]
        entity["c"] = shift(entity_point(entity, "c"))
        return moved, [f"{entity_id}.c"]
    if entity["kind"] == "line" or name == "c":
        point = _point_name(name)
        _set_point(entity, point, shift(entity_point(entity, point)))
        return moved, [drag["ref"]]
    # Dragging an arc end swings it round the centre.
    x, y = shift(point_of(sketch, drag["ref"]))
    centre = entity_point(entity, "c")
    angle = math.atan2(y - centre[1], x - centre[0])
    if name == "p0":
        entity["a0"] = angle
    else:
        entity["a1"] = angle
    return moved, [drag["ref"]]


class _Build:
    """One planegcs system for one sketch, with a map from each of its
    constraint tags back to the index of the document constraint.
    """

    def __init__(self, sketch: SolverSketch, pinned: list[str] | None) -> None:
        self.sketch: SolverSketch = sketch
        self.system: Sketch = Sketch()
        self.points: dict[str, PointId] = {ORIGIN: self.system.add_fixed_point(0, 0)}
        # planegcs gives each kind of curve its own ids. The sketch's two axes
        # are lines, under their reference names.
        self.lines: dict[str, LineId] = {}
        self.circles: dict[str, CircleId] = {}
        self.arcs: dict[str, ArcId] = {}
        self.radii: dict[str, ParamId] = {}
        self.owner: dict[ConstraintTag, int] = {}
        self._add_geometry(pinned or [])
        for index, constraint in enumerate(sketch["constraints"]):
            for tag in self._constraint_tags(constraint):
                self.owner[tag] = index

    def _add_geometry(self, pinned: list[str]) -> None:
        system = self.system
        axis_ends = {
            name: system.add_fixed_point(*along) for name, along in AXES.items()
        }
        for entity in self.sketch["entities"]:
            for name in POINTS[entity["kind"]]:
                reference = f"{entity['id']}.{name}"
                x, y = point_of(self.sketch, reference)
                # A projected reference is locked where its source put it.
                if entity.get("reference"):
                    self.points[reference] = system.add_fixed_point(x, y)
                    continue
                self.points[reference] = system.add_point(x, y)
                if reference in pinned:
                    _ = system.fix_point(self.points[reference], x, y)
        for name, end in axis_ends.items():
            self.lines[name] = system.add_line(self.points[ORIGIN], end)
        for entity in self.sketch["entities"]:
            entity_id = entity["id"]

            def at(name: PointName, entity_id: str = entity_id) -> PointId:
                return self.points[f"{entity_id}.{name}"]

            if entity["kind"] == "line":
                self.lines[entity_id] = system.add_line(at("p0"), at("p1"))
                continue
            radius = entity_number(entity, "r")
            self.radii[entity_id] = system.add_param(radius)
            if entity["kind"] == "circle":
                self.circles[entity_id] = system.add_circle(
                    at("c"), self.radii[entity_id]
                )
                if entity.get("reference"):
                    _ = system.set_circle_radius(self.circles[entity_id], radius)
                continue
            # planegcs runs an arc counter-clockwise from start to end and
            # wants the end angle above the start angle.
            start = entity_number(entity, "a0")
            end = entity_number(entity, "a1")
            while end <= start:
                end += TWO_PI
            # add_arc adds the arc rules itself. A second arc_rules call makes
            # every arc redundant, and the diagnosis then runs a slow search.
            self.arcs[entity_id] = system.add_arc(
                at("c"),
                at("p0"),
                at("p1"),
                self.radii[entity_id],
                system.add_param(start),
                system.add_param(end),
            )

    def _tangent(self, a: Entity, b: Entity) -> ConstraintTag | None:
        s, kinds = self.system, (a["kind"], b["kind"])
        if kinds == ("line", "circle"):
            return s.tangent_line_circle(self.lines[a["id"]], self.circles[b["id"]])
        if kinds == ("line", "arc"):
            return s.tangent_line_arc(self.lines[a["id"]], self.arcs[b["id"]])
        if kinds == ("circle", "circle"):
            return s.tangent_circle_circle(self.circles[a["id"]], self.circles[b["id"]])
        if kinds == ("circle", "arc"):
            return s.tangent_circle_arc(self.circles[a["id"]], self.arcs[b["id"]])
        if kinds == ("arc", "arc"):
            return s.tangent_arc_arc(self.arcs[a["id"]], self.arcs[b["id"]])
        return None

    def _equal(self, a: Entity, b: Entity) -> ConstraintTag | None:
        s, kinds = self.system, (a["kind"], b["kind"])
        if kinds == ("line", "line"):
            return s.equal_length(self.lines[a["id"]], self.lines[b["id"]])
        if kinds == ("circle", "circle"):
            return s.equal_radius_cc(self.circles[a["id"]], self.circles[b["id"]])
        if kinds == ("circle", "arc"):
            return s.equal_radius_ca(self.circles[a["id"]], self.arcs[b["id"]])
        if kinds == ("arc", "arc"):
            return s.equal_radius_aa(self.arcs[a["id"]], self.arcs[b["id"]])
        return None

    def _curve_pair(
        self,
        constraint: Constraint,
        join: Callable[[Entity, Entity], ConstraintTag | None],
    ) -> ConstraintTag:
        """`join` on the constraint's two curves, in either order."""
        a = _entity(self.sketch, _field(constraint, "a"))
        b = _entity(self.sketch, _field(constraint, "b"))
        tag = join(a, b)
        if tag is None:
            tag = join(b, a)
        if tag is None:
            raise ValueError(
                f"{constraint['kind']} cannot join a {a['kind']} and a {b['kind']}"
            )
        return tag

    def _line(self, entity_id: str) -> LineId:
        _ = _require(self.sketch, entity_id, "line")
        return self.lines[entity_id]

    def _constraint_tags(self, constraint: Constraint) -> list[ConstraintTag]:
        s, kind = self.system, constraint["kind"]
        sketch, points = self.sketch, self.points

        def a() -> str:
            return _field(constraint, "a")

        def b() -> str:
            return _field(constraint, "b")

        def e() -> str:
            return _field(constraint, "e")

        if kind == "coincident":
            return [s.coincident(points[a()], points[b()])]
        if kind == "point_on":
            if b() in AXES:
                return [s.point_on_line(points[a()], self.lines[b()])]
            curve = _require(sketch, b(), "line", "circle", "arc")
            if curve["kind"] == "line":
                return [s.point_on_line(points[a()], self.lines[curve["id"]])]
            if curve["kind"] == "circle":
                return [s.point_on_circle(points[a()], self.circles[curve["id"]])]
            return [s.point_on_arc(points[a()], self.arcs[curve["id"]])]
        if kind in ("horizontal", "vertical"):
            if "e" in constraint:
                line = self._line(e())
                return [
                    s.horizontal(line) if kind == "horizontal" else s.vertical(line)
                ]
            first, second = points[a()], points[b()]
            return [
                s.horizontal_points(first, second)
                if kind == "horizontal"
                else s.vertical_points(first, second)
            ]
        if kind in ("parallel", "perpendicular"):
            first, second = self._line(a()), self._line(b())
            return [
                s.parallel(first, second)
                if kind == "parallel"
                else s.perpendicular(first, second)
            ]
        if kind == "tangent":
            return [self._curve_pair(constraint, self._tangent)]
        if kind == "equal":
            return [self._curve_pair(constraint, self._equal)]
        if kind == "fix":
            return self._fix(constraint)
        if kind == "symmetric":
            line = self._line(e())
            return [s.symmetric_line(points[a()], points[b()], line)]
        if kind == "distance":
            return [s.set_p2p_distance(points[a()], points[b()], _value(constraint))]
        if kind in ("distance_x", "distance_y"):
            axis = 0 if kind == "distance_x" else 1
            first = s.get_point_param_ids(points[a()])[axis]
            second = s.get_point_param_ids(points[b()])[axis]
            return [s.difference(first, second, s.add_fixed_param(_value(constraint)))]
        if kind in ("radius", "diameter"):
            entity = _require(sketch, e(), "circle", "arc")
            value = _value(constraint)
            if entity["kind"] == "circle":
                circle = self.circles[entity["id"]]
                return [
                    s.set_circle_radius(circle, value)
                    if kind == "radius"
                    else s.set_circle_diameter(circle, value)
                ]
            arc = self.arcs[entity["id"]]
            return [
                s.set_arc_radius(arc, value)
                if kind == "radius"
                else s.set_arc_diameter(arc, value)
            ]
        if kind == "angle":
            first, second = self._line(a()), self._line(b())
            return [s.set_l2l_angle(first, second, math.radians(_value(constraint)))]
        raise ValueError(f"unknown constraint kind '{kind}'")

    def _fix(self, constraint: Constraint) -> list[ConstraintTag]:
        """A point is fixed where it is. A whole entity fixes all its points,
        and a whole circle holds its size too, as Fusion does.
        """
        entity_id = _field(constraint, "e")
        whole: Entity | None
        if "." in entity_id:
            references, whole = [entity_id], None
        else:
            whole = _entity(self.sketch, entity_id)
            references = [f"{entity_id}.{name}" for name in POINTS[whole["kind"]]]
        tags: list[ConstraintTag] = []
        for reference in references:
            x, y = point_of(self.sketch, reference)
            tags.extend(self.system.fix_point(self.points[reference], x, y))
        if whole and whole["kind"] == "circle":
            tags.append(
                self.system.set_circle_radius(
                    self.circles[entity_id], entity_number(whole, "r")
                )
            )
        return tags

    def indices(self, tags: list[ConstraintTag]) -> list[int]:
        """Return the document constraint indices behind planegcs constraint tags."""
        return sorted({self.owner[tag] for tag in tags if tag in self.owner})

    def moved(self) -> SolverSketch:
        """The sketch with every number the solver moved read back."""
        system, result = self.system, copy.deepcopy(self.sketch)
        for entity in result["entities"]:
            entity_id = entity["id"]
            if entity["kind"] == "line":
                entity["p0"] = list(system.get_point(self.points[entity_id + ".p0"]))
                entity["p1"] = list(system.get_point(self.points[entity_id + ".p1"]))
            elif entity["kind"] == "circle":
                circle = system.get_circle(self.circles[entity_id])
                entity["c"], entity["r"] = list(circle.center), circle.radius
            else:
                arc = system.get_arc(self.arcs[entity_id])
                entity["c"], entity["r"] = list(arc.center), arc.radius
                start, end = arc.start_angle, arc.end_angle
                while end <= start:
                    end += TWO_PI
                entity["a0"], entity["a1"] = start, end
        return result


def solve(sketch: SolverSketch, drag: Drag | None = None) -> SolveResult:
    """Solves `sketch`. Returns {ok, sketch, dof, conflicting, redundant}.

    `sketch` in the result is the moved copy when ok, and the input untouched
    when not. `conflicting` and `redundant` are indices into the sketch's
    constraints. `drag` is {"ref": "e1.p0", "delta": [du, dv]}: the point is
    held where the pointer is, and if that makes the sketch unsolvable it was
    dragged somewhere a dimension forbids, so the solve is retried with it free.
    """
    start, held = _dragged(sketch, drag) if drag else (sketch, None)
    for pinned in [held, None] if held else [None]:
        build = _Build(start, pinned)
        status = build.system.solve()
        diagnosis = build.system.diagnose()
        conflicting = build.indices(diagnosis.conflicting)
        redundant = build.indices(diagnosis.redundant)
        if status in SOLVED and not conflicting:
            return {
                "ok": True,
                "sketch": build.moved(),
                "dof": diagnosis.dof,
                "conflicting": conflicting,
                "redundant": redundant,
            }
        if not pinned:
            return {
                "ok": False,
                "sketch": sketch,
                "dof": None,
                "conflicting": conflicting,
                "redundant": redundant,
            }
    raise AssertionError("unreachable")
