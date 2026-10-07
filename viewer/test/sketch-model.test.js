const test = require("node:test");
const assert = require("node:assert/strict");
require("../vendor/planegcs.js");
const model = require("../sketch-model.js");
const { solve } = require("../sketch-solver.js");

let gcs;
test.before(async () => {
  gcs = await globalThis.Planegcs.createSolver();
});

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `expected ${expected}, got ${actual}`);

test("ids count on from the highest in use and are never reused after a delete", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [1, 0]);
  sketch = model.addLine(sketch, [1, 0], [1, 1]).sketch;
  assert.deepEqual(sketch.entities.map((entity) => entity.id), ["e1", "e2"]);
  const gap = model.deleteEntities(sketch, ["e1"]);
  assert.equal(model.nextId(gap), "e3");
});

test("a rectangle is four joined, level and upright lines that solve to a rectangle", () => {
  const { sketch, ids } = model.addRectangle(model.emptySketch(), [0, 0], [40, 20]);
  assert.deepEqual(ids, ["e1", "e2", "e3", "e4"]);
  assert.equal(sketch.constraints.filter((constraint) => constraint.kind === "coincident").length, 4);
  assert.equal(sketch.constraints.filter((constraint) => constraint.kind === "horizontal").length, 2);
  assert.equal(sketch.constraints.filter((constraint) => constraint.kind === "vertical").length, 2);
  const solved = solve(gcs, sketch);
  assert.ok(solved.ok);
  assert.equal(solved.conflicting.length, 0);
  assert.equal(solved.redundant.length, 0, "no constraint the rectangle does not need");
});

test("a flat rectangle is refused", () => {
  assert.equal(model.addRectangle(model.emptySketch(), [0, 0], [10, 0]), null);
});

test("an arc through three points runs the way that passes the middle one", () => {
  // Upper half of the unit circle, given start-to-end left to right: that is
  // clockwise, so the counter-clockwise arc starts at the right-hand end.
  const arc = model.arcThrough([-1, 0], [1, 0], [0, 1]);
  near(arc.r, 1);
  near(arc.c[0], 0);
  near(arc.a0, 0);
  near(arc.a1, Math.PI);
  // The same points given right to left are already counter-clockwise.
  const other = model.arcThrough([1, 0], [-1, 0], [0, 1]);
  near(other.a0, 0);
  near(other.a1, Math.PI);
});

test("three points in a line make no arc", () => {
  assert.equal(model.arcThrough([0, 0], [1, 1], [2, 2]), null);
  assert.equal(model.addArc(model.emptySketch(), [0, 0], [1, 1], [2, 2]), null);
});

test("snapTarget finds the nearest point in range and can skip one", () => {
  const { sketch } = model.addLine(model.emptySketch(), [0, 0], [10, 0]);
  assert.equal(model.snapTarget(sketch, [9.5, 0.2], 1).reference, "e1.p1");
  assert.equal(model.snapTarget(sketch, [5, 5], 1), null);
  assert.equal(model.snapTarget(sketch, [0.1, 0], 1, ["e1.p0"]).reference, "origin", "the origin sits under the line's start");
  assert.equal(model.snapTarget(sketch, [0.1, 0], 1, ["e1.p0", "origin"]), null);
});

test("hit prefers a point to the entity it sits on, and finds circles and arcs by their curve", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [10, 0]);
  sketch = model.addCircle(sketch, [20, 0], 5).sketch;
  sketch = model.addArc(sketch, [-1, 0], [1, 0], [0, 1]).sketch; // e3
  assert.deepEqual(model.hit(sketch, [0.1, 0.1], 0.5), { kind: "point", reference: "e1.p0" });
  assert.deepEqual(model.hit(sketch, [5, 0.2], 0.5), { kind: "entity", reference: "e1", gap: model.hit(sketch, [5, 0.2], 0.5).gap });
  assert.equal(model.hit(sketch, [20, 5.2], 0.5).reference, "e2");
  assert.deepEqual(model.hit(sketch, [20, 0], 0.5), { kind: "point", reference: "e2.c" }, "the middle of a circle is its centre point");
  assert.equal(model.hit(sketch, [22, 0], 0.5), null, "the space inside a circle is not the circle");
  assert.equal(model.hit(sketch, [0, 1.1], 0.5).reference, "e3");
  assert.equal(model.hit(sketch, [0, -1.1], 0.5), null, "the missing half of an arc is not the arc");
});

