const test = require("node:test");
const assert = require("node:assert/strict");
const { layoutLabels, leaderEnd, LEADER, MARGIN } = require("../labels.js");

const view = { left: 0, top: 0, width: 1000, height: 800 };
// The layout aims for GAP between labels and can stop a hair short of it, so
// this asks only that two chips do not intersect.
const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

test("a label sits a leader length out from its anchor, away from the centre", () => {
  const [label] = layoutLabels([{ id: "a", x: 700, y: 400, width: 40, height: 16 }], view);
  assert.equal(label.x + label.width / 2, 700 + LEADER);
  assert.equal(label.y + label.height / 2, 400);
  assert.deepEqual([label.anchorX, label.anchorY], [700, 400]);
});

test("labels whose anchors share a point end up apart", () => {
  const anchors = ["a", "b", "c", "d"].map((id) => ({ id, x: 300, y: 300, width: 60, height: 16 }));
  const labels = layoutLabels(anchors, view);
  for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) assert.ok(!overlaps(labels[i], labels[j]), `${labels[i].id} overlaps ${labels[j].id}`);
});

test("anchors on the centre each get a direction of their own", () => {
  const labels = layoutLabels(
    [
      { id: "a", x: 500, y: 400, width: 30, height: 16 },
      { id: "b", x: 500, y: 400, width: 30, height: 16 },
    ],
    view
  );
  assert.ok(!overlaps(labels[0], labels[1]));
});

test("a label near the edge stays on screen", () => {
  const [label] = layoutLabels([{ id: "a", x: 990, y: 5, width: 80, height: 16 }], view);
  assert.ok(label.x >= MARGIN && label.x + label.width <= view.width - MARGIN);
  assert.ok(label.y >= MARGIN && label.y + label.height <= view.height - MARGIN);
});

test("the leader line ends on the label's edge nearest the anchor", () => {
  assert.deepEqual(leaderEnd({ x: 100, y: 100, width: 50, height: 20, anchorX: 0, anchorY: 110 }), { x: 100, y: 110 });
  assert.deepEqual(leaderEnd({ x: 100, y: 100, width: 50, height: 20, anchorX: 300, anchorY: 0 }), { x: 150, y: 100 });
});

test("the layout is the same every time for the same input", () => {
  const anchors = ["a", "b", "c"].map((id, index) => ({ id, x: 400 + index, y: 400, width: 50, height: 16 }));
  assert.deepEqual(layoutLabels(anchors, view), layoutLabels(anchors, view));
});

test("labels crowded against the edge of the view still end up apart", () => {
  // The 22 labels of luxo mk2's base in a narrow window, captured from the
  // viewer. A clamp done only at the end left 6 pairs on top of each other:
  // short labels pushed sideways off the edge came back onto long ones.
  const { view: narrow, anchors } = require("./labels-luxo.json");
  const labels = layoutLabels(anchors, narrow);
  for (const label of labels) assert.ok(label.x >= narrow.left + MARGIN - 1e-6 && label.x + label.width <= narrow.left + narrow.width - MARGIN + 1e-6, `${label.id} is off the view`);
  for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) assert.ok(!overlaps(labels[i], labels[j]), `${labels[i].id} overlaps ${labels[j].id}`);
});
