const test = require("node:test");
const assert = require("node:assert/strict");
const { editsFor } = require("../edits.js");

const sketch = { id: "s1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } };
const extrude = { id: "e1", type: "extrude", profile: "s1", distance: 10 };
const box = { features: [sketch, extrude], rollbackIndex: 2 };
const topFace = { type: "extrude_face", point: [0, 0, 10], normal: [0, 0, 1], distance: 5 };

test("extrude_face appends a feature based on the newest one, with a fresh id", () => {
  const [insert] = editsFor(box, topFace);
  assert.equal(insert.op, "insert");
  assert.equal(insert.feature.id, "f3");
  assert.equal(insert.feature.base, "e1");
  assert.deepEqual(insert.feature.face.select[1], { op: "nearest_to", point: [0, 0, 10], normal: [0, 0, 1] });
});

test("building after a rollback deletes the tail from the end, then inserts", () => {
  const edits = editsFor({ ...box, rollbackIndex: 1 }, { type: "add_sketch", plane: "XY", polygon: [[0, 0], [5, 0], [5, 5]] });
  assert.deepEqual(edits.slice(0, 1), [{ op: "delete", id: "e1" }]);
  assert.equal(edits[1].feature.id, "f2");
});

test("an id already in use is skipped", () => {
  const crowded = { features: [sketch, { ...extrude, id: "f2" }], rollbackIndex: 2 };
  assert.equal(editsFor(crowded, topFace)[0].feature.id, "f3");
  const taken = { features: [sketch, { ...extrude, id: "f3" }], rollbackIndex: 2 };
  assert.equal(editsFor(taken, topFace)[0].feature.id, "f4");
});

test("extrude_sketch fuses into the newest solid, skipping sketches", () => {
  const withSecondSketch = { features: [sketch, extrude, { id: "s2", type: "sketch", plane: "XY", polygon: [] }], rollbackIndex: 3 };
  const [insert] = editsFor(withSecondSketch, { type: "extrude_sketch", profile: "s2", distance: 4 });
  assert.equal(insert.feature.base, "e1");
});

test("the first extrude has no base", () => {
  const [insert] = editsFor({ features: [sketch], rollbackIndex: 1 }, { type: "extrude_sketch", profile: "s1", distance: 4 });
  assert.equal("base" in insert.feature, false);
});

test("extrude_sketch refuses a sketch that is not there", () => {
  assert.throws(() => editsFor(box, { type: "extrude_sketch", profile: "nope", distance: 4 }), /unknown sketch id/);
});

test("extrude_face refuses an empty document", () => {
  assert.throws(() => editsFor({ features: [], rollbackIndex: 0 }, topFace), /no part/);
});

test("dragging a corner turns a rect sketch into a polygon and leaves the input alone", () => {
  const [update] = editsFor(box, { type: "update_sketch", id: "s1", polygon: [[0, 0], [1, 0], [1, 1]] });
  assert.equal("rect" in update.feature, false);
  assert.deepEqual(update.feature.polygon, [[0, 0], [1, 0], [1, 1]]);
  assert.ok(sketch.rect, "the state's own feature is not mutated");
});

test("update_sketch refuses a feature that is not a sketch", () => {
  assert.throws(() => editsFor(box, { type: "update_sketch", id: "e1", polygon: [] }), /no sketch/);
});

test("set_body passes only the fields given", () => {
  assert.deepEqual(editsFor(box, { type: "set_body", id: "f2", color: "#ff0000" }), [{ op: "set_body", id: "f2", color: "#ff0000" }]);
  // Visibility is view state, so it never becomes an edit.
  assert.deepEqual(editsFor(box, { type: "set_body", id: "f2", color: "#ff0000", visible: false }), [{ op: "set_body", id: "f2", color: "#ff0000" }]);
  assert.throws(() => editsFor(box, { type: "set_body", id: "f2" }), /needs name or color/);
});

test("set_rollback clamps to the feature count", () => {
  assert.deepEqual(editsFor(box, { type: "set_rollback", index: 99 }), [{ op: "set_rollback", index: 2 }]);
  assert.deepEqual(editsFor(box, { type: "set_rollback", index: -3 }), [{ op: "set_rollback", index: 0 }]);
});

test("new_document deletes every feature newest first, then rolls back to zero", () => {
  assert.deepEqual(editsFor(box, { type: "new_document" }), [
    { op: "delete", id: "e1" },
    { op: "delete", id: "s1" },
    { op: "set_rollback", index: 0 },
  ]);
});

test("an unknown gesture is an error, not a silent no-op", () => {
  assert.throws(() => editsFor(box, { type: "bogus" }), /unknown viewer command/);
});

const drawn = [{ id: "e1", kind: "line", p0: [0, 0], p1: [1, 0] }];

test("add_sketch takes entities and constraints as well as a polygon", () => {
  const [insert] = editsFor({ features: [], rollbackIndex: 0 }, { type: "add_sketch", plane: "XY", entities: drawn, constraints: [{ kind: "horizontal", e: "e1" }] });
  assert.deepEqual(insert.feature, { id: "f1", type: "sketch", plane: "XY", entities: drawn, constraints: [{ kind: "horizontal", e: "e1" }] });
  assert.throws(() => editsFor(box, { type: "add_sketch", plane: "XY" }), /entities or polygon/);
});

test("extrude_sketch with profiles keeps the region names and checks every sketch exists", () => {
  const profiles = [{ sketch: "s1", bounded_by: ["e1", "e2"], inside: [1, 1] }];
  const [insert] = editsFor(box, { type: "extrude_sketch", profiles, distance: 3 });
  assert.deepEqual(insert.feature.profiles, profiles);
  assert.equal("profile" in insert.feature, false);
  assert.equal(insert.feature.base, "e1");
  assert.throws(() => editsFor(box, { type: "extrude_sketch", profiles: [{ sketch: "e1", bounded_by: [] }], distance: 3 }), /unknown sketch id 'e1'/);
  assert.throws(() => editsFor(box, { type: "extrude_sketch", distance: 3 }), /profile or profiles/);
});

test("update_sketch_entities redraws a sketch and drops its old single shape", () => {
  const [update] = editsFor(box, { type: "update_sketch_entities", id: "s1", entities: drawn });
  assert.equal("rect" in update.feature, false);
  assert.deepEqual(update.feature.entities, drawn);
  assert.deepEqual(update.feature.constraints, []);
  assert.equal(update.feature.plane, "XY");
  assert.throws(() => editsFor(box, { type: "update_sketch_entities", id: "e1", entities: drawn }), /no sketch/);
});

test("extrude_face uses the selector the engine made from the pick, when there is one", () => {
  const face = { from: "part", select: [{ op: "faces" }, { op: "at_end", axis: "Z", end: "max" }], expected_count: 1 };
  const edits = editsFor(box, { type: "extrude_face", face, distance: 5 });
  assert.deepEqual(edits.at(-1).feature.face, face);
});

test("extrude_face without a selector still needs the point and normal", () => {
  assert.throws(() => editsFor(box, { type: "extrude_face", distance: 5 }), /point/);
});