test("alignment snaps near-level and near-upright lines only", () => {
  assert.equal(model.alignment([0, 0], [10, 0.3], 3), "horizontal");
  assert.equal(model.alignment([0, 0], [0.3, 10], 3), "vertical");
  assert.equal(model.alignment([0, 0], [10, 3], 3), null);
  assert.equal(model.alignment([0, 0], [0, 0], 3), null);
});

test("constraintFor builds each constraint from the right selection and says what is wrong otherwise", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [10, 0]);
  sketch = model.addLine(sketch, [0, 5], [10, 6]).sketch;
  sketch = model.addCircle(sketch, [30, 0], 4).sketch;
  assert.deepEqual(model.constraintFor(sketch, "horizontal", ["e1"]), { kind: "horizontal", e: "e1" });
  assert.deepEqual(model.constraintFor(sketch, "coincident", ["e1.p1", "e2.p0"]), { kind: "coincident", a: "e1.p1", b: "e2.p0" });
  assert.deepEqual(model.constraintFor(sketch, "parallel", ["e1", "e2"]), { kind: "parallel", a: "e1", b: "e2" });
  assert.deepEqual(model.constraintFor(sketch, "tangent", ["e1", "e3"]), { kind: "tangent", a: "e1", b: "e3" });
  assert.deepEqual(model.constraintFor(sketch, "fix", ["e3"]), { kind: "fix", e: "e3" });
  assert.throws(() => model.constraintFor(sketch, "coincident", ["e1"]), /two points/);
  assert.throws(() => model.constraintFor(sketch, "parallel", ["e1", "e3"]), /two lines/);
  assert.throws(() => model.constraintFor(sketch, "equal", ["e1", "e3"]), /two lines, or two circles/);
  assert.throws(() => model.constraintFor(sketch, "tangent", ["e1", "e2"]), /circle or arc/);
});

test("a suggested dimension carries the measured value, so accepting it moves nothing", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [3, 4]);
  sketch = model.addLine(sketch, [0, 0], [-4, 3]).sketch;
  sketch = model.addCircle(sketch, [20, 0], 4).sketch;
  near(model.suggestDimension(sketch, ["e1"]).constraint.value, 5);
  near(model.suggestDimension(sketch, ["e3"]).constraint.value, 8);
  assert.equal(model.suggestDimension(sketch, ["e3"]).constraint.kind, "diameter");
  near(model.suggestDimension(sketch, ["e1.p0", "e1.p1"]).constraint.value, 5);
  assert.equal(model.suggestDimension(sketch, ["e1", "e3"]), null);
  assert.equal(model.suggestDimension(sketch, []), null);

  // Every kind, applied unchanged, leaves the geometry where it was.
  const constraints = [["e1"], ["e3"], ["e1.p0", "e2.p1"], ["e1", "e2"]].map((selection) => model.suggestDimension(sketch, selection).constraint);
  const solved = solve(gcs, { ...sketch, constraints: [{ kind: "fix", e: "e1" }, { kind: "coincident", a: "e1.p0", b: "e2.p0" }, ...constraints.filter((constraint) => constraint.kind !== "distance" || constraint.a !== "e1.p0" || constraint.b !== "e2.p1")] });
  assert.ok(solved.ok);
  near(solved.sketch.entities[1].p1[0], -4);
  near(solved.sketch.entities[1].p1[1], 3);
});

