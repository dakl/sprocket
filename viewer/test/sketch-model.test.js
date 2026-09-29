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
  assert.equal(model.snapTarget(sketch, [0.1, 0], 1, ["e1.p0"]), null);
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
