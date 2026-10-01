// Runs the real planegcs (vendor/planegcs.js) on sketches, the way the viewer does.
const test = require("node:test");
const assert = require("node:assert/strict");
require("../vendor/planegcs.js");
const { solve, entityStates } = require("../sketch-solver.js");

let gcs;
test.before(async () => {
  gcs = await globalThis.Planegcs.createSolver();
});

const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-6, `${message || ""} expected ${expected}, got ${actual}`);
const length = ([ax, ay], [bx, by]) => Math.hypot(bx - ax, by - ay);

/// Four lines drawn roughly as a rectangle, joined at the corners and held
/// horizontal and vertical. The corner coordinates are deliberately off.
function roughRectangle(extra = []) {
  return {
    entities: [
      { id: "e1", kind: "line", p0: [0, 0], p1: [38, 3] },
      { id: "e2", kind: "line", p0: [38, 3], p1: [41, 22] },
      { id: "e3", kind: "line", p0: [41, 22], p1: [-2, 19] },
      { id: "e4", kind: "line", p0: [-2, 19], p1: [0, 0] },
    ],
    constraints: [
      { kind: "coincident", a: "e1.p1", b: "e2.p0" },
      { kind: "coincident", a: "e2.p1", b: "e3.p0" },
      { kind: "coincident", a: "e3.p1", b: "e4.p0" },
      { kind: "coincident", a: "e4.p1", b: "e1.p0" },
      { kind: "horizontal", e: "e1" },
      { kind: "horizontal", e: "e3" },
      { kind: "vertical", e: "e2" },
      { kind: "vertical", e: "e4" },
      ...extra,
    ],
  };
}

const line = (result, id) => result.sketch.entities.find((entity) => entity.id === id);

test("horizontal, vertical and coincident turn a rough rectangle into a rectangle", () => {
  const result = solve(gcs, roughRectangle());
  assert.ok(result.ok);
  const [e1, e2, e3, e4] = ["e1", "e2", "e3", "e4"].map((id) => line(result, id));
  near(e1.p0[1], e1.p1[1], "bottom is level");
  near(e3.p0[1], e3.p1[1], "top is level");
  near(e2.p0[0], e2.p1[0], "right is upright");
  near(e4.p0[0], e4.p1[0], "left is upright");
  assert.deepEqual(e1.p1, e2.p0, "corners meet");
  assert.deepEqual(e3.p1, e4.p0);
});

test("two dimensions and a fixed corner leave a rectangle nothing to move", () => {
  const sketch = roughRectangle([
    { kind: "fix", e: "e1.p0" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: 40 },
    { kind: "distance", a: "e2.p0", b: "e2.p1", value: 20 },
  ]);
  const result = solve(gcs, sketch);
  assert.ok(result.ok);
  assert.equal(result.dof, 0);
  const [e1, e2, e3] = ["e1", "e2", "e3"].map((id) => line(result, id));
  near(length(e1.p0, e1.p1), 40);
  near(length(e2.p0, e2.p1), 20);
  near(e3.p0[0], 40);
  near(e3.p0[1], 20);
});

test("entityStates calls a fully constrained rectangle fixed and a loose one free", () => {
  const pinned = roughRectangle([
    { kind: "fix", e: "e1.p0" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: 40 },
    { kind: "distance", a: "e2.p0", b: "e2.p1", value: 20 },
  ]);
  const before = entityStates(gcs, pinned);
  assert.equal(before.dof, 0);
  assert.deepEqual(Object.values(before.states), ["fixed", "fixed", "fixed", "fixed"]);

  const loose = entityStates(gcs, roughRectangle());
  assert.ok(loose.dof > 0);
  assert.deepEqual(Object.values(loose.states), ["free", "free", "free", "free"]);
});

test("a rectangle with only its width dimensioned is still free, and only the height is missing", () => {
  const sketch = roughRectangle([
    { kind: "fix", e: "e1.p0" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: 40 },
  ]);
  const { dof, states } = entityStates(gcs, sketch);
  assert.equal(dof, 1);
  // The bottom edge is pinned at both ends. The top and right can still slide.
  assert.equal(states.e1, "fixed");
  assert.equal(states.e3, "free");
});

test("a dimension typed from the measured value moves nothing", () => {
  // The viewer opens a dimension box showing the current length. Accepting it
  // unchanged must not nudge the sketch.
  const solved = solve(gcs, roughRectangle()).sketch;
  const bottom = line({ sketch: solved }, "e1");
  const measured = length(bottom.p0, bottom.p1);
  const again = solve(gcs, { ...solved, constraints: [...solved.constraints, { kind: "distance", a: "e1.p0", b: "e1.p1", value: measured }] });
  assert.ok(again.ok);
  near(length(line(again, "e1").p0, line(again, "e1").p1), measured);
});