test("the angle between two lines is measured the way the solver measures it", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [10, 0]);
  sketch = model.addLine(sketch, [0, 0], [0, 10]).sketch;
  near(model.suggestDimension(sketch, ["e1", "e2"]).constraint.value, 90);
  near(model.suggestDimension(sketch, ["e2", "e1"]).constraint.value, -90);
});

test("deleting an entity takes every constraint that mentions it", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [4, 4]);
  const smaller = model.deleteEntities(sketch, ["e1"]);
  assert.equal(smaller.entities.length, 3);
  assert.ok(smaller.constraints.every((constraint) => JSON.stringify(constraint).indexOf('"e1') === -1));
  assert.equal(smaller.constraints.length, sketch.constraints.length - 3, "the coincident on each of e1's ends, and its horizontal");
});

test("adding the same constraint twice keeps one, but two dimensions with different values both stay", () => {
  let { sketch } = model.addLine(model.emptySketch(), [0, 0], [10, 0]);
  const fix = { kind: "fix", e: "e1.p0" };
  sketch = model.withConstraint(model.withConstraint(sketch, fix), fix);
  assert.equal(sketch.constraints.length, 1);
  const width = { kind: "distance", a: "e1.p0", b: "e1.p1" };
  sketch = model.withConstraint(model.withConstraint(sketch, { ...width, value: 10 }), { ...width, value: 12 });
  assert.equal(sketch.constraints.length, 3);
});

test("toFeature copies, so later edits to the sketch do not reach the feature", () => {
  const { sketch } = model.addLine(model.emptySketch(), [0, 0], [1, 0]);
  const feature = model.toFeature("XY", sketch);
  sketch.entities[0].p1[0] = 99;
  assert.equal(feature.entities[0].p1[0], 1);
  assert.equal(feature.plane, "XY");
});

test("snap prefers a point, then a spot on a curve, then the raw position", () => {
  let { sketch } = model.addLine(model.emptySketch(), [10, 0], [30, 0]);
  sketch = model.addCircle(sketch, [20, 20], 5).sketch; // e2
  const radius = 0.5;
  assert.deepEqual(model.snap(sketch, [10.2, 0.1], radius), { uv: [10, 0], reference: "e1.p0", curve: null });
  assert.deepEqual(model.snap(sketch, [0.2, -0.1], radius), { uv: [0, 0], reference: "origin", curve: null }, "the origin is a point");
  assert.deepEqual(model.snap(sketch, [20, 20.1], radius), { uv: [20, 20], reference: "e2.c", curve: null });
  const onLine = model.snap(sketch, [20, 0.3], radius);
  assert.deepEqual([onLine.uv, onLine.reference, onLine.curve], [[20, 0], null, "e1"]);
  const onCircle = model.snap(sketch, [25.2, 20.1], radius);
  assert.equal(onCircle.curve, "e2");
  assert.ok(Math.abs(Math.hypot(onCircle.uv[0] - 20, onCircle.uv[1] - 20) - 5) < 1e-9, "the snapped spot is on the circle");
  assert.deepEqual(model.snap(sketch, [50, 50], radius), { uv: [50, 50], reference: null, curve: null });
});

test("a curve snap stops at the end of an arc instead of reaching the missing half", () => {
  const { sketch } = model.addArc(model.emptySketch(), [-5, 0], [5, 0], [0, 5]); // the upper half
  assert.equal(model.snap(sketch, [0, 5.2], 0.5).curve, "e1");
  assert.equal(model.snap(sketch, [0, -5.2], 0.5).curve, null, "the lower half is not drawn");
});

