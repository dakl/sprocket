const test = require("node:test");
const assert = require("node:assert/strict");
const {
  distanceScale,
  pixelDelta,
  clampSensitivity,
  MIN_SENSITIVITY,
  MAX_SENSITIVITY,
  DEFAULT_SENSITIVITY,
} = require("../wheel-zoom.js");

const close = (actual, expected) =>
  assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} is not ${expected}`);

test("the zoom follows the distance scrolled, not the number of events", () => {
  // One 100 px event and twenty 5 px events zoom the same amount. With
  // OrbitControls' fixed step the twenty zoomed 0.95^20, about 2.8x.
  const one = distanceScale(100, 0, 800, 1);
  const twenty = distanceScale(5, 0, 800, 1) ** 20;
  close(twenty, one);
  close(one, Math.exp(0.05));
});

test("positive deltaY zooms out and negative zooms in, by the same factor", () => {
  assert.ok(distanceScale(50, 0, 800, 1) > 1);
  assert.ok(distanceScale(-50, 0, 800, 1) < 1);
  close(distanceScale(50, 0, 800, 1) * distanceScale(-50, 0, 800, 1), 1);
  assert.equal(distanceScale(0, 0, 800, 1), 1);
});

test("sensitivity scales the zoom per pixel", () => {
  close(Math.log(distanceScale(100, 0, 800, 2)), 2 * Math.log(distanceScale(100, 0, 800, 1)));
  close(Math.log(distanceScale(100, 0, 800, 0.5)), 0.5 * Math.log(distanceScale(100, 0, 800, 1)));
});

test("line and page deltas become pixels", () => {
  assert.equal(pixelDelta(3, 1, 800), 48);
  assert.equal(pixelDelta(1, 2, 800), 800);
  assert.equal(pixelDelta(7, 0, 800), 7);
});

test("one huge event is capped", () => {
  assert.equal(distanceScale(100_000, 0, 800, 1), distanceScale(400, 0, 800, 1));
  assert.equal(distanceScale(-100_000, 0, 800, 1), distanceScale(-400, 0, 800, 1));
});

test("sensitivity is clamped, and junk falls back to the default", () => {
  assert.equal(clampSensitivity(100), MAX_SENSITIVITY);
  assert.equal(clampSensitivity(0), MIN_SENSITIVITY);
  assert.equal(clampSensitivity(undefined), DEFAULT_SENSITIVITY);
  assert.equal(clampSensitivity("abc"), DEFAULT_SENSITIVITY);
  assert.equal(clampSensitivity("2"), 2);
});