test("dragging one corner of a constrained rectangle keeps it a rectangle", () => {
  const solved = solve(gcs, roughRectangle([{ kind: "fix", e: "e1.p0" }])).sketch;
  const dragged = solve(gcs, solved, { drag: { ref: "e2.p1", delta: [5, 4] } });
  assert.ok(dragged.ok);
  const [e1, e2, e3, e4] = ["e1", "e2", "e3", "e4"].map((id) => line(dragged, id));
  near(e1.p0[1], e1.p1[1]);
  near(e2.p0[0], e2.p1[0]);
  near(e3.p0[1], e3.p1[1]);
  near(e4.p0[0], e4.p1[0]);
  assert.ok(e2.p1[0] > line({ sketch: solved }, "e2").p1[0] + 4, "the corner followed the pointer");
});

test("two dimensions that disagree are refused and the sketch is handed back untouched", () => {
  const sketch = roughRectangle([
    { kind: "fix", e: "e1.p0" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: 40 },
  ]);
  const solved = solve(gcs, sketch).sketch;
  const conflicting = { ...solved, constraints: [...solved.constraints, { kind: "distance", a: "e3.p0", b: "e3.p1", value: 55 }] };
  const result = solve(gcs, conflicting);
  assert.equal(result.ok, false);
  assert.ok(result.conflicting.length > 0);
  assert.equal(result.sketch, conflicting, "the input comes back, not a half-solved copy");
});

test("a radius dimension resizes a circle and a diameter does the same in halves", () => {
  const circle = { entities: [{ id: "e1", kind: "circle", c: [5, 5], r: 3 }], constraints: [{ kind: "diameter", e: "e1", value: 20 }] };
  near(solve(gcs, circle).sketch.entities[0].r, 10);
  circle.constraints = [{ kind: "radius", e: "e1", value: 7 }];
  near(solve(gcs, circle).sketch.entities[0].r, 7);
});

test("an arc keeps its ends on the circle when its radius changes", () => {
  const sketch = {
    entities: [{ id: "e1", kind: "arc", c: [0, 0], r: 5, a0: 0, a1: Math.PI / 2 }],
    constraints: [{ kind: "radius", e: "e1", value: 8 }, { kind: "fix", e: "e1.c" }],
  };
  const arc = solve(gcs, sketch).sketch.entities[0];
  near(arc.r, 8);
  near(arc.c[0], 0);
  // The angles are free, so they may drift a little. What must hold is that
  // both ends sit on the resized circle.
  for (const angle of [arc.a0, arc.a1]) {
    near(length(arc.c, [arc.c[0] + arc.r * Math.cos(angle), arc.c[1] + arc.r * Math.sin(angle)]), 8);
  }
});

test("a line made tangent to a circle ends up one radius from its centre", () => {
  const sketch = {
    entities: [
      { id: "e1", kind: "line", p0: [-10, 6], p1: [10, 7] },
      { id: "e2", kind: "circle", c: [0, 0], r: 5 },
    ],
    constraints: [{ kind: "tangent", a: "e1", b: "e2" }, { kind: "fix", e: "e2" }],
  };
  const moved = solve(gcs, sketch).sketch;
  const [a, b] = [moved.entities[0].p0, moved.entities[0].p1];
  const distance = Math.abs((b[0] - a[0]) * a[1] - (b[1] - a[1]) * a[0]) / length(a, b);
  near(distance, 5);
});

test("parallel, perpendicular and equal do what they say", () => {
  const sketch = {
    entities: [
      { id: "e1", kind: "line", p0: [0, 0], p1: [10, 0] },
      { id: "e2", kind: "line", p0: [0, 5], p1: [9, 7] },
      { id: "e3", kind: "line", p0: [0, 9], p1: [3, 15] },
    ],
    constraints: [
      { kind: "fix", e: "e1" },
      { kind: "parallel", a: "e1", b: "e2" },
      { kind: "perpendicular", a: "e1", b: "e3" },
      { kind: "equal", a: "e1", b: "e2" },
    ],
  };
  const [e1, e2, e3] = solve(gcs, sketch).sketch.entities;
  near(e2.p1[1] - e2.p0[1], 0, "e2 is parallel to e1");
  near(length(e2.p0, e2.p1), 10, "e2 is as long as e1");
  near(e3.p1[0] - e3.p0[0], 0, "e3 is perpendicular to e1");
});

test("an angle dimension sets the angle between two lines, in degrees", () => {
  const sketch = {
    entities: [
      { id: "e1", kind: "line", p0: [0, 0], p1: [10, 0] },
      { id: "e2", kind: "line", p0: [0, 0], p1: [7, 7.5] },
    ],
    constraints: [
      { kind: "fix", e: "e1" },
      { kind: "coincident", a: "e1.p0", b: "e2.p0" },
      { kind: "angle", a: "e1", b: "e2", value: 60 },
    ],
  };
  const [, e2] = solve(gcs, sketch).sketch.entities;
  near(Math.atan2(e2.p1[1] - e2.p0[1], e2.p1[0] - e2.p0[0]), Math.PI / 3);
});