test("joining a click to what it snapped to adds coincident for a point and point_on for a curve", () => {
  let { sketch } = model.addLine(model.emptySketch(), [10, 0], [30, 0]);
  sketch = model.addCircle(sketch, [20, 20], 5).sketch;
  const joined = model.join(sketch, { reference: "origin", curve: null }, "e2.c");
  assert.deepEqual(joined.constraints, [{ kind: "coincident", a: "origin", b: "e2.c", id: "c1" }]);
  const onCurve = model.join(sketch, { reference: null, curve: "e1" }, "e2.c");
  assert.deepEqual(onCurve.constraints, [{ kind: "point_on", a: "e2.c", b: "e1", id: "c1" }]);
  assert.deepEqual(model.join(sketch, { reference: null, curve: null }, "e2.c").constraints, []);
});

test("point_on takes a point and a curve in either order, and the origin takes a coincident or a dimension but not a fix", () => {
  let { sketch } = model.addLine(model.emptySketch(), [10, 0], [30, 0]);
  sketch = model.addCircle(sketch, [20, 20], 5).sketch;
  assert.deepEqual(model.constraintFor(sketch, "point_on", ["e2.c", "e1"]), { kind: "point_on", a: "e2.c", b: "e1" });
  assert.deepEqual(model.constraintFor(sketch, "point_on", ["e1", "e2.c"]), { kind: "point_on", a: "e2.c", b: "e1" });
  assert.throws(() => model.constraintFor(sketch, "point_on", ["e1.p0", "e1"]), /Select a point and a line/, "a line's own end is on it already");
  assert.throws(() => model.constraintFor(sketch, "point_on", ["e1", "e2"]), /Select a point/);
  assert.throws(() => model.constraintFor(sketch, "point_on", ["e1.p0"]), /Select a point/);
  assert.deepEqual(model.constraintFor(sketch, "coincident", ["origin", "e2.c"]), { kind: "coincident", a: "origin", b: "e2.c" });
  assert.throws(() => model.constraintFor(sketch, "fix", ["origin"]), /already fixed/);
  assert.equal(model.suggestDimension(sketch, ["origin", "e2.c"]).constraint.value, Math.hypot(20, 20));
});

test("deleting an entity leaves a constraint on the origin that names no deleted entity alone", () => {
  let { sketch } = model.addLine(model.emptySketch(), [10, 0], [30, 0]);
  sketch = model.addCircle(sketch, [20, 20], 5).sketch;
  sketch = model.withConstraint(sketch, { kind: "coincident", a: "origin", b: "e2.c" });
  assert.equal(model.deleteEntities(sketch, ["e1"]).constraints.length, 1);
  assert.equal(model.deleteEntities(sketch, ["e2"]).constraints.length, 0);
});

test("snap reaches a shown axis, only when told it is shown, and a curve wins when it is nearer", () => {
  const axes = [{ ref: "axis_u", name: "X axis" }, { ref: "axis_v", name: "Y axis" }];
  const { sketch } = model.addLine(model.emptySketch(), [10, 5], [30, 5]);
  const onX = model.snap(sketch, [50, 0.3], 0.5, axes);
  assert.deepEqual([onX.uv, onX.curve, onX.label], [[50, 0], "axis_u", "On X axis"]);
  const onY = model.snap(sketch, [-0.3, 40], 0.5, axes);
  assert.deepEqual([onY.uv, onY.curve, onY.label], [[0, 40], "axis_v", "On Y axis"]);
  assert.equal(model.snap(sketch, [50, 0.3], 0.5).curve, null, "an axis that is hidden is not there");
  assert.equal(model.snap(sketch, [20, 5.1], 0.5, axes).curve, "e1");
  const crowded = model.snap(sketch, [20, 4.9], 5, [{ ref: "axis_u", name: "X axis" }]);
  assert.equal(crowded.curve, "e1", "the line is 0.1 away and the axis 4.9");
  assert.deepEqual(model.join(sketch, onX, "e1.p1").constraints, [{ kind: "point_on", a: "e1.p1", b: "axis_u", id: "c1" }]);
});

