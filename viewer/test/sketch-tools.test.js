const test = require("node:test");
const assert = require("node:assert/strict");
require("../vendor/planegcs.js");
const model = require("../sketch-model.js");
const tools = require("../sketch-tools.js");
const { solve } = require("../sketch-solver.js");

let gcs;
test.before(async () => {
  gcs = await globalThis.Planegcs.createSolver();
});

const near = (actual, expected, message) =>
  assert.ok(
    Math.abs(actual - expected) < 1e-6,
    `${message || ""} expected ${expected}, got ${actual}`,
  );
const nearPoint = (actual, expected) => {
  near(actual[0], expected[0]);
  near(actual[1], expected[1]);
};
const line = (sketch, p0, p1) => model.addLine(sketch, p0, p1).sketch;
const byId = (sketch, id) => sketch.entities.find((entity) => entity.id === id);
const solved = (sketch) => {
  const result = solve(gcs, sketch);
  assert.ok(result.ok, `the sketch solves: ${JSON.stringify(result.conflicting)}`);
  return result.sketch;
};

// e1 runs level from 0 to 30, e2 and e3 cross it upright at x = 10 and x = 20.
function crossed() {
  let sketch = line(model.emptySketch(), [0, 0], [30, 0]);
  sketch = line(sketch, [10, -5], [10, 5]);
  return line(sketch, [20, -5], [20, 5]);
}

test("trim cuts a line back to the crossing, at either end", () => {
  const sketch = crossed();
  const startCut = tools.trimEntity(sketch, "e1", [3, 0]);
  nearPoint(byId(startCut.sketch, "e1").p0, [10, 0]);
  nearPoint(byId(startCut.sketch, "e1").p1, [30, 0]);
  assert.ok(
    startCut.sketch.constraints.some(
      (constraint) =>
        constraint.kind === "point_on" && constraint.a === "e1.p0" && constraint.b === "e2",
    ),
  );
  const endCut = tools.trimEntity(sketch, "e1", [27, 0]);
  nearPoint(byId(endCut.sketch, "e1").p1, [20, 0]);
  solved(startCut.sketch);
  solved(endCut.sketch);
});

test("trim between two crossings splits the line, and the far end's constraints go with the new piece", () => {
  let sketch = crossed();
  sketch = model.withConstraint(sketch, { kind: "horizontal", e: "e1" });
  sketch = model.withConstraint(sketch, { kind: "point_on", a: "e2.p0", b: "e1" });
  sketch = model.withConstraint(sketch, { kind: "coincident", a: "e1.p1", b: "e3.p0" });
  const cut = tools.trimEntity(sketch, "e1", [15, 0]);
  assert.deepEqual(cut.ids, ["e1", "e4"]);
  nearPoint(byId(cut.sketch, "e1").p1, [10, 0]);
  nearPoint(byId(cut.sketch, "e4").p0, [20, 0]);
  nearPoint(byId(cut.sketch, "e4").p1, [30, 0]);
  const has = (kind, fields) =>
    cut.sketch.constraints.some(
      (constraint) =>
        constraint.kind === kind &&
        Object.entries(fields).every(([key, value]) => constraint[key] === value),
    );
  assert.ok(
    has("coincident", { a: "e4.p1", b: "e3.p0" }),
    "the old far end's joint moved to the new piece",
  );
  assert.ok(!has("coincident", { a: "e1.p1" }), "the old end is no longer joined");
  assert.ok(has("horizontal", { e: "e1" }) && has("horizontal", { e: "e4" }));
  assert.ok(has("point_on", { a: "e1.p1", b: "e2" }) && has("point_on", { a: "e4.p0", b: "e3" }));
  solved(cut.sketch);
});

test("trim with nothing crossing deletes the shape and what mentions it", () => {
  let sketch = line(model.emptySketch(), [0, 0], [10, 0]);
  sketch = model.withConstraint(sketch, { kind: "horizontal", e: "e1" });
  const cut = tools.trimEntity(sketch, "e1", [5, 0]);
  assert.deepEqual(cut.sketch.entities, []);
  assert.deepEqual(cut.sketch.constraints, []);
});

