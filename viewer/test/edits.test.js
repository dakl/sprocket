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
