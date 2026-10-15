// Toolbar and browser controls that used to be placeholders: Export, the
// Parameters rows in the browser tree, and Offset plane.
const fs = require("node:fs");
const { test, expect } = require("@playwright/test");
const { rpc, resetDocument, settled, cameraStill } = require("./helpers");

const BOX = {
  features: [
    { id: "f1", type: "sketch", plane: "XY", rect: { width: 40, height: 30 } },
    { id: "f2", type: "extrude", profile: "f1", distance: 10 },
  ],
  rollback_index: 2,
};
const REBUILD = { timeout: 15_000 };

test.beforeEach(async ({ page, request }) => {
  await resetDocument(request, BOX);
  await page.goto("/index.html");
  await page.waitForFunction(() => window.__tcad && window.__tcad.mesh !== null);
  await settled(page);
});

test("Export saves the part as STL, 3MF or STEP, and the file is the part", async ({ page }, info) => {
  await page.locator("#tool-export").click();
  await page.screenshot({ path: info.outputPath("export-menu.png") });
  await page.keyboard.press("Escape");
  for (const [label, check] of [
    ["STL", (bytes) => expect(bytes.readUInt32LE(80)).toBe(12)], // a box is 12 triangles
    ["3MF", (bytes) => expect(bytes.subarray(0, 2).toString()).toBe("PK")],
    ["STEP", (bytes) => expect(bytes.subarray(0, 12).toString()).toBe("ISO-10303-21")],
  ]) {
    await page.locator("#tool-export").click();
    const downloading = page.waitForEvent("download");
    await page.locator("#context-menu button", { hasText: label }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\.${label.toLowerCase()}$`));
    check(fs.readFileSync(await download.path()));
  }
});

test("Export of an empty document says there is nothing to export", async ({ page, request }) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  await page.locator("#tool-export").click();
  await page.locator("#context-menu button", { hasText: "STL" }).click();
  await expect(page.locator("#status")).toContainText("no part to export");
});

const parameters = async (request) => (await rpc(request, "get_document")).parameters;

test("the Parameters rows add, change and remove a named parameter, and an expression follows the others", async ({ page, request }, info) => {
  const rows = page.locator(".parameter-row");
  // The last row adds one.
  await rows.last().locator(".browser-param.name").fill("w");
  await rows.last().locator(".browser-param").nth(1).fill("10");
  await rows.last().locator(".browser-button").click();
  await expect.poll(() => parameters(request), REBUILD).toEqual({ w: 10 });
  await expect(rows).toHaveCount(2);

  await rows.last().locator(".browser-param.name").fill("h");
  await rows.last().locator(".browser-param").nth(1).fill("w * 2");
  await rows.last().locator(".browser-param").nth(1).press("Enter");
  await expect.poll(() => parameters(request), REBUILD).toEqual({ w: 10, h: "=w * 2" });

  await page.screenshot({ path: info.outputPath("parameters-rows.png") });
  // Changing w is one edit, and h follows it: 15 * 2 is 30 whatever the engine says.
  await rows.first().locator(".browser-param").fill("15");
  await rows.first().locator(".browser-param").press("Enter");
  await expect.poll(() => parameters(request), REBUILD).toEqual({ w: 15, h: "=w * 2" });

  // A cycle is refused and the box goes back to what it was.
  await rows.first().locator(".browser-param").fill("=h");
  await rows.first().locator(".browser-param").press("Enter");
  await expect(page.locator("#status")).toContainText("cycle", REBUILD);
  await expect(rows.first().locator(".browser-param")).toHaveValue("15");
  expect(await parameters(request)).toEqual({ w: 15, h: "=w * 2" });

  await rows.nth(1).locator(".browser-button").click();
  await expect.poll(() => parameters(request), REBUILD).toEqual({ w: 15 });
  // It is the same edit an agent makes, so it is undoable: undo puts h back.
  await rpc(request, "undo");
  expect(await parameters(request)).toEqual({ w: 15, h: "=w * 2" });
});

test("a parameter used in a feature moves the part when it changes", async ({ page, request }) => {
  await rpc(request, "apply_edit", { edits: [{ op: "set_parameter", name: "height", value: 10 }, { op: "update", id: "f2", feature: { ...BOX.features[1], distance: "=height" } }] });
  await expect(page.locator(".parameter-row .browser-param").first()).toHaveValue("10", REBUILD);
  await page.locator(".parameter-row .browser-param").first().fill("25");
  await page.locator(".parameter-row .browser-param").first().press("Enter");
  await expect.poll(async () => (await rpc(request, "measure")).volume, REBUILD).toBeCloseTo(40 * 30 * 25, 3);
});

test("a parameter that drives a sketch dimension moves the sketch itself, not only the extrude", async ({ page, request }, info) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  const lines = [
    ["e1", [0, 0], [40, 0]],
    ["e2", [40, 0], [40, 20]],
    ["e3", [40, 20], [0, 20]],
    ["e4", [0, 20], [0, 0]],
  ].map(([id, p0, p1]) => ({ id, kind: "line", p0, p1 }));
  const constraints = [
    { kind: "coincident", a: "e1.p1", b: "e2.p0" },
    { kind: "coincident", a: "e2.p1", b: "e3.p0" },
    { kind: "coincident", a: "e3.p1", b: "e4.p0" },
    { kind: "coincident", a: "e4.p1", b: "e1.p0" },
    { kind: "horizontal", e: "e1" },
    { kind: "horizontal", e: "e3" },
    { kind: "vertical", e: "e2" },
    { kind: "vertical", e: "e4" },
    { kind: "coincident", a: "e1.p0", b: "origin" },
    { kind: "distance", a: "e1.p0", b: "e1.p1", value: "=w" },
    { kind: "distance", a: "e2.p0", b: "e2.p1", value: 20 },
  ];
  await rpc(request, "apply_edit", {
    edits: [
      { op: "set_parameter", name: "w", value: 40 },
      { op: "insert", feature: { id: "f1", type: "sketch", plane: "XY", entities: lines, constraints } },
      { op: "insert", feature: { id: "f2", type: "extrude", profile: "f1", distance: 10 } },
    ],
  });
  await expect.poll(async () => (await rpc(request, "measure")).volume, REBUILD).toBeCloseTo(8000, 3);
  await expect(page.locator(".parameter-row .browser-param").first()).toHaveValue("40", REBUILD);

  await page.locator(".parameter-row .browser-param").first().fill("60");
  await page.locator(".parameter-row .browser-param").first().press("Enter");
  await expect.poll(async () => (await rpc(request, "measure")).volume, REBUILD).toBeCloseTo(12000, 3);
  const sketch = (await rpc(request, "get_document")).features[0];
  expect(sketch.entities.find((item) => item.id === "e1").p1).toEqual([60, 0]);
  expect(sketch.constraints.find((item) => typeof item.value === "string").value).toBe("=w");
  await settled(page);
  await page.screenshot({ path: info.outputPath("parameter-moves-sketch.png") });
});

test("Offset plane: type the offset, pick XY, sketch, and the part sits on that plane", async ({ page, request }, info) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  await page.locator("#tool-offset-plane").click();
  await expect(page.locator("#sketch-plane-offset")).toBeFocused();
  await page.keyboard.type("12");
  await page.screenshot({ path: info.outputPath("offset-plane-panel.png") });
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);
  await page.locator('#sketch-draw-panel [data-tool="rectangle"]').click();
  const click = async (uv) => {
    const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    await page.mouse.click(point.x, point.y);
  };
  await click([-10, -5]);
  await click([10, 5]);
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(4);
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active && window.__tcad.timeline.length === 1);
  await settled(page);
  expect((await rpc(request, "get_document")).features[0].plane).toEqual({ name: "XY", offset: 12 });

  // Extruding 5 from a plane 12 up gives a slab from z = 12 to 17.
  const point = await page.evaluate(() => window.__tcad.regionPoint("f1", 0));
  await page.mouse.click(point.x, point.y);
  await page.locator("#tool-extrude").click();
  await page.keyboard.type("5");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !window.__tcad.extruding);
  await settled(page);
  const box = (await rpc(request, "measure")).bounding_box;
  expect([box.min[2], box.max[2]]).toEqual([12, 17]);
});

test("Offset plane on a picked face: the sketch rides the offset above the face", async ({ page, request }) => {
  const { faces } = await rpc(request, "describe_geometry");
  const top = faces.findIndex((face) => face.normal && face.normal[2] === 1);
  const point = await page.evaluate((face) => window.__tcad.screenPointOf({ face }), top);
  await page.mouse.click(point.x, point.y);
  await expect.poll(() => page.evaluate(() => window.__tcad.selection.face)).toBe(top);
  await page.locator("#tool-offset-plane").click();
  await expect(page.locator("#sketch-plane-face")).toBeVisible();
  await page.keyboard.type("3");
  await page.locator("#sketch-plane-face").click();
  await page.waitForFunction(() => window.__tcad.sketch.active);
  await cameraStill(page);
  const plane = await rpc(request, "resolve_plane", { plane: { feature: "f2", face: { select: [{ op: "faces" }, { op: "nearest_to", point: [0, 0, 10], normal: [0, 0, 1] }] }, offset: 3 } });
  expect(plane.plane.origin[2]).toBeCloseTo(13, 6);
  // Draw and finish: the stored plane carries the offset beside the face selector.
  await page.locator('#sketch-draw-panel [data-tool="rectangle"]').click();
  for (const uv of [[-5, -5], [5, 5]]) {
    const at = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    await page.mouse.click(at.x, at.y);
  }
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(4);
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active && window.__tcad.timeline.length === 3);
  const stored = (await rpc(request, "get_document")).features[2].plane;
  expect(stored.offset).toBe(3);
  expect(stored.feature).toBe("f2");
  const resolved = (await page.evaluate(() => window.__tcad.timeline)).every((entry) => !entry.failed);
  expect(resolved).toBe(true);
});

test("the Fillet tool rounds a corner of two lines with an arc of the radius typed", async ({ page, request }, info) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line");
  await cameraStill(page);
  const click = async (uv) => {
    const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    await page.mouse.click(point.x, point.y);
  };
  // A square corner at the origin: up the v axis to it, then out along u.
  await click([0, 30]);
  await click([0, 0]);
  await click([40, 0]);
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(2);

  await page.locator('#sketch-draw-panel [data-tool="fillet"]').click();
  await click([0, 0]);
  await expect(page.locator("#sketch-dimension")).toBeVisible();
  // Offered a quarter of the shorter line, 30 / 4 = 7.5.
  await expect(page.locator("#sketch-dimension")).toHaveValue("7.50");
  await page.keyboard.type("5");
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-dimension")).toBeHidden();
  await page.screenshot({ path: info.outputPath("fillet.png") });

  const sketch = await page.evaluate(() => window.__tcad.sketch);
  const arc = sketch.entities.find((entity) => entity.kind === "arc");
  // A quarter circle in a square corner: centre 5 in from both lines, and the lines stop 5 short of the corner.
  expect(arc.r).toBeCloseTo(5, 6);
  expect(arc.c[0]).toBeCloseTo(5, 4);
  expect(arc.c[1]).toBeCloseTo(5, 4);
  expect(sketch.entities.find((entity) => entity.id === "e1").p1[1]).toBeCloseTo(5, 4);
  expect(sketch.entities.find((entity) => entity.id === "e2").p0[0]).toBeCloseTo(5, 4);
  expect(sketch.constraints.filter((constraint) => constraint.kind === "tangent")).toHaveLength(2);

  // Finish saves it to the document, arc and all.
  await page.locator("#sketch-finish").click();
  await page.waitForFunction(() => !window.__tcad.sketch.active && window.__tcad.timeline.length === 1);
  const saved = (await rpc(request, "get_document")).features[0].entities;
  expect(saved.filter((entity) => entity.kind === "arc")).toHaveLength(1);
});

test("the Fillet tool says so when the radius does not fit, and changes nothing", async ({ page, request }) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line");
  await cameraStill(page);
  const click = async (uv) => {
    const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    await page.mouse.click(point.x, point.y);
  };
  await click([0, 10]);
  await click([0, 0]);
  await click([40, 0]);
  await page.keyboard.press("Escape");
  await page.locator('#sketch-draw-panel [data-tool="fillet"]').click();
  await click([0, 0]);
  await page.keyboard.type("50");
  await page.keyboard.press("Enter");
  await expect(page.locator("#sketch-status")).toContainText("too big");
  expect((await page.evaluate(() => window.__tcad.sketch)).entities).toHaveLength(2);
  // A click on a point that is not a corner says what to click.
  await page.keyboard.press("Escape");
  await click([40, 0]);
  await expect(page.locator("#sketch-status")).toContainText("exactly two lines");
});

test("Mirror copies the selected shape across the line picked last, and the copy follows the original", async ({ page, request }, info) => {
  await resetDocument(request, { features: [], rollback_index: 0 });
  await page.waitForFunction(() => window.__tcad.mesh === null);
  await page.locator("#tool-sketch").click();
  await page.locator('#sketch-plane-panel button[data-plane="XY"]').click();
  await page.waitForFunction(() => window.__tcad.sketch.active && window.__tcad.sketch.tool === "line");
  await cameraStill(page);
  const click = async (uv, shift) => {
    const point = await page.evaluate((target) => window.__tcad.sketchPoint(target), uv);
    if (shift) await page.keyboard.down("Shift");
    await page.mouse.click(point.x, point.y);
    if (shift) await page.keyboard.up("Shift");
  };
  // The mirror line, upright at u = 0, and a slanted line to its right.
  await click([0, -20]);
  await click([0, 20]);
  await page.keyboard.press("Escape");
  await click([10, 0]);
  await click([25, 10]);
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(2);

  await page.locator('#sketch-draw-panel [data-tool="select"]').click();
  await expect(page.locator("#sketch-mirror")).toBeDisabled();
  await click([17.5, 5]);
  await click([0, 12], true);
  await expect(page.locator("#sketch-mirror")).toBeEnabled();
  await page.locator("#sketch-mirror").click();
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).entities.length).toBe(3);
  await page.screenshot({ path: info.outputPath("mirror.png") });

  const sketch = await page.evaluate(() => window.__tcad.sketch);
  const [line, original, copy] = ["e1", "e2", "e3"].map((id) => sketch.entities.find((entity) => entity.id === id));
  // Across u = 0: (10, 0) and (25, 10) land at (-10, 0) and (-25, 10).
  const mirrorU = line.p0[0];
  expect(copy.p0[0]).toBeCloseTo(2 * mirrorU - original.p0[0], 4);
  expect(copy.p0[0]).toBeCloseTo(-10, 3);
  expect(copy.p1[0]).toBeCloseTo(-25, 3);
  expect(copy.p1[1]).toBeCloseTo(10, 3);
  expect(sketch.constraints.filter((constraint) => constraint.kind === "symmetric")).toHaveLength(2);

  // Hold the mirror line, then drag the original's end up: the copy's end rises with it.
  await page.locator('#sketch-draw-panel [data-tool="select"]').click();
  await page.keyboard.press("Escape");
  await click([0, 12]);
  await page.locator('#sketch-draw-panel [data-constraint="fix"]').click();
  await expect.poll(async () => (await page.evaluate(() => window.__tcad.sketch)).constraints.some((constraint) => constraint.kind === "fix")).toBe(true);
  const grab = await page.evaluate(() => window.__tcad.sketchPoint([25, 10]));
  const drop = await page.evaluate(() => window.__tcad.sketchPoint([25, 16]));
  await page.mouse.move(grab.x, grab.y);
  await page.mouse.down();
  await page.mouse.move(drop.x, drop.y, { steps: 6 });
  await page.mouse.up();
  const dragged = await page.evaluate(() => window.__tcad.sketch);
  const movedOriginal = dragged.entities.find((entity) => entity.id === "e2");
  const movedCopy = dragged.entities.find((entity) => entity.id === "e3");
  expect(movedOriginal.p1[1]).toBeGreaterThan(14);
  expect(movedCopy.p1[1]).toBeCloseTo(movedOriginal.p1[1], 4);
  expect(movedCopy.p1[0]).toBeCloseTo(-movedOriginal.p1[0], 3);
});