test("constraints get ids, and a deleted constraint's id does not come back on another", () => {
  let { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [10, 5]);
  assert.deepEqual(sketch.constraints.map((constraint) => constraint.id), ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]);
  sketch = model.removeConstraintById(sketch, "c8");
  sketch = model.withConstraint(sketch, { kind: "fix", e: "e1.p0" });
  assert.equal(sketch.constraints.at(-1).id, "c8");
  sketch = model.removeConstraintById(sketch, "c3");
  sketch = model.withConstraint(sketch, { kind: "fix", e: "e2.p0" });
  assert.equal(sketch.constraints.at(-1).id, "c9");
});

test("a document from before ids gets them without changing the order", () => {
  const old = { entities: [], constraints: [{ kind: "fix", e: "e1.p0" }, { kind: "fix", e: "e2.p0" }] };
  assert.deepEqual(model.withConstraintIds(old).constraints.map((constraint) => constraint.id), ["c1", "c2"]);
});

test("replacing a dimension keeps its id", () => {
  let sketch = model.withConstraint({ entities: [], constraints: [] }, { kind: "radius", e: "e1", value: 5 });
  sketch = model.replaceConstraint(sketch, 0, { kind: "radius", e: "e1", value: 7 });
  assert.deepEqual(sketch.constraints, [{ kind: "radius", e: "e1", value: 7, id: "c1" }]);
});

test("a constraint names the entities it acts on, and not the origin", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [10, 5]);
  assert.deepEqual(model.constraintEntities(sketch, { kind: "coincident", a: "e1.p1", b: "e2.p0" }), ["e1", "e2"]);
  assert.deepEqual(model.constraintEntities(sketch, { kind: "coincident", a: "e1.p0", b: "origin" }), ["e1"]);
});

test("a constraint between two shapes has a glyph beside each", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [10, 5]);
  assert.deepEqual(model.glyphAnchors(sketch, { kind: "parallel", a: "e1", b: "e3" }), [[5, 0], [5, 5]]);
  assert.deepEqual(model.glyphAnchors(sketch, { kind: "horizontal", e: "e1" }), [[5, 0]]);
});

// --- Projected references, orientation and construction -----------------------

/// The user's case: an inner rectangle, and the outer one's left edge projected beside it.
function innerWithProjectedEdge() {
  let { sketch } = model.addRectangle(model.emptySketch(), [10, 10], [70, 40]);
  const added = model.addReference(sketch, { sketch: "f1", entity: "e4" }, { kind: "line", p0: [0, 60], p1: [0, 0] });
  return { sketch: added.sketch, reference: added.id };
}

test("a projected edge is a locked construction entity that remembers its source", () => {
  const { sketch, reference } = innerWithProjectedEdge();
  const entity = sketch.entities.find((item) => item.id === reference);
  assert.equal(entity.construction, true);
  assert.deepEqual(entity.reference, { sketch: "f1", entity: "e4" });
  assert.equal(model.addReference(sketch, { sketch: "f1", entity: "e4" }, entity), null, "the same edge twice adds nothing");
});

test("two upright lines are dimensioned across, edge to edge, with the lower one first", () => {
  const { sketch, reference } = innerWithProjectedEdge();
  // e4 is the inner left edge, at u = 10. The reference is the line u = 0.
  const suggestion = model.suggestDimension(sketch, ["e4", reference]);
  assert.equal(suggestion.constraint.kind, "distance_x");
  assert.equal(suggestion.constraint.a, reference + ".p0", "the reference is the left one, so it comes first");
  assert.equal(suggestion.constraint.b, "e4.p0");
  near(suggestion.constraint.value, 10);
  // Picked the other way round gives the same dimension.
  assert.deepEqual(model.suggestDimension(sketch, [reference, "e4"]).constraint, suggestion.constraint);
});

