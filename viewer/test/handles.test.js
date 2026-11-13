const test = require("node:test");
const assert = require("node:assert/strict");
const {
  axisParameter,
  angleAbout,
  unwrapAngle,
  snapStep,
  snap,
  clamp,
  format,
  anyPerpendicular,
  arcPoints,
} = require("../handles.js");

const close = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 1e-9, actual + " is not " + expected);

test("axisParameter finds where a ray passes the axis closest", () => {
  // A ray along -Y at height z = 7 crosses the Z axis line at t = 7.
  close(axisParameter([0, 10, 7], [0, -1, 0], [0, 0, 0], [0, 0, 1]), 7);
  // The axis need not start at the origin, and the ray may miss it.
  close(axisParameter([3, 10, 7], [0, -1, 0], [0, 0, 2], [0, 0, 1]), 5);
  // A slanted ray: the closest point on the axis is still found.
  const slant = [0, -Math.SQRT1_2, -Math.SQRT1_2];
  close(axisParameter([0, 10, 10], slant, [0, 0, 0], [0, 0, 1]), 0);
});

test("axisParameter gives null when the ray runs along the axis", () => {
  assert.equal(axisParameter([0, 0, 10], [0, 0, -1], [0, 0, 0], [0, 0, 1]), null);
});

test("angleAbout counts right-handed from the reference", () => {
  const axis = [0, 0, 1];
  const reference = [1, 0, 0];
  close(angleAbout([5, 0, 3], [0, 0, 0], axis, reference), 0);
  close(angleAbout([0, 5, 0], [0, 0, 0], axis, reference), 90);
  close(angleAbout([-5, 0, 0], [0, 0, 0], axis, reference), 180);
  close(angleAbout([0, -5, 0], [0, 0, 0], axis, reference), 270);
});

test("unwrapAngle keeps counting past a full turn", () => {
  assert.equal(unwrapAngle(350, 5), 365);
  assert.equal(unwrapAngle(10, 355), -5);
  assert.equal(unwrapAngle(180, 190), 190);
});

test("snapStep grows with the zoom and snap leaves no float residue", () => {
  assert.equal(snapStep(0.001), 0.01);
  assert.equal(snapStep(0.02), 0.1);
  assert.equal(snapStep(0.2), 1);
  assert.equal(snapStep(1000), 100);
  assert.equal(snap(0.30000000004, 0.1), 0.3);
  assert.equal(snap(12.26, 0.5), 12.5);
});

test("clamp holds a positive field above its floor and leaves a signed one alone", () => {
  assert.equal(clamp(-3, { min: 0.1 }), 0.1);
  assert.equal(clamp(-3, {}), -3);
  assert.equal(clamp(400, { min: 1, max: 360 }), 360);
});

test("format shows at most two decimals and the unit", () => {
  assert.equal(format(5, "mm"), "5 mm");
  assert.equal(format(2.345, "mm"), "2.35 mm");
  assert.equal(format(90, "°"), "90°");
  assert.equal(format(3), "3");
});

test("anyPerpendicular is a unit vector square to the axis", () => {
  for (const axis of [
    [0, 0, 1],
    [1, 0, 0],
    [0.6, 0.8, 0],
  ]) {
    const perpendicular = anyPerpendicular(axis);
    close(Math.hypot(...perpendicular), 1);
    close(perpendicular[0] * axis[0] + perpendicular[1] * axis[1] + perpendicular[2] * axis[2], 0);
  }
});

test("arcPoints runs from the reference to the angle", () => {
  const points = arcPoints(90);
  assert.deepEqual(points[0], [1, 0, 0]);
  close(points[points.length - 1][0], 0);
  close(points[points.length - 1][1], 1);
  assert.equal(points.length, 19);
});