test("trim turns a circle crossed twice into the arc that is left", () => {
  let { sketch } = model.addCircle(model.emptySketch(), [0, 0], 10);
  sketch = line(sketch, [-20, 5], [20, 5]); // crosses at x = +-8.66
  sketch = line(sketch, [-20, -5], [20, -5]);
  // Click the top, between the upper line's two crossings.
  const cut = tools.trimEntity(sketch, "e1", [0, 10]);
  const arc = byId(cut.sketch, "e1");
  assert.equal(arc.kind, "arc");
  // The upper line crosses at 30 and 150 degrees, so the 120 degrees between them go and 240 remain.
  near(arc.a1 - arc.a0, (240 * Math.PI) / 180);
  solved(cut.sketch);
  // One crossing is not enough to cut a circle.
  const single = line(model.addCircle(model.emptySketch(), [0, 0], 10).sketch, [0, 0], [20, 0]);
  assert.throws(() => tools.trimEntity(single, "e1", [0, 10]), /two crossings/);
});

test("trim shortens an arc and splits it in the middle", () => {
  let sketch = model.addArc(model.emptySketch(), [10, 0], [-10, 0], [0, 10]).sketch; // the upper half circle
  sketch = line(sketch, [-5, -1], [-5, 20]);
  sketch = line(sketch, [5, -1], [5, 20]);
  const middle = tools.trimEntity(sketch, "e1", [0, 10]);
  assert.deepEqual(middle.ids, ["e1", "e4"]);
  const [first, second] = [byId(middle.sketch, "e1"), byId(middle.sketch, "e4")];
  // The first piece runs from the arc's start (10, 0) to x = 5, the second from x = -5 to the end.
  nearPoint(
    [first.c[0] + first.r * Math.cos(first.a0), first.c[1] + first.r * Math.sin(first.a0)],
    [10, 0],
  );
  nearPoint(
    [first.c[0] + first.r * Math.cos(first.a1), first.c[1] + first.r * Math.sin(first.a1)],
    [5, Math.sqrt(75)],
  );
  nearPoint(
    [second.c[0] + second.r * Math.cos(second.a0), second.c[1] + second.r * Math.sin(second.a0)],
    [-5, Math.sqrt(75)],
  );
  assert.ok(
    middle.sketch.constraints.some(
      (constraint) => constraint.kind === "equal" && constraint.b === "e4",
    ),
  );
  solved(middle.sketch);
  const startPiece = tools.trimEntity(sketch, "e1", [8, 6]);
  const arc = byId(startPiece.sketch, "e1");
  nearPoint(
    [arc.c[0] + arc.r * Math.cos(arc.a0), arc.c[1] + arc.r * Math.sin(arc.a0)],
    [5, Math.sqrt(75)],
  );
});

test("trim refuses a projected reference", () => {
  const sketch = line(model.emptySketch(), [0, 0], [10, 0]);
  sketch.entities[0].reference = { sketch: "f1", entity: "e1" };
  assert.throws(() => tools.trimEntity(sketch, "e1", [5, 0]), /locked/);
});

test("extend runs a line on to the first shape in its way, and drops the joint at that end", () => {
  let sketch = line(model.emptySketch(), [0, 0], [10, 0]);
  sketch = line(sketch, [25, -5], [25, 5]);
  sketch = line(sketch, [40, -5], [40, 5]);
  sketch = model.withConstraint(sketch, { kind: "distance", a: "e1.p0", b: "e1.p1", value: 10 });
  const longer = tools.extendEntity(sketch, "e1", [9, 0]);
  nearPoint(byId(longer.sketch, "e1").p1, [25, 0]);
  nearPoint(byId(longer.sketch, "e1").p0, [0, 0]);
  assert.ok(
    !longer.sketch.constraints.some((constraint) => constraint.kind === "distance"),
    "the old length no longer holds",
  );
  assert.ok(
    longer.sketch.constraints.some(
      (constraint) =>
        constraint.kind === "point_on" && constraint.a === "e1.p1" && constraint.b === "e2",
    ),
  );
  solved(longer.sketch);
  // The other end looks the other way, where nothing is.
  assert.throws(() => tools.extendEntity(sketch, "e1", [1, 0]), /Nothing in the way/);
});