test("the gap to a projected edge holds after the edge moves, which is what a rebuild does to it", () => {
  const { sketch, reference } = innerWithProjectedEdge();
  const dimensioned = model.withConstraint(sketch, model.suggestDimension(sketch, ["e4", reference]).constraint);
  // Pin the width so the rectangle keeps its size, as a drawn one has.
  const sized = model.withConstraint(dimensioned, { kind: "distance", a: "e1.p0", b: "e1.p1", value: 60 });
  const first = solve(gcs, sized);
  assert.ok(first.ok);
  // The outer sketch widens, so its left edge is now at u = -40: the engine re-projects it and sends the sketch back.
  const moved = JSON.parse(JSON.stringify(first.sketch));
  const edge = moved.entities.find((entity) => entity.id === reference);
  edge.p0 = [-40, 60];
  edge.p1 = [-40, 0];
  const again = solve(gcs, moved);
  assert.ok(again.ok);
  const left = again.sketch.entities.find((entity) => entity.id === "e4");
  near(left.p0[0], -30, "the inner left edge stays 10 right of the outer one");
  near(again.sketch.entities.find((entity) => entity.id === "e2").p0[0], 30, "and the rectangle keeps its width");
});

test("two level lines are dimensioned up the page, and two points take the orientation they are asked for", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [30, 12]);
  // e1 is the bottom edge and e3 the top.
  const up = model.suggestDimension(sketch, ["e1", "e3"]);
  assert.equal(up.constraint.kind, "distance_y");
  near(up.constraint.value, 12);
  const points = ["e1.p0", "e3.p0"];
  assert.equal(model.suggestDimension(sketch, points).constraint.kind, "distance");
  const across = model.suggestDimension(sketch, ["e1.p0", "e2.p1"], "horizontal");
  assert.deepEqual([across.constraint.kind, across.constraint.a, across.constraint.b], ["distance_x", "e1.p0", "e2.p1"]);
  near(across.constraint.value, 30);
  const down = model.suggestDimension(sketch, ["e3.p0", "e1.p0"], "vertical");
  assert.deepEqual([down.constraint.a, down.constraint.b], ["e1.p0", "e3.p0"], "lower first, so the value is positive");
  near(down.constraint.value, 12);
});

test("a dimension label for a distance along an axis sits between its two points", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [30, 12]);
  const anchor = model.dimensionAnchor(sketch, { kind: "distance_x", a: "e1.p0", b: "e1.p1", value: 30 });
  assert.deepEqual(anchor, [15, 0]);
});

test("construction flips on the entities picked, and a projected reference is left alone", () => {
  const { sketch, reference } = innerWithProjectedEdge();
  const made = model.toggleConstruction(sketch, ["e1", "e2"]);
  assert.deepEqual(made.entities.filter((entity) => entity.construction && !entity.reference).map((entity) => entity.id), ["e1", "e2"]);
  const half = model.toggleConstruction(made, ["e1", "e3"]);
  assert.ok(half.entities.find((entity) => entity.id === "e1").construction && half.entities.find((entity) => entity.id === "e3").construction, "a mixed pick makes all construction");
  const undone = model.toggleConstruction(made, ["e1", "e2"]);
  assert.equal(undone.entities.some((entity) => "construction" in entity && !entity.reference), false);
  assert.equal(model.toggleConstruction(sketch, [reference]), null);
  assert.equal(model.toggleConstruction(sketch, []), null);
});

// --- Rounding a corner ------------------------------------------------------------

