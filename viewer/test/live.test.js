const test = require("node:test");
const assert = require("node:assert/strict");
const { changeCue, selectionToApply } = require("../live.js");

test("an agent's edit gets a cue naming the agent and the changed features", () => {
  const cue = changeCue({ author: "agent:claude", changes: [{ action: "update", feature_id: "e1" }, { action: "update", feature_id: "e1" }] });
  assert.deepEqual(cue, { text: "Updated by agent:claude", featureIds: ["e1"] });
});

test("the person's own edit and a view change get no cue", () => {
  assert.equal(changeCue({ author: "viewer", changes: [{ action: "insert", feature_id: "e2" }] }), null);
  assert.equal(changeCue({ author: null, changes: [] }), null);
  assert.equal(changeCue(undefined), null);
});

test("a reload from disk says so", () => {
  assert.deepEqual(changeCue({ author: "file", changes: [{ action: "reload" }] }), { text: "Reloaded from disk", featureIds: [] });
});

test("the daemon's selection is put back only for the revision on screen", () => {
  const view = { handles: ["e4", "f2", "f7"], sketches: ["s1"], revision: 5, stale: false };
  assert.deepEqual(selectionToApply(view, 5), { face: 2, edge: 4, sketches: ["s1"] });
  assert.equal(selectionToApply(view, 4), null);
  assert.equal(selectionToApply({ ...view, stale: true }, 5), null);
  assert.deepEqual(selectionToApply({ handles: [], sketches: [], revision: 5, stale: false }, 5), { face: null, edge: null, sketches: [] });
});

test("a referenced file that changed says which, and lights its reference row", () => {
  const cue = changeCue({ author: "file", changes: [{ action: "reference_changed", feature_id: "board", file: "orangepi-zero-3w.sprocket" }] });
  assert.deepEqual(cue, { text: "Rebuilt: orangepi-zero-3w.sprocket changed", featureIds: ["board"] });
});
