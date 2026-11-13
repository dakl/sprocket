const test = require("node:test");
const assert = require("node:assert/strict");
const {
  PALETTE,
  normaliseHex,
  pushRecent,
  withFaceColour,
  withoutFaceColour,
  clearLabel,
} = require("../colours.js");

const box = { select: [{ op: "faces" }, { op: "within", box: [0, 0, 0, 1, 1, 1] }] };
const everything = { color: "#00ff00", select: { select: [{ op: "faces" }] } };
const mine = { color: "#ff0000", select: box };

test("the palette is twelve distinct colours", () => {
  assert.equal(PALETTE.length, 12);
  assert.equal(new Set(PALETTE.map((item) => item.hex)).size, 12);
  assert.ok(PALETTE.every((item) => normaliseHex(item.hex) === item.hex));
});

test("hex input accepts what people type and refuses the rest", () => {
  assert.equal(normaliseHex("A1B2C3"), "#a1b2c3");
  assert.equal(normaliseHex(" #a1b2c3 "), "#a1b2c3");
  assert.equal(normaliseHex("#abc"), "#aabbcc");
  assert.equal(normaliseHex("red"), null);
  assert.equal(normaliseHex("#12345"), null);
});

test("recent colours put the newest first with no repeats, six at most", () => {
  let recent = [];
  for (const colour of ["#111111", "#222222", "#111111"]) recent = pushRecent(recent, colour);
  assert.deepEqual(recent, ["#111111", "#222222"]);
  for (let k = 0; k < 10; k++) recent = pushRecent(recent, "#00000" + k);
  assert.equal(recent.length, 6);
  assert.deepEqual(pushRecent(recent, "nope"), recent);
});

test("a new face colour replaces the entry made for that face and keeps wider ones", () => {
  const info = {
    selector: box,
    entries: [
      { index: 0, color: "#00ff00", faces: 6 },
      { index: 1, color: "#ff0000", faces: 1 },
    ],
  };
  assert.deepEqual(withFaceColour([everything, mine], info, "#0000FF"), [
    everything,
    { color: "#0000ff", select: box },
  ]);
  const fresh = { selector: box, entries: [{ index: 0, color: "#00ff00", faces: 6 }] };
  assert.deepEqual(withFaceColour([everything], fresh, "#0000ff"), [
    everything,
    { color: "#0000ff", select: box },
  ]);
});

test("clearing drops every entry on the face, and the label warns when that reaches other faces", () => {
  const own = { selector: box, entries: [{ index: 1, color: "#ff0000", faces: 1 }] };
  assert.deepEqual(withoutFaceColour([everything, mine], own), [everything]);
  assert.equal(clearLabel(own), "Clear face colour");
  assert.equal(
    clearLabel({ selector: box, entries: [{ index: 0, color: "#00ff00", faces: 6 }] }),
    "Clear colour (6 faces)",
  );
  assert.equal(clearLabel({ selector: box, entries: [] }), null);
});

test("a finish on a face keeps the face's own colour, and a new colour keeps its finish", () => {
  const { withFaceAppearance, MATERIALS } = require("../colours.js");
  assert.deepEqual(
    MATERIALS.map((item) => item.id),
    ["matte_plastic", "glossy", "anodised_aluminium", "bare_metal"],
  );
  const own = { selector: box, entries: [{ index: 1, color: "#ff0000", faces: 1 }] };
  const finished = withFaceAppearance(
    [everything, mine],
    own,
    { material: "bare_metal" },
    "#999999",
  );
  assert.deepEqual(finished, [
    everything,
    { color: "#ff0000", select: box, material: "bare_metal" },
  ]);
  const recoloured = withFaceAppearance(finished, own, { color: "#0000ff" });
  assert.deepEqual(recoloured, [
    everything,
    { color: "#0000ff", select: box, material: "bare_metal" },
  ]);
  assert.deepEqual(withFaceAppearance(recoloured, own, { material: null }), [
    everything,
    { color: "#0000ff", select: box },
  ]);
  // No own entry: the colour on screen is kept.
  const fresh = { selector: box, entries: [{ index: 0, color: "#00ff00", faces: 6 }] };
  assert.deepEqual(withFaceAppearance([everything], fresh, { material: "glossy" }, "#00ff00"), [
    everything,
    { color: "#00ff00", select: box, material: "glossy" },
  ]);
});