test("extend runs an arc along its circle to a line, not back into itself", () => {
  let sketch = model.addArc(
    model.emptySketch(),
    [10, 0],
    [0, 10],
    [Math.SQRT1_2 * 10, Math.SQRT1_2 * 10],
  ).sketch; // a quarter circle
  sketch = line(sketch, [-20, 5], [20, 5]); // crosses the circle at x = +-8.66
  const longer = tools.extendEntity(sketch, "e1", [0, 10]);
  const arc = byId(longer.sketch, "e1");
  nearPoint(
    [arc.c[0] + arc.r * Math.cos(arc.a1), arc.c[1] + arc.r * Math.sin(arc.a1)],
    [-Math.sqrt(75), 5],
  );
  nearPoint([arc.c[0] + arc.r * Math.cos(arc.a0), arc.c[1] + arc.r * Math.sin(arc.a0)], [10, 0]);
  solved(longer.sketch);
});

test("offset of a rectangle outward is a bigger rectangle that solves", () => {
  const { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [40, 20]);
  // Rectangle lines walk counter-clockwise, so the left of e1 (the bottom edge) is inside.
  const inside = tools.offsetChain(sketch, "e1", 3);
  assert.equal(inside.ids.length, 4);
  const box = (result) => {
    const points = result.ids.flatMap((id) => [
      byId(result.sketch, id).p0,
      byId(result.sketch, id).p1,
    ]);
    return [
      Math.min(...points.map((p) => p[0])),
      Math.min(...points.map((p) => p[1])),
      Math.max(...points.map((p) => p[0])),
      Math.max(...points.map((p) => p[1])),
    ];
  };
  assert.deepEqual(
    box(inside).map((value) => Math.round(value * 1e6) / 1e6),
    [3, 3, 37, 17],
  );
  const outside = tools.offsetChain(sketch, "e1", -3);
  assert.deepEqual(
    box(outside).map((value) => Math.round(value * 1e6) / 1e6),
    [-3, -3, 43, 23],
  );
  const result = solved(outside.sketch);
  assert.equal(result.entities.length, 8);
  assert.equal(
    outside.sketch.constraints.filter((constraint) => constraint.kind === "parallel").length,
    4,
  );
});

test("offset of a circle is a concentric circle with its own radius", () => {
  const { sketch } = model.addCircle(model.emptySketch(), [5, 5], 10);
  const outside = tools.offsetChain(sketch, "e1", -2);
  assert.equal(byId(outside.sketch, "e2").r, 12);
  const inside = tools.offsetChain(sketch, "e1", 4);
  assert.equal(byId(inside.sketch, "e2").r, 6);
  assert.throws(() => tools.offsetChain(sketch, "e1", 11), /bigger than the radius/);
  solved(outside.sketch);
});

test("offset follows a rounded corner: lines meet an arc at a tangent", () => {
  let { sketch } = model.addRectangle(model.emptySketch(), [0, 0], [40, 20]);
  // Round the corner at the end of e1 and the start of e2, whatever order the constraints name them.
  const corner = ["e1.p1", "e2.p0"].find((reference) =>
    sketch.constraints.some(
      (constraint) =>
        constraint.kind === "coincident" && [constraint.a, constraint.b].includes(reference),
    ),
  );
  sketch = solved(model.addFillet(sketch, corner, 5).sketch);
  const outside = tools.offsetChain(sketch, "e1", -3);
  assert.equal(outside.ids.length, 5);
  const arc = outside.ids
    .map((id) => byId(outside.sketch, id))
    .find((entity) => entity.kind === "arc");
  near(arc.r, 8);
  assert.equal(
    outside.sketch.constraints.filter(
      (constraint) => constraint.kind === "tangent" && outside.ids.includes(constraint.a),
    ).length,
    2,
    "both joins of the arc stay smooth",
  );
  solved(outside.sketch);
});

test("sideOf says which side of a shape a point is on", () => {
  const sketch = line(model.emptySketch(), [0, 0], [10, 0]);
  assert.equal(tools.sideOf(sketch, "e1", [5, 3]), 1);
  assert.equal(tools.sideOf(sketch, "e1", [5, -3]), -1);
  const circle = model.addCircle(model.emptySketch(), [0, 0], 5).sketch;
  assert.equal(tools.sideOf(circle, "e1", [1, 1]), 1);
  assert.equal(tools.sideOf(circle, "e1", [9, 9]), -1);
});