test("a corner of two lines is rounded with an arc that touches both and has the radius asked for", () => {
  // A square corner at the origin: one line up the v axis, one along the u axis.
  let sketch = model.addLine(model.emptySketch(), [0, 30], [0, 0]).sketch;
  sketch = model.addLine(sketch, [0, 0], [40, 0]).sketch;
  sketch = model.connect(sketch, "e1.p1", "e2.p0");
  const rounded = model.addFillet(sketch, "e1.p1", 5);
  const solved = solve(gcs, rounded.sketch);
  assert.ok(solved.ok, "the arc, two tangents and a radius leave a sketch that solves");
  const [up, along, arc] = ["e1", "e2", rounded.id].map((id) => solved.sketch.entities.find((entity) => entity.id === id));
  // A quarter circle of radius 5 in a square corner has its centre 5 in from both lines and touches them 5 from the corner.
  near(arc.c[0], 5);
  near(arc.c[1], 5);
  near(arc.r, 5);
  near(up.p1[1], 5, "the upright line stops 5 short of the corner");
  near(along.p0[0], 5, "and so does the level one");
  near(up.p1[0], 0);
  near(along.p0[1], 0);
  // A quarter turn, counter-clockwise from the touch point on the level line to the one on the upright line.
  near(arc.a1 - arc.a0, Math.PI / 2);
  assert.equal(rounded.sketch.constraints.some((constraint) => constraint.kind === "coincident" && constraint.a === "e1.p1" && constraint.b === "e2.p0"), false, "the old corner joint is gone");
});

test("rounding works for a corner at any angle, and the arc ends where the tangent length says", () => {
  // 60 degrees between the lines. The tangent length is radius / tan(30 degrees).
  const apex = [10, 10];
  const second = [10 + 50 * Math.cos(Math.PI / 3), 10 + 50 * Math.sin(Math.PI / 3)];
  let sketch = model.addLine(model.emptySketch(), apex, [60, 10]).sketch;
  sketch = model.addLine(sketch, apex, second).sketch;
  sketch = model.connect(sketch, "e1.p0", "e2.p0");
  const rounded = model.addFillet(sketch, "e1.p0", 4);
  const reach = 4 / Math.tan(Math.PI / 6);
  near(reach, rounded.reach);
  const solved = solve(gcs, rounded.sketch);
  assert.ok(solved.ok);
  const [first, other] = ["e1", "e2"].map((id) => solved.sketch.entities.find((entity) => entity.id === id));
  near(Math.hypot(first.p0[0] - apex[0], first.p0[1] - apex[1]), reach);
  near(Math.hypot(other.p0[0] - apex[0], other.p0[1] - apex[1]), reach);
});

test("a corner that cannot be rounded says why", () => {
  let sketch = model.addLine(model.emptySketch(), [0, 3], [0, 0]).sketch;
  sketch = model.addLine(sketch, [0, 0], [40, 0]).sketch;
  sketch = model.connect(sketch, "e1.p1", "e2.p0");
  assert.throws(() => model.addFillet(sketch, "e1.p1", 5), /too big/);
  assert.throws(() => model.addFillet(sketch, "e1.p0", 1), /exactly two lines/);
  assert.throws(() => model.addFillet(sketch, "e1.p1", 0), /above zero/);
  assert.equal(model.suggestFilletRadius(sketch, "e1.p0"), null);
  assert.equal(model.suggestFilletRadius(sketch, "e1.p1"), 0.75, "a quarter of the shorter line, 3");
});

test("rounding a corner that sits on the origin lets the lines come off it", () => {
  let sketch = model.addLine(model.emptySketch(), [0, 30], [0, 0]).sketch;
  sketch = model.addLine(sketch, [0, 0], [40, 0]).sketch;
  sketch = model.connect(sketch, "e1.p1", "origin");
  sketch = model.connect(sketch, "e2.p0", "origin");
  const rounded = model.addFillet(sketch, "e1.p1", 5);
  assert.ok(solve(gcs, rounded.sketch).ok);
  assert.equal(rounded.sketch.constraints.some((constraint) => constraint.kind === "coincident" && [constraint.a, constraint.b].includes("origin")), false);
});

// --- Mirror ---------------------------------------------------------------------------

/// A slanted line, a circle and an arc to the right of a vertical mirror line at u = 0.
function mirrorFixture() {
  let sketch = model.addLine(model.emptySketch(), [0, -20], [0, 20]).sketch; // e1: the mirror line
  sketch = model.addLine(sketch, [5, 0], [15, 10]).sketch; // e2
  sketch = model.addCircle(sketch, [20, -5], 3).sketch; // e3
  sketch = model.addArc(sketch, [10, -10], [14, -14], [14, -10]).sketch; // e4, a quarter-ish arc
  return sketch;
}