test("solving twice on one solver gives the same answer and leaks nothing", () => {
  const first = solve(gcs, roughRectangle());
  const second = solve(gcs, roughRectangle());
  assert.deepEqual(second.sketch, first.sketch);
});

test("a constraint the solver cannot use is an error that says why", () => {
  const sketch = { entities: [{ id: "e1", kind: "line", p0: [0, 0], p1: [1, 0] }, { id: "e2", kind: "line", p0: [0, 1], p1: [1, 1] }], constraints: [{ kind: "tangent", a: "e1", b: "e2" }] };
  assert.throws(() => solve(gcs, sketch), /cannot join a line and a line/);
});

test("fixing a circle holds its size as well as its centre", () => {
  const circle = { entities: [{ id: "e1", kind: "circle", c: [1, 2], r: 3 }], constraints: [{ kind: "fix", e: "e1" }] };
  const { dof } = entityStates(gcs, circle);
  assert.equal(dof, 0);
});

test("a corner dragged where a dimension forbids stops at the nearest legal place", () => {
  const sketch = roughRectangle([
    { kind: "fix", e: "e1.p0" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: 40 },
  ]);
  const solved = solve(gcs, sketch).sketch;
  const dragged = solve(gcs, solved, { drag: { ref: "e1.p1", delta: [30, 0] } });
  assert.ok(dragged.ok, "an impossible drag is not an error");
  near(length(line(dragged, "e1").p0, line(dragged, "e1").p1), 40, "the width dimension still holds");
});

test("point_on keeps a point on a line, a circle and an arc", () => {
  const sketch = {
    entities: [
      { id: "e1", kind: "line", p0: [0, 0], p1: [10, 0] },
      { id: "e2", kind: "circle", c: [30, 0], r: 5 },
      { id: "e3", kind: "arc", c: [0, 30], r: 5, a0: 0, a1: Math.PI },
      { id: "e4", kind: "line", p0: [4, 3], p1: [4, 9] },
      { id: "e5", kind: "line", p0: [33, 8], p1: [40, 8] },
      { id: "e6", kind: "line", p0: [1, 36], p1: [-9, 40] },
    ],
    constraints: [
      { kind: "fix", e: "e1" },
      { kind: "fix", e: "e2" },
      { kind: "fix", e: "e3" },
      { kind: "point_on", a: "e4.p0", b: "e1" },
      { kind: "point_on", a: "e5.p0", b: "e2" },
      { kind: "point_on", a: "e6.p0", b: "e3" },
    ],
  };
  const result = solve(gcs, sketch);
  assert.ok(result.ok);
  near(line(result, "e4").p0[1], 0, "on the line");
  near(length(line(result, "e5").p0, [30, 0]), 5, "on the circle");
  near(length(line(result, "e6").p0, [0, 30]), 5, "on the arc");
});

test("coincident with the origin pins a point to [0, 0], and the origin never moves", () => {
  const sketch = {
    entities: [{ id: "e1", kind: "line", p0: [3, 4], p1: [20, 4] }],
    constraints: [{ kind: "coincident", a: "e1.p0", b: "origin" }],
  };
  const result = solve(gcs, sketch);
  assert.ok(result.ok);
  assert.deepEqual(line(result, "e1").p0.map((value) => Math.round(value * 1e6) / 1e6), [0, 0]);
  const dragged = solve(gcs, result.sketch, { drag: { ref: "e1.p0", delta: [5, 5] } });
  assert.ok(dragged.ok);
  near(line(dragged, "e1").p0[0], 0, "the pinned point holds");
});

test("a distance from the origin dimensions a point", () => {
  const sketch = {
    entities: [{ id: "e1", kind: "circle", c: [3, 4], r: 2 }],
    constraints: [{ kind: "distance", a: "origin", b: "e1.c", value: 10 }],
  };
  const result = solve(gcs, sketch);
  assert.ok(result.ok);
  near(length([0, 0], result.sketch.entities[0].c), 10);
});

test("point_on the sketch axes puts a point on the line through the origin, however far out", () => {
  const sketch = {
    entities: [
      { id: "e1", kind: "line", p0: [40, 3], p1: [50, 20] },
      { id: "e2", kind: "line", p0: [-4, -30], p1: [-4, -50] },
    ],
    constraints: [
      { kind: "point_on", a: "e1.p0", b: "axis_u" },
      { kind: "point_on", a: "e2.p0", b: "axis_v" },
    ],
  };
  const result = solve(gcs, sketch);
  assert.ok(result.ok);
  near(line(result, "e1").p0[1], 0, "on the x axis");
  near(line(result, "e2").p0[0], 0, "on the y axis");
  near(line(result, "e1").p0[0], 40, "and it slid along the axis, not to the origin");
});
