// Run with `node --test viewer/test`. No dependencies: the form logic is plain
// functions, and the DOM half of params-form.js is covered by the Playwright
// suite.
const test = require("node:test");
const assert = require("node:assert/strict");
const { fieldsFor, parseField, applyField } = require("../params-form.js");

const extrudeSchema = {
  required: ["id", "type", "profile", "distance"],
  properties: {
    id: { type: "string" },
    type: { const: "extrude" },
    profile: { type: "string" },
    base: { type: "string" },
    distance: { type: "number", not: { const: 0 } },
  },
};

const sketchSchema = {
  required: ["id", "type", "plane"],
  properties: {
    id: { type: "string" },
    type: { const: "sketch" },
    plane: { oneOf: [{ enum: ["XY", "XZ", "YZ"] }] },
    rect: { type: "object" },
    polygon: { type: "array" },
    circle: { type: "object" },
  },
};

test("id and type are not editable", () => {
  const names = fieldsFor(extrudeSchema, { id: "e1", type: "extrude", profile: "s1", distance: 10 }).map((f) => f.name);
  assert.deepEqual(names, ["profile", "base", "distance"]);
});

test("a sketch shows only the profile shape it already has", () => {
  const feature = { id: "s1", type: "sketch", plane: "XY", rect: { width: 4, height: 3 } };
  assert.deepEqual(fieldsFor(sketchSchema, feature).map((f) => f.name), ["plane", "rect"]);
});

test("a number field takes a number, or an =expression the engine resolves", () => {
  const [, , distance] = fieldsFor(extrudeSchema, { id: "e1", type: "extrude", profile: "s1", distance: 10 });
  assert.deepEqual(parseField(distance, " 12.5 "), { value: 12.5 });
  assert.deepEqual(parseField(distance, "=width * 2"), { value: "=width * 2" });
  assert.match(parseField(distance, "twelve").error, /distance must be a number/);
});

test("emptying a required field is an error and emptying an optional one removes it", () => {
  const feature = { id: "e1", type: "extrude", profile: "s1", base: "f1", distance: 10 };
  const [, base, distance] = fieldsFor(extrudeSchema, feature);
  assert.match(parseField(distance, "").error, /required/);
  const result = applyField(feature, base, "");
  assert.equal("base" in result.feature, false);
  assert.equal(feature.base, "f1", "the original feature is not mutated");
});

test("a bare word is accepted as a JSON string, so nobody types quotes for an axis", () => {
  const [plane] = fieldsFor(sketchSchema, { id: "s1", type: "sketch", plane: "XY", rect: {} });
  assert.deepEqual(parseField(plane, "XZ"), { value: "XZ" });
  assert.deepEqual(parseField(plane, '{"a": 1}'), { value: { a: 1 } });
  assert.match(parseField(plane, "{oops").error, /not valid JSON/);
});