test("mirroring across a vertical line puts each copy at its reflection, and ties it to the original", () => {
  const sketch = mirrorFixture();
  const done = model.mirrorEntities(sketch, ["e2", "e3", "e4"], "e1");
  assert.deepEqual(done.ids, ["e5", "e6", "e7"]);
  const solved = solve(gcs, done.sketch);
  assert.ok(solved.ok, "the symmetric constraints agree with the geometry they were made from");
  const by = (id) => solved.sketch.entities.find((entity) => entity.id === id);
  // Reflection across u = 0 negates u. Checked from the numbers, not from the function.
  assert.deepEqual([by("e5").p0.map((v) => Math.round(v * 1e6) / 1e6), by("e5").p1.map((v) => Math.round(v * 1e6) / 1e6)], [[-5, 0], [-15, 10]]);
  near(by("e6").c[0], -20);
  near(by("e6").c[1], -5);
  near(by("e6").r, 3);
  const original = solver_endpoints(by("e4"));
  const copy = solver_endpoints(by("e7"));
  // The copy's start is the reflection of the original's end, and the other way round.
  near(copy.p0[0], -original.p1[0]);
  near(copy.p0[1], original.p1[1]);
  near(copy.p1[0], -original.p0[0]);
  near(copy.p1[1], original.p0[1]);
  assert.equal(done.sketch.constraints.filter((constraint) => constraint.kind === "symmetric").length, 2 + 1 + 3);
});

function solver_endpoints(arc) {
  const at = (angle) => [arc.c[0] + arc.r * Math.cos(angle), arc.c[1] + arc.r * Math.sin(angle)];
  return { p0: at(arc.a0), p1: at(arc.a1) };
}

test("the mirrored copy follows when the original is dragged", () => {
  // With the mirror line held, as a drawn one usually is.
  const sketch = model.withConstraint(mirrorFixture(), { kind: "fix", e: "e1" });
  const done = model.mirrorEntities(sketch, ["e2"], "e1");
  const solved = solve(gcs, done.sketch, { drag: { ref: "e2.p1", delta: [0, 6] } });
  assert.ok(solved.ok);
  const copyEnd = solved.sketch.entities.find((entity) => entity.id === "e5").p1;
  near(copyEnd[0], -15);
  near(copyEnd[1], 16, "the original's end rose 6, so the copy's did too");
});

test("mirroring across a slanted line reflects across that line", () => {
  // The line v = u. Reflecting (4, 1) across it gives (1, 4).
  let sketch = model.addLine(model.emptySketch(), [-10, -10], [10, 10]).sketch;
  sketch = model.addLine(sketch, [4, 1], [9, 2]).sketch;
  const done = model.mirrorEntities(sketch, ["e2"], "e1");
  const copy = done.sketch.entities.find((entity) => entity.id === "e3");
  near(copy.p0[0], 1);
  near(copy.p0[1], 4);
  near(copy.p1[0], 2);
  near(copy.p1[1], 9);
  assert.ok(solve(gcs, done.sketch).ok);
});

test("mirror refuses a pick that is not shapes plus a line, and the symmetric constraint wants two points and a line", () => {
  const sketch = mirrorFixture();
  assert.throws(() => model.mirrorEntities(sketch, ["e3"], "e3"), /across a line/);
  assert.throws(() => model.mirrorEntities(sketch, ["e1"], "e1"), /Select the shapes/);
  const made = model.constraintFor(sketch, "symmetric", ["e2.p0", "e1", "e2.p1"]);
  assert.deepEqual(made, { kind: "symmetric", a: "e2.p0", b: "e2.p1", e: "e1" });
  assert.throws(() => model.constraintFor(sketch, "symmetric", ["e2.p0", "e2.p1"]), /two points and a line/);
});
