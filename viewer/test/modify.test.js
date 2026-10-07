const test = require("node:test");
const assert = require("node:assert/strict");
const { defaults, featureFor, parseNumber, visible, TOOLS } = require("../modify.js");

const edges = { select: [{ op: "edges" }], expected_count: 4 };

test("parseNumber reads plain numbers and a trailing mm", () => {
  assert.equal(parseNumber("2.5 mm"), 2.5);
  assert.equal(parseNumber(" 3 "), 3);
  assert.ok(Number.isNaN(parseNumber("")));
  assert.ok(Number.isNaN(parseNumber("two")));
});

test("fillet and chamfer take their one number and the selector", () => {
  assert.deepEqual(featureFor("fillet", { radius: "2" }, { base: "f2", edges }), { type: "fillet", base: "f2", edges, radius: 2 });
  assert.deepEqual(featureFor("chamfer", { length: "1.5" }, { base: "f2", edges }), { type: "chamfer", base: "f2", edges, length: 1.5 });
});

test("a number that is not above zero is refused with the field's name", () => {
  assert.match(featureFor("fillet", { radius: "0" }, { base: "f2", edges }).error, /Radius/);
  assert.match(featureFor("chamfer", { length: "abc" }, { base: "f2", edges }).error, /Distance/);
});

test("shell opens the picked face unless told to stay closed", () => {
  const open = { select: [{ op: "faces" }] };
  const values = { ...defaults("shell"), thickness: "2" };
  assert.deepEqual(featureFor("shell", values, { base: "f2", open }), { type: "shell", base: "f2", thickness: 2, open });
  assert.equal("open" in featureFor("shell", { ...values, closed: true }, { base: "f2", open }), false);
  assert.equal("open" in featureFor("shell", values, { base: "f2" }), false);
  assert.equal(featureFor("shell", { ...values, outward: true }, { base: "f2" }).outward, true);
});

test("a hole drills against the face normal, and the head fields follow the kind", () => {
  const target = { base: "f2", point: [1, 2, 10], normal: [0, 0, 1] };
  const simple = featureFor("hole", { ...defaults("hole"), diameter: "4", depth: "6" }, target);
  assert.deepEqual(simple, { type: "hole", base: "f2", points: [[1, 2, 10]], direction: [0, 0, -1], kind: "simple", diameter: 4, depth: 6 });
  const through = featureFor("hole", { ...defaults("hole"), through: true }, target);
  assert.equal(through.through, true);
  assert.equal("depth" in through, false);
  const bore = featureFor("hole", { ...defaults("hole"), kind: "counterbore" }, target);
  assert.equal(bore.head_diameter, 8);
  assert.equal(bore.head_depth, 3);
  assert.equal("head_angle" in bore, false);
  const sink = featureFor("hole", { ...defaults("hole"), kind: "countersink" }, target);
  assert.equal(sink.head_angle, 90);
  assert.equal("head_depth" in sink, false);
  assert.match(featureFor("hole", { ...defaults("hole"), kind: "countersink", head_diameter: "3" }, target).error, /larger/);
});

test("fields show only for the kind and depth they belong to", () => {
  const field = (name) => TOOLS.hole.fields.find((item) => item.name === name);
  assert.equal(visible(field("head_depth"), { kind: "simple" }), false);
  assert.equal(visible(field("head_depth"), { kind: "counterbore" }), true);
  assert.equal(visible(field("depth"), { through: true }), false);
});

test("revolve sweeps the sketch about an axis, a full turn by default", () => {
  assert.deepEqual(featureFor("revolve", { axis: "Y", angle: "360" }, { profile: "f1" }), { type: "revolve", profile: "f1", axis: "Y" });
  assert.equal(featureFor("revolve", { axis: "X", angle: "90" }, { profile: "f1" }).angle, 90);
  assert.match(featureFor("revolve", { axis: "X", angle: "400" }, { profile: "f1" }).error, /360/);
});

test("starting values scale with the part and fall back to fixed ones", () => {
  const { nice } = require("../modify.js");
  const box = [40, 30, 10];
  assert.equal(defaults("fillet", { box }).radius, 0.5);
  assert.equal(defaults("chamfer", { box }).length, 0.5);
  assert.equal(defaults("shell", { box }).thickness, 0.8);
  assert.equal(defaults("fillet", { box: [400, 300, 200] }).radius, 10);
  assert.equal(defaults("fillet").radius, 1);
  assert.equal(defaults("shell").thickness, 2);
  const hole = defaults("hole", { box, face: [40, 30] });
  assert.deepEqual([hole.diameter, hole.depth, hole.head_diameter, hole.head_depth], [4.5, 5.5, 9, 3.5]);
  assert.ok(hole.head_diameter > hole.diameter);
  assert.equal(defaults("hole").diameter, 4);
  // A flat plate keeps a usable radius; a zero side does not collapse it.
  assert.equal(defaults("fillet", { box: [40, 30, 0] }).radius, 1.5);
  assert.equal(nice(0.04, 1), 0.1);
  assert.equal(nice(NaN, 7), 7);
});
